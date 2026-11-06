from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .errors import BridgeError

PENDING = ("reserved", "prepared", "sending", "waiting", "uncertain")


class StateLock:
    """One live worker process per journal, including Windows native Python."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.file = (directory / "bridge.lockfile").open("a+b")
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise BridgeError(
                409, "state_in_use", "Another bridge process is using this state directory."
            ) from None

    def close(self):
        if self.file.closed:
            return
        self.file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()


@dataclass(frozen=True)
class Job:
    job_id: str
    request_key: str
    text_hash: str
    state: str
    created_at: float
    updated_at: float
    baseline: int | None
    response: str | None
    response_seq: int | None
    error_code: str | None


class Journal:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.RLock()
        self.db = sqlite3.connect(directory / "journal.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
                text_hash TEXT NOT NULL, state TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                baseline INTEGER, response TEXT, response_seq INTEGER, error_code TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO settings VALUES ('node_id', ?)",
                ("airi-" + uuid.uuid4().hex[:16],),
            )

    def node_id(self) -> str:
        with self._mutex:
            return self.db.execute("SELECT value FROM settings WHERE name='node_id'").fetchone()[0]

    def recover_interrupted(self):
        with self._mutex, self.db:
            self.db.execute(
                "UPDATE jobs SET state='failed', error_code='interrupted_before_send' "
                "WHERE state IN ('reserved','prepared')"
            )
            self.db.execute(
                "UPDATE jobs SET state='uncertain', error_code='interrupted_after_send' "
                "WHERE state IN ('sending','waiting')"
            )

    def get(self, job_id: str) -> Job:
        with self._mutex:
            row = self.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise BridgeError(404, "job_not_found", "Job not found.")
            return Job(**dict(row))

    def reserve(self, key: str, text_hash: str, ttl: float) -> tuple[Job, bool]:
        with self._mutex, self.db:
            row = self.db.execute("SELECT * FROM jobs WHERE request_key=?", (key,)).fetchone()
            if row is not None:
                job = Job(**dict(row))
                if job.state == "completed" and time.time() - job.updated_at <= ttl:
                    return job, False
                if job.state in PENDING:
                    return job, False
                if job.state == "cleared":
                    raise BridgeError(
                        409,
                        "job_cleared",
                        "This request was explicitly cleared; send a new turn.",
                        job_id=job.job_id,
                    )
                self.db.execute("DELETE FROM jobs WHERE job_id=?", (job.job_id,))
            blocked = self.db.execute(
                "SELECT job_id FROM jobs WHERE state IN ('reserved','prepared','sending',"
                "'waiting','uncertain') LIMIT 1"
            ).fetchone()
            if blocked is not None:
                raise BridgeError(
                    409,
                    "muse_busy",
                    "Another turn is pending. Resolve it before sending a new one.",
                    job_id=blocked[0],
                )
            now = time.time()
            job_id = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO jobs (job_id,request_key,text_hash,state,created_at,updated_at) "
                "VALUES (?,?,?,'reserved',?,?)",
                (job_id, key, text_hash, now, now),
            )
            return self.get(job_id), True

    def update(self, job_id: str, state: str, **values):
        allowed = {"baseline", "response", "response_seq", "error_code"}
        if not values.keys() <= allowed:
            raise ValueError("Invalid journal fields")
        values = {"state": state, "updated_at": time.time(), **values}
        fields = ",".join(f"{key}=?" for key in values)
        with self._mutex, self.db:
            self.db.execute(f"UPDATE jobs SET {fields} WHERE job_id=?", (*values.values(), job_id))

    def prune(self, ttl: float):
        with self._mutex, self.db:
            self.db.execute(
                "DELETE FROM jobs WHERE state IN ('completed','failed','cleared') AND updated_at<?",
                (time.time() - ttl,),
            )

    def close(self):
        with self._mutex:
            self.db.close()


def pending_summary(directory: Path) -> list[dict]:
    path = directory / "journal.sqlite3"
    if not path.is_file():
        return []
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        db.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in db.execute(
                "SELECT job_id,state,created_at,updated_at,baseline,error_code FROM jobs "
                "WHERE state IN ('reserved','prepared','sending','waiting','uncertain')"
            )
        ]
    finally:
        db.close()


def print_pending(directory: Path):
    print(json.dumps({"pending": pending_summary(directory)}, indent=2))
