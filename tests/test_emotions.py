import pytest

from muse_airi_bridge.emotions import classify, decorate


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("よかった😊", "happy"),
        ("悲しい😢", "sad"),
        ("怒った😡", "angry"),
        ("びっくり😮", "surprised"),
        ("説明です", "neutral"),
        ("😊😢", "neutral"),
        ("`😊`", "neutral"),
        ("```python\nprint('😊')\n```", "neutral"),
        ("引用「😊」", "neutral"),
        ("> 😊", "neutral"),
    ],
)
def test_conservative_emotion_cues(text, expected):
    assert classify(text) == expected


def test_emotion_marker_never_changes_the_original_reply():
    raw = "  原文\n😊\n"
    result = decorate(raw)
    assert result.split("|>", 1)[1] == raw
    assert result.count("<|ACT ") == 1
    assert decorate(raw, False) == raw
