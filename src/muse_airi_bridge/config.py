from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default="", repr=False)
    config_dir: Path = field(default_factory=lambda: Path.home() / ".config" / "muse-cli")
    state_dir: Path = field(
        default_factory=lambda: Path.home() / ".local" / "state" / "muse-airi-bridge"
    )
    host: str = "127.0.0.1"
    port: int = 8000
    reply_timeout: float = 300
    poll_interval: float = 1
    cache_ttl: float = 600
    emotions: bool = True
    max_body_bytes: int = 2 * 1024 * 1024

    def __post_init__(self):
        if any(
            not math.isfinite(value) or value <= 0
            for value in (self.reply_timeout, self.poll_interval, self.cache_ttl)
        ):
            raise ValueError("Timeout, poll interval and cache TTL must be positive.")
        if not 1 <= self.port <= 65535 or self.max_body_bytes <= 0:
            raise ValueError("Invalid port or request size limit.")

    @classmethod
    def from_env(cls) -> Settings:
        defaults = cls()
        boolean = os.environ.get("MUSE_BRIDGE_EMOTIONS", "true").lower()
        if boolean not in {"true", "false", "1", "0"}:
            raise ValueError("MUSE_BRIDGE_EMOTIONS must be true or false.")
        return cls(
            api_key=os.environ.get("MUSE_BRIDGE_API_KEY", ""),
            config_dir=Path(os.environ.get("MUSE_BRIDGE_CONFIG_DIR", defaults.config_dir))
            .expanduser()
            .resolve(),
            state_dir=Path(os.environ.get("MUSE_BRIDGE_STATE_DIR", defaults.state_dir))
            .expanduser()
            .resolve(),
            host=os.environ.get("MUSE_BRIDGE_HOST", defaults.host),
            port=int(os.environ.get("MUSE_BRIDGE_PORT", defaults.port)),
            reply_timeout=float(os.environ.get("MUSE_BRIDGE_REPLY_TIMEOUT", 300)),
            poll_interval=float(os.environ.get("MUSE_BRIDGE_POLL_INTERVAL", 1)),
            emotions=boolean in {"true", "1"},
        )
