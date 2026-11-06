"""All muse-cli internals are confined to this adapter. No upstream config writes."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from importlib.metadata import version
from typing import Protocol

from .config import Settings
from .errors import BridgeError


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def payload(event: dict) -> dict:
    value = event.get("payload")
    return value if isinstance(value, dict) else {}


def event_text(event: dict) -> str:
    p = payload(event)
    value = p.get("display_text") or p.get("content") or ""
    return value if isinstance(value, str) else ""


def eligible_reply(event: dict, baseline: int) -> bool:
    p = payload(event)
    return (
        event.get("event_name") == "message.assistant"
        and event["seq"] > baseline
        and p.get("status") == "completed"
        and not (event.get("reply_to_message_id") or p.get("reply_to_message_id"))
        and bool(event_text(event))
    )


class Session(Protocol):
    def history(self) -> list[dict]: ...
    def send(self, text: str) -> None: ...


class MuseSession:
    def __init__(self, gateway, node_id: str):
        self.gateway = gateway
        self.node_id = node_id

    def history(self) -> list[dict]:
        data = self.gateway.call_json("chat.history", body={"limit": 100}, timeout=30)
        if not isinstance(data, dict) or not isinstance(data.get("chat_events"), list):
            raise BridgeError(502, "muse_protocol_error", "Unexpected Muse history format.")
        events = data["chat_events"]
        if any(
            not isinstance(e, dict) or type(e.get("seq")) is not int or e["seq"] < 0 for e in events
        ):
            raise BridgeError(502, "muse_protocol_error", "Unexpected Muse event sequence.")
        return sorted(events, key=lambda e: e["seq"])

    def send(self, text: str) -> None:
        # Unlike _open alone, request also consumes the send echo and checks HTTP status.
        # muse-cli PROTOCOL.md documents that this stream ends after that echo.
        self.gateway.request(
            "chat.stream",
            body={
                "items": [{"type": "text", "text": text}],
                "node_id": self.node_id,
                "capabilities": {},
            },
            timeout=30,
        )


class MuseAdapter:
    def __init__(self, settings: Settings, node_id: str):
        self.settings = settings
        self.node_id = node_id

    @contextmanager
    def open(self):
        gateway = None
        try:
            if version("muse-cli") != "0.3.10":
                raise BridgeError(503, "muse_version_mismatch", "muse-cli 0.3.10 is required.")
            from muse_cli.gateway import Gateway, load_cookies

            config_path = self.settings.config_dir / "config.json"
            cfg = (
                json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
            )
            if not isinstance(cfg, dict):
                raise ValueError("Invalid config")
            cookie_path = self.settings.config_dir / "cookies.txt"
            if cfg.get("cookies_file"):
                from pathlib import Path

                cookie_path = Path(cfg["cookies_file"]).expanduser()
                if not cookie_path.is_absolute():
                    cookie_path = self.settings.config_dir / cookie_path
            cookies = load_cookies(str(cookie_path))
            if not cookies.strip():
                raise BridgeError(503, "muse_auth_required", "Refresh muse-cli authentication.")
            gateway = Gateway(cookies, vm_id=cfg.get("vm_id"))
            yield MuseSession(gateway, cfg.get("node_id") or self.node_id)
        except BridgeError:
            raise
        except Exception as error:
            # Some upstream exceptions include URLs, auth tokens or gateway response text.
            # Deliberately retain none of those in our public errors or exception chains.
            name = type(error).__name__
            if name in {"AuthError", "FileNotFoundError"}:
                code, message = "muse_auth_required", "Refresh muse-cli authentication."
            elif name in {"JSONDecodeError", "TypeError", "ValueError"}:
                code, message = "muse_config_error", "Check the muse-cli config and cookie paths."
            else:
                code, message = "muse_unavailable", "Could not complete the Muse connection."
            raise BridgeError(503, code, message) from None
        finally:
            if gateway is not None:
                gateway.close()
