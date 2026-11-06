"""Translate Muse's dedicated emotion footer to AIRI ACT; never infer from emoji."""

from __future__ import annotations

import json

from .protocol import INVISIBLE_PROTOCOL, parse_reply


def decorate(text: str, enabled: bool = True, *, protocol: str = INVISIBLE_PROTOCOL) -> str:
    reply = parse_reply(text, protocol)
    if not enabled:
        return reply.text
    emotion = reply.emotion
    payload = {"emotion": {"name": emotion, "intensity": 1 if emotion == "neutral" else 0.6}}
    marker = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"<|ACT {marker}|>{reply.text}"
