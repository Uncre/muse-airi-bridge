"""Convert Muse's invisible emotion footer into an AIRI <|ACT ...|> prefix.

Emotion is determined strictly from the dedicated protocol footer and is never
inferred from visible text or emoji.
"""

from __future__ import annotations

import json

from .protocol import INVISIBLE_PROTOCOL, parse_reply


def decorate(text: str, enabled: bool = True, *, protocol: str = INVISIBLE_PROTOCOL) -> str:
    """Strip any recognized emotion footer and optionally prepend an AIRI ACT marker."""
    reply = parse_reply(text, protocol)
    if not enabled:
        return reply.text
    emotion = reply.emotion
    payload = {"emotion": {"name": emotion, "intensity": 1 if emotion == "neutral" else 0.6}}
    marker = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"<|ACT {marker}|>{reply.text}"
