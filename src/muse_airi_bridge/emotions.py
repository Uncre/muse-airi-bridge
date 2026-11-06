"""Conservative, deterministic expression cues. No models or remote calls."""

from __future__ import annotations

import json
import re

EMOJI = {
    "happy": frozenset("😊😄😃😀🥰😆😁"),
    "sad": frozenset("😢😭😞😔☹"),
    "angry": frozenset("😠😡🤬"),
    "surprised": frozenset("😮😲🤯"),
}


def classify(text: str) -> str:
    # Quoted examples and code should not drive the avatar expression.
    visible = re.sub(r"```[\s\S]*?```|`[^`\n]*`|「[^」]*」|\"[^\"\n]*\"", "", text)
    visible = "\n".join(line for line in visible.splitlines() if not line.lstrip().startswith(">"))
    matches = [name for name, characters in EMOJI.items() if any(c in visible for c in characters)]
    return matches[0] if len(matches) == 1 else "neutral"


def decorate(text: str, enabled: bool = True) -> str:
    if not enabled:
        return text
    emotion = classify(text)
    payload = {"emotion": {"name": emotion, "intensity": 1 if emotion == "neutral" else 0.6}}
    marker = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"<|ACT {marker}|>{text}"
