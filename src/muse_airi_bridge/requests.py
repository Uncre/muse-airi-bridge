"""Recognize AIRI v0.14.1's rendered requests; never forward a whole prompt.

Source fixtures are based on AIRI commit 349ecfeda2d0e1508a6b2c2a25740757c9ed51c6.
This is intentionally a narrow Chat Completions subset, not a stateless LLM.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .errors import BridgeError

MODEL = "muse-airi"
CHAT_FORMAT_PREFIX = "- For any programming code block, always specify the programming language"
DATE_PREFIX = re.compile(r"\A\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] ")
REPLY_PREFIX = re.compile(r"\A\[(?:Replying to: [^\n]*|Replying to message: [^\n]*)\]\n")
APPENDED_SECTIONS = ("\n[Context]\n", '\n\n[The owner used the skill "')
AUXILIARY_SYSTEM_MARKERS = (
    "You are the Cinematic Director for AIRI.",
    "This is AIRI system, the life pod hosting your consciousness.",
    " in its own space for your conversation with the owner.",
)


class Message(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: Literal["system", "developer", "user", "assistant", "tool", "function"]
    content: str | list[dict] | None = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str
    messages: list[Message] = Field(min_length=1)
    stream: bool = False
    stream_options: dict | None = None
    tools: list[dict] | None = None
    tool_choice: str | dict | None = None
    response_format: dict | None = None
    n: int = 1
    stop: str | list[str] | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    max_completion_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = None
    top_p: float | None = None


@dataclass(frozen=True)
class RoutedInput:
    kind: Literal["probe", "conversation"]
    text: str = ""
    fingerprint: str = ""


def unsupported(message: str) -> BridgeError:
    return BridgeError(400, "unsupported_request_kind", message)


def text_content(message: Message) -> str:
    if isinstance(message.content, str):
        return message.content
    if isinstance(message.content, list) and all(
        part.get("type") == "text" and isinstance(part.get("text"), str) for part in message.content
    ):
        # AIRI's renderer concatenates text segments without a separator.
        return "".join(part["text"] for part in message.content)
    raise unsupported("Only text content is supported for the current input.")


def extract_original(rendered: str) -> str:
    match = DATE_PREFIX.match(rendered)
    if not match:
        raise unsupported("Unrecognized AIRI input envelope; expected the v0.14.1 datetime prefix.")
    try:
        datetime.strptime(match[1], "%Y-%m-%d %H:%M")
    except ValueError:
        raise unsupported("Invalid AIRI datetime prefix.") from None
    original = rendered[match.end() :]
    if original.startswith("[Notice from "):
        raise unsupported("AIRI notices are not owner messages and are not forwarded to Muse.")
    if original.startswith(("[Replying to:", "[Replying to message:")):
        reply = REPLY_PREFIX.match(original)
        if not reply:
            raise unsupported("Unrecognized AIRI reply reference.")
        original = original[reply.end() :]
    cuts = [original.find(marker) for marker in APPENDED_SECTIONS if marker in original]
    if cuts:
        original = original[: min(cuts)]
    if not original.strip():
        raise unsupported("The original user input is empty.")
    return original


def route(request: ChatRequest) -> RoutedInput:
    if request.model != MODEL:
        raise BridgeError(404, "model_not_found", f"Select model '{MODEL}'.")
    if request.n != 1 or request.stop not in (None, [], ""):
        raise unsupported("Only n=1 without stop sequences is supported.")
    if request.tool_choice not in (None, "auto", "none"):
        raise unsupported("AIRI function tools cannot be required or selected by this bridge.")
    if request.response_format and request.response_format != {"type": "text"}:
        raise unsupported("Structured output and auxiliary analysis are not supported.")
    if request.model_extra and any(
        request.model_extra.get(name)
        for name in ("functions", "function_call", "modalities", "audio")
    ):
        raise unsupported("Legacy function calling and non-text modalities are not supported.")
    if (
        len(request.messages) == 1
        and request.messages[0].role == "user"
        and request.messages[0].content == "ping"
        and not request.tools
        and not request.stream
        and (request.max_tokens == 16 or request.max_completion_tokens == 16)
    ):
        return RoutedInput("probe")
    if request.messages[-1].role != "user":
        raise unsupported("Only a new user turn is forwarded; continuations are not replayed.")
    system_texts = [
        text_content(message)
        for message in request.messages
        if message.role in {"system", "developer"}
    ]
    if any(marker in text for text in system_texts for marker in AUXILIARY_SYSTEM_MARKERS):
        raise unsupported("This AIRI background or auxiliary request is not forwarded to Muse.")
    if system_texts and not any(text.startswith(CHAT_FORMAT_PREFIX) for text in system_texts):
        raise unsupported("Unrecognized AIRI system envelope; no message was sent to Muse.")
    original = extract_original(text_content(request.messages[-1]))
    # Include the entire (unforwarded) transcript to distinguish repeated words in later turns.
    canonical = json.dumps(
        {"model": request.model, "messages": [m.model_dump(mode="json") for m in request.messages]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return RoutedInput("conversation", original, hashlib.sha256(canonical.encode()).hexdigest())
