"""Versioned, bit-exact wire metadata; unrelated invisible text is never normalized."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ZERO = "\u200b"
ONE = "\u200c"
LEGACY_PROTOCOL = "raw-v0"
INVISIBLE_PROTOCOL = "invisible-v1"
SUPPORTED_PROTOCOLS = frozenset({LEGACY_PROTOCOL, INVISIBLE_PROTOCOL})
EMOTION_CODES = {
    "N": "neutral",
    "H": "happy",
    "S": "sad",
    "A": "angry",
    "T": "think",
    "U": "surprised",
    "W": "awkward",
    "Q": "question",
    "C": "curious",
}


def encode_bytes(data: bytes) -> str:
    return "".join(ONE if byte & (1 << bit) else ZERO for byte in data for bit in range(7, -1, -1))


def decode_bytes(text: str) -> bytes:
    if len(text) % 8 or any(char not in {ZERO, ONE} for char in text):
        raise ValueError("Expected whole bytes encoded with U+200B and U+200C only.")
    return bytes(
        int("".join("1" if char == ONE else "0" for char in text[i : i + 8]), 2)
        for i in range(0, len(text), 8)
    )


INPUT_HEADER = encode_bytes(b"AIRI1")
FOOTER_PREFIX = encode_bytes(b"AE1:")
FOOTER_LENGTH = 40


def outbound_text(original: str, protocol: str = INVISIBLE_PROTOCOL) -> str:
    if protocol == INVISIBLE_PROTOCOL:
        return INPUT_HEADER + original
    if protocol == LEGACY_PROTOCOL:
        return original
    raise ValueError("Unsupported wire protocol.")


def emotion_footer(emotion: str) -> str:
    code = next((code for code, name in EMOTION_CODES.items() if name == emotion), None)
    if code is None:
        raise ValueError("Unsupported emotion.")
    return encode_bytes(b"AE1:" + code.encode("ascii"))


@dataclass(frozen=True)
class DecodedReply:
    text: str
    emotion: str
    status: Literal["valid", "missing", "invalid", "duplicate", "legacy"]


def parse_reply(text: str, protocol: str = INVISIBLE_PROTOCOL) -> DecodedReply:
    if protocol == LEGACY_PROTOCOL:
        # Old cached replies never opted into this protocol, even if they quote its footer.
        return DecodedReply(text, "neutral", "legacy")
    if protocol != INVISIBLE_PROTOCOL:
        raise ValueError("Unsupported wire protocol.")
    body = text
    candidates: list[str | None] = []
    while len(body) >= FOOTER_LENGTH and body[-FOOTER_LENGTH:-8] == FOOTER_PREFIX:
        candidate, body = body[-FOOTER_LENGTH:], body[:-FOOTER_LENGTH]
        try:
            code = decode_bytes(candidate)[-1:].decode("ascii")
        except (ValueError, UnicodeDecodeError):
            code = ""
        candidates.append(EMOTION_CODES.get(code))
    if not candidates:
        return DecodedReply(text, "neutral", "missing")
    if len(candidates) > 1:
        return DecodedReply(body, "neutral", "duplicate")
    if candidates[0] is None:
        return DecodedReply(body, "neutral", "invalid")
    return DecodedReply(body, candidates[0], "valid")
