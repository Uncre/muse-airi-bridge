import json
from pathlib import Path

import pytest

from muse_airi_bridge.errors import BridgeError
from muse_airi_bridge.muse import MuseAdapter

from .fakes import event, settings


def test_adapter_sends_only_original_and_never_rewrites_config(tmp_path, monkeypatch):
    from muse_cli import gateway

    config = settings(tmp_path)
    config.config_dir.mkdir()
    cookie_path = config.config_dir / "cookies.txt"
    config_path = config.config_dir / "config.json"
    cookie_path.write_text("hatch_sess=TEST_ONLY_NOT_A_REAL_COOKIE", encoding="utf-8")
    config_path.write_text(
        json.dumps({"cookies_file": "cookies.txt", "node_id": "existing"}), encoding="utf-8"
    )
    before = {p: p.read_bytes() for p in config.config_dir.iterdir()}
    calls = []

    class Gateway:
        def __init__(self, cookies, **kwargs):
            assert cookies == "hatch_sess=TEST_ONLY_NOT_A_REAL_COOKIE"

        def call_json(self, method, **kwargs):
            calls.append((method, kwargs))
            return {"chat_events": [event(3), event(1)]}

        def request(self, method, **kwargs):
            calls.append((method, kwargs))
            return b"{}\n"

        def close(self):
            calls.append(("close", {}))

    monkeypatch.setattr(gateway, "Gateway", Gateway)
    with MuseAdapter(config, "fallback").open() as session:
        assert [e["seq"] for e in session.history()] == [1, 3]
        session.send(" 原文\n")
    assert calls[1][0] == "chat.stream"
    assert calls[1][1]["body"] == {
        "items": [{"type": "text", "text": " 原文\n"}],
        "node_id": "existing",
        "capabilities": {},
    }
    assert "session_id" not in calls[0][1]["body"]
    assert calls[-1][0] == "close"
    assert before == {p: p.read_bytes() for p in config.config_dir.iterdir()}


def test_missing_auth_does_not_create_cli_directory(tmp_path):
    config = settings(tmp_path)
    with pytest.raises(BridgeError) as error, MuseAdapter(config, "unused").open():
        pass
    assert error.value.code == "muse_auth_required"
    assert not config.config_dir.exists()


def test_upstream_token_urls_are_redacted(tmp_path, monkeypatch):
    from muse_cli import gateway

    config = settings(tmp_path)
    config.config_dir.mkdir()
    (config.config_dir / "cookies.txt").write_text("hatch_sess=TEST_COOKIE", encoding="utf-8")

    def fail(*args, **kwargs):
        raise RuntimeError("wss://example.invalid/?auth_token=SENSITIVE_SECRET")

    monkeypatch.setattr(gateway, "Gateway", fail)
    with pytest.raises(BridgeError) as error, MuseAdapter(config, "unused").open():
        pass
    assert "SENSITIVE" not in str(error.value)
    assert "auth_token" not in str(error.value.payload())
    assert error.value.__cause__ is None


@pytest.mark.parametrize("bad", [{}, {"chat_events": None}, {"chat_events": [{"seq": "1"}]}])
def test_malformed_history_is_not_used_as_a_baseline(bad):
    from muse_airi_bridge.muse import MuseSession

    class Gateway:
        def call_json(self, *args, **kwargs):
            return bad

    with pytest.raises(BridgeError):
        MuseSession(Gateway(), "unused").history()


def test_version_mismatch_fails_before_reading_auth(tmp_path, monkeypatch):
    monkeypatch.setattr("muse_airi_bridge.muse.version", lambda _: "99.0")
    config = settings(tmp_path)
    with pytest.raises(BridgeError) as error, MuseAdapter(config, "unused").open():
        pass
    assert error.value.code == "muse_version_mismatch"
    assert not Path(config.config_dir).exists()
