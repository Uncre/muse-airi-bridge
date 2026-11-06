from __future__ import annotations

import copy
import threading
from contextlib import contextmanager

from muse_airi_bridge.config import Settings
from muse_airi_bridge.errors import BridgeError
from muse_airi_bridge.protocol import emotion_footer
from muse_airi_bridge.requests import CHAT_FORMAT_PREFIX, MODEL

REPLY_WIRE = "回答です😊" + emotion_footer("happy")


def event(seq, name="message.assistant", text=REPLY_WIRE, **values):
    return {
        "seq": seq,
        "event_name": name,
        "payload": {
            "content": text,
            "display_text": text,
            "status": "completed" if name == "message.assistant" else None,
            **values,
        },
    }


def conversation(text="  原文\n次の行  ", **kwargs):
    return {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": CHAT_FORMAT_PREFIX + " supported on AIRI\nPersona"},
            {"role": "user", "content": "[2026-11-06 14:00] 前の発言"},
            {"role": "assistant", "content": "前の回答"},
            {
                "role": "user",
                "content": "[2026-11-06 14:01] "
                + text
                + "\n[Context]\n- system:airi-runtime-prompt: private formatting instructions",
            },
        ],
        **kwargs,
    }


class FakeAdapter:
    def __init__(self, *, auto_reply=True, failure=None, reply_text=REPLY_WIRE):
        self.events = [event(10, text="古い回答")]
        self.sent = []
        self.opens = 0
        self.closed = 0
        self.auto_reply = auto_reply
        self.release = threading.Event()
        self.posted = threading.Event()
        self.failure = failure
        self.reply_text = reply_text
        self.mutex = threading.RLock()
        self.receivers = []

    @contextmanager
    def open(self):
        if self.failure == "open":
            raise BridgeError(503, "muse_auth_required", "Refresh muse-cli authentication.")
        self.opens += 1
        adapter = self

        class Session:
            def __init__(self):
                self.owner = None

            def record(self):
                ident = threading.get_ident()
                if self.owner is None:
                    self.owner = ident
                assert self.owner == ident, "A connection was consumed by multiple threads"
                adapter.receivers.append(ident)

            def history(self):
                self.record()
                with adapter.mutex:
                    if adapter.failure == "baseline" and not adapter.sent:
                        raise BridgeError(503, "muse_unavailable", "History unavailable.")
                    if adapter.sent and (adapter.auto_reply or adapter.release.is_set()):
                        if adapter.events[-1]["event_name"] == "message.user":
                            adapter.events.append(
                                event(adapter.events[-1]["seq"] + 1, text=adapter.reply_text)
                            )
                    return copy.deepcopy(adapter.events)

            def send(self, text):
                self.record()
                with adapter.mutex:
                    adapter.sent.append(text)
                    adapter.events.append(
                        event(adapter.events[-1]["seq"] + 1, "message.user", text)
                    )
                    adapter.posted.set()
                    delay = getattr(adapter, "release_delay", None)
                    if delay is not None:
                        timer = threading.Timer(delay, adapter.release.set)
                        timer.daemon = True
                        timer.start()
                    if adapter.failure == "send":
                        raise RuntimeError("SENSITIVE_UPSTREAM_COOKIE_URL")

        try:
            yield Session()
        finally:
            self.closed += 1


def settings(tmp_path, **kwargs):
    return Settings(
        api_key="test-key-at-least-16-characters",
        state_dir=tmp_path / "state",
        config_dir=tmp_path / "muse-config",
        reply_timeout=0.3,
        poll_interval=0.005,
        **kwargs,
    )


def create_socket_app():
    """Factory for a separate, fake-only Uvicorn process in the socket contract test."""
    import os
    from pathlib import Path

    from muse_airi_bridge.api import create_app

    adapter = FakeAdapter(auto_reply=False)
    adapter.release_delay = 0.4
    config = Settings(
        api_key="test-key-at-least-16-characters",
        state_dir=Path(os.environ["BRIDGE_TEST_STATE_DIR"]),
        reply_timeout=5,
        poll_interval=0.01,
    )
    app = create_app(config, adapter=adapter)

    @app.get("/test-only-stats")
    def stats():
        return {"posts": len(adapter.sent), "open": adapter.opens, "closed": adapter.closed}

    return app
