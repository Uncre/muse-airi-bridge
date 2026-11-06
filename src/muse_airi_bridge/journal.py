"""SQLite state journal and single-process lock for safe turn deduplication."""

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
from .protocol import INVISIBLE_PROTOCOL, LEGACY_PROTOCOL, SUPPORTED_PROTOCOLS

PENDING = ("reserved", "prepared", "sending", "waiting", "uncertain")
SCHEMA_VERSION = 2


class StateLock:
    """Ensure only one worker process accesses the state directory at a time."""

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
    """Recorded state for a single conversation turn."""

    job_id: str
    request_key: str
    text_hash: str
    protocol: str
    wire_hash: str
    state: str
    created_at: float
    updated_at: float
    baseline: int | None
    response: str | None
    response_seq: int | None
    error_code: str | None


class Journal:
    """Persistent SQLite store tracking turn states, hashes, and cached replies."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.RLock()
        path = directory / "journal.sqlite3"
        existed = path.is_file()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        try:
            previous = self.db.execute("PRAGMA user_version").fetchone()[0]
            if previous > SCHEMA_VERSION:
                raise BridgeError(
                    503,
                    "journal_version_unsupported",
                    "The state journal was created by a newer version of this bridge.",
                )
            # Create a pre-migration backup before upgrading a 0.1.0 database to schema v2.
            backup_path = directory / "journal.pre-v2.sqlite3"
            if existed and previous < SCHEMA_VERSION and not backup_path.exists():
                backup = sqlite3.connect(backup_path)
                try:
                    self.db.backup(backup)
                finally:
                    backup.close()
            self.db.execute("PRAGMA journal_mode=WAL")
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute(
                    """CREATE TABLE IF NOT EXISTS jobs (
                        job_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
                        text_hash TEXT NOT NULL, state TEXT NOT NULL,
                        created_at REAL NOT NULL, updated_at REAL NOT NULL,
                        baseline INTEGER, response TEXT, response_seq INTEGER, error_code TEXT,
                        protocol TEXT NOT NULL DEFAULT 'raw-v0',
                        wire_hash TEXT NOT NULL DEFAULT ''
                    )"""
                )
                self.db.execute(
                    "CREATE TABLE IF NOT EXISTS settings "
                    "(name TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                columns = {row[1] for row in self.db.execute("PRAGMA table_info(jobs)")}
                if "protocol" not in columns:
                    self.db.execute(
                        "ALTER TABLE jobs ADD COLUMN protocol TEXT NOT NULL DEFAULT 'raw-v0'"
                    )
                if "wire_hash" not in columns:
                    self.db.execute("ALTER TABLE jobs ADD COLUMN wire_hash TEXT")
                self.db.execute(
                    "UPDATE jobs SET wire_hash=text_hash WHERE wire_hash IS NULL OR wire_hash=''"
                )
                if self.db.execute(
                    "SELECT 1 FROM jobs WHERE protocol NOT IN (?,?) LIMIT 1",
                    tuple(sorted(SUPPORTED_PROTOCOLS)),
                ).fetchone():
                    raise BridgeError(
                        503,
                        "journal_protocol_unsupported",
                        "The state journal contains an unsupported wire protocol.",
                    )
                # Retire failed legacy jobs into tombstones so they cannot be resent with a header.
                self.db.execute(
                    "UPDATE jobs SET state='cleared',response=NULL,"
                    "error_code='legacy_failed_retired' "
                    "WHERE protocol=? AND state='failed'",
                    (LEGACY_PROTOCOL,),
                )
                self.db.execute(
                    "UPDATE jobs SET response=NULL WHERE protocol=? AND state='cleared'",
                    (LEGACY_PROTOCOL,),
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO settings VALUES ('node_id', ?)",
                    ("airi-" + uuid.uuid4().hex[:16],),
                )
                self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        except BaseException:
            self.db.close()
            raise

    def node_id(self) -> str:
        with self._mutex:
            return self.db.execute("SELECT value FROM settings WHERE name='node_id'").fetchone()[0]

    def recover_interrupted(self):
        """Transition in-flight jobs left behind by a previous process shutdown."""
        with self._mutex, self.db:
            # All pending legacy (raw-v0) jobs become uncertain so they are never upgraded and
            # reposted under the new invisible-v1 protocol.
            self.db.execute(
                "UPDATE jobs SET state='uncertain',error_code='legacy_pending' "
                "WHERE protocol=? AND state IN ('reserved','prepared','sending','waiting')",
                (LEGACY_PROTOCOL,),
            )
            # New-protocol jobs interrupted before network send are safe to mark as failed.
            self.db.execute(
                "UPDATE jobs SET state='failed', error_code='interrupted_before_send' "
                "WHERE protocol=? AND state IN ('reserved','prepared')",
                (INVISIBLE_PROTOCOL,),
            )
            # Jobs interrupted during or after network send may have reached Muse; hold them.
            self.db.execute(
                "UPDATE jobs SET state='uncertain', error_code='interrupted_after_send' "
                "WHERE state IN ('sending','waiting')"
            )

    def get(self, job_id: str) -> Job:
        with self._mutex:
            row = self.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise BridgeError(404, "job_not_found", "Job ID was not found in the journal.")
            return Job(**dict(row))

    def reserve(
        self,
        key: str,
        text_hash: str,
        ttl: float,
        *,
        protocol: str = LEGACY_PROTOCOL,
        wire_hash: str | None = None,
    ) -> tuple[Job, bool]:
        """Reserve a turn slot, or return an existing active/cached job for the same request."""
        if protocol not in SUPPORTED_PROTOCOLS or (
            protocol == INVISIBLE_PROTOCOL and not wire_hash
        ):
            raise ValueError("An explicit wire_hash is required for invisible-v1 jobs.")
        self.prune(ttl)
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
                        "This request was cleared; send a new message in AIRI instead.",
                        job_id=job.job_id,
                    )
                if job.protocol == LEGACY_PROTOCOL:
                    raise BridgeError(
                        409,
                        "legacy_job_retired",
                        "Send a new message in AIRI instead of reusing a legacy 0.1.0 request.",
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
                    "Another turn is still pending. Resolve it before sending a new message.",
                    job_id=blocked[0],
                )
            now = time.time()
            job_id = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO jobs "
                "(job_id,request_key,text_hash,state,created_at,updated_at,protocol,wire_hash) "
                "VALUES (?,?,?,'reserved',?,?,?,?)",
                (job_id, key, text_hash, now, now, protocol, wire_hash or text_hash),
            )
            return self.get(job_id), True

    def update(self, job_id: str, state: str, **values):
        allowed = {"baseline", "response", "response_seq", "error_code"}
        if not values.keys() <= allowed:
            raise ValueError("Invalid journal update fields.")
        values = {"state": state, "updated_at": time.time(), **values}
        fields = ",".join(f"{key}=?" for key in values)
        with self._mutex, self.db:
            self.db.execute(f"UPDATE jobs SET {fields} WHERE job_id=?", (*values.values(), job_id))

    def prune(self, ttl: float):
        """Expire old completed/failed entries while preserving legacy tombstones."""
        with self._mutex, self.db:
            # Keep expired legacy request keys as cleared tombstones (without reply text)
            # so they are never reposted with an invisible header on a late retry.
            self.db.execute(
                "UPDATE jobs SET state='cleared',response=NULL,error_code='legacy_cache_expired' "
                "WHERE protocol=? AND state='completed' AND updated_at<?",
                (LEGACY_PROTOCOL, time.time() - ttl),
            )
            self.db.execute(
                "DELETE FROM jobs WHERE protocol=? "
                "AND state IN ('completed','failed','cleared') AND updated_at<?",
                (INVISIBLE_PROTOCOL, time.time() - ttl),
            )

    def close(self):
        with self._mutex:
            self.db.close()


def pending_summary(directory: Path) -> list[dict]:
    """Read pending job metadata in read-only mode without mutating the database."""
    path = directory / "journal.sqlite3"
    if not path.is_file():
        return []
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        db.row_factory = sqlite3.Row
        columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        protocol_column = "protocol" if "protocol" in columns else "'raw-v0' AS protocol"
        return [
            dict(row)
            for row in db.execute(
                f"SELECT job_id,state,created_at,updated_at,baseline,error_code,{protocol_column} "
                "FROM jobs "
                "WHERE state IN ('reserved','prepared','sending','waiting','uncertain')"
            )
        ]
    finally:
        db.close()


def print_pending(directory: Path):
    """Print pending job metadata as formatted JSON."""
    print(json.dumps({"pending": pending_summary(directory)}, indent=2, ensure_ascii=False))
