from muse_airi_bridge.cli import main

from .fakes import FakeAdapter


def test_doctor_works_without_api_key_and_does_not_create_state(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MUSE_BRIDGE_API_KEY", raising=False)
    monkeypatch.setenv("MUSE_BRIDGE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MUSE_BRIDGE_CONFIG_DIR", str(tmp_path / "config"))
    adapter = FakeAdapter()
    monkeypatch.setattr("muse_airi_bridge.cli.MuseAdapter", lambda *args: adapter)
    assert main(["doctor"]) == 0
    assert '"message_sent": false' in capsys.readouterr().out
    assert adapter.sent == []
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "config").exists()


def test_placeholder_key_cannot_start_server(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MUSE_BRIDGE_API_KEY", "replace-with-a-long-random-local-key")
    monkeypatch.setenv("MUSE_BRIDGE_STATE_DIR", str(tmp_path / "state"))
    assert main(["serve"]) == 1
    assert "bridge_configuration_error" in capsys.readouterr().out
    assert not (tmp_path / "state").exists()
