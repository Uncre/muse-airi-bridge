import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from muse_airi_bridge.api import create_app
from muse_airi_bridge.errors import BridgeError
from muse_airi_bridge.journal import Journal, pending_summary
from muse_airi_bridge.muse import text_hash
from muse_airi_bridge.protocol import INVISIBLE_PROTOCOL, LEGACY_PROTOCOL, emotion_footer
from muse_airi_bridge.requests import ChatRequest, route
from muse_airi_bridge.worker import BridgeWorker

from .fakes import FakeAdapter, conversation, event, settings


def old_database(config, state="completed", *, updated_at=None, response=None):
    config.state_dir.mkdir(parents=True)
    db = sqlite3.connect(config.state_dir / "journal.sqlite3")
    db.executescript("""
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
            text_hash TEXT NOT NULL, state TEXT NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            baseline INTEGER, response TEXT, response_seq INTEGER, error_code TEXT
        );
        CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO settings VALUES ('node_id','old-existing-node');
    """)
    routed = route(ChatRequest.model_validate(conversation("legacy original")))
    now = time.time()
    with db:
        db.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "old-job",
                routed.fingerprint,
                text_hash(routed.text),
                state,
                now,
                updated_at if updated_at is not None else now,
                10,
                response,
                12,
                None,
            ),
        )
    db.close()
    return routed


def test_legacy_cache_replays_without_decoding_or_posting_and_backup_is_original(tmp_path):
    config = settings(tmp_path)
    raw = "old cached quote 😊" + emotion_footer("angry")
    old_database(config, response=raw)
    adapter = FakeAdapter()
    with TestClient(create_app(config, adapter=adapter)) as client:
        result = client.post(
            "/v1/chat/completions",
            json=conversation("legacy original"),
            headers={"Authorization": "Bearer " + config.api_key},
        )
        assert result.status_code == 200
        text = result.json()["choices"][0]["message"]["content"]
        assert '"name":"neutral"' in text
        assert text.split("|>", 1)[1] == raw
        assert client.app.state.worker.journal.get("old-job").protocol == LEGACY_PROTOCOL
    assert adapter.opens == 0
    assert not adapter.sent
    backup = sqlite3.connect(config.state_dir / "journal.pre-v2.sqlite3")
    try:
        assert "protocol" not in {row[1] for row in backup.execute("PRAGMA table_info(jobs)")}
        assert backup.execute("SELECT response FROM jobs").fetchone()[0] == raw
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        backup.close()


@pytest.mark.parametrize("state", ["reserved", "prepared", "sending", "waiting", "uncertain"])
def test_every_legacy_pending_state_remains_blocked_instead_of_being_upgraded(tmp_path, state):
    config = settings(tmp_path)
    routed = old_database(config, state)
    # The inspection command must also work BEFORE migration without touching the old schema.
    assert pending_summary(config.state_dir)[0]["protocol"] == LEGACY_PROTOCOL
    adapter = FakeAdapter()
    worker = BridgeWorker(config, adapter)
    try:
        job = worker.journal.get("old-job")
        assert job.state == "uncertain"
        assert job.wire_hash == job.text_hash == text_hash("legacy original")
        assert worker.journal.node_id() == "old-existing-node"
        with pytest.raises(BridgeError) as error:
            worker.submit(routed)
        assert error.value.code == "muse_turn_uncertain"
        assert not adapter.sent
        assert adapter.opens == 0
    finally:
        worker.close()


def test_legacy_pending_can_reconcile_with_old_display_text_hash_without_posting(tmp_path):
    config = settings(tmp_path)
    routed = old_database(config, "waiting")
    adapter = FakeAdapter()
    adapter.events += [event(11, "message.user", "legacy original"), event(12, text="old reply")]
    # Preserve the old field precedence only for a versioned legacy job.
    adapter.events[-2]["payload"]["content"] = "different historical representation"
    worker = BridgeWorker(config, adapter)
    try:
        job = worker.reconcile("old-job")
        assert (job.protocol, job.response) == (LEGACY_PROTOCOL, "old reply")
        assert worker.submit(routed).result().job_id == "old-job"
        assert not adapter.sent
    finally:
        worker.close()


@pytest.mark.parametrize("state", ["completed", "failed", "cleared"])
def test_expired_or_retired_legacy_identity_cannot_become_a_new_header_post(tmp_path, state):
    config = settings(tmp_path)
    routed = old_database(config, state, updated_at=time.time() - 1000, response="old reply")
    adapter = FakeAdapter()
    worker = BridgeWorker(config, adapter)
    try:
        with pytest.raises(BridgeError):
            worker.submit(routed)
        assert worker.journal.get("old-job").state == "cleared"
        assert worker.journal.get("old-job").response is None
        worker.journal.prune(1)
        assert worker.journal.get("old-job").protocol == LEGACY_PROTOCOL
        assert not adapter.sent
        # A genuinely new turn can still proceed with the new protocol.
        new = route(ChatRequest.model_validate(conversation("new original")))
        assert worker.submit(new).result(2).protocol == INVISIBLE_PROTOCOL
    finally:
        worker.close()


def test_migration_is_repeatable_and_preserves_new_protocol_and_response(tmp_path):
    config = settings(tmp_path)
    adapter = FakeAdapter()
    worker = BridgeWorker(config, adapter)
    routed = route(ChatRequest.model_validate(conversation("new original")))
    completed = worker.submit(routed).result(2)
    worker.close()
    restarted = BridgeWorker(config, adapter)
    try:
        assert restarted.submit(routed).result() == completed
        assert len(adapter.sent) == 1
        assert not (config.state_dir / "journal.pre-v2.sqlite3").exists()
    finally:
        restarted.close()


def test_newer_database_version_fails_closed(tmp_path):
    config = settings(tmp_path)
    old_database(config)
    db = sqlite3.connect(config.state_dir / "journal.sqlite3")
    db.execute("PRAGMA user_version=999")
    db.close()
    with pytest.raises(BridgeError) as error:
        Journal(config.state_dir)
    assert error.value.code == "journal_version_unsupported"
    assert not (config.state_dir / "journal.pre-v2.sqlite3").exists()
