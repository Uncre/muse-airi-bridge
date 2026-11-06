import json

import pytest

from muse_airi_bridge.emotions import decorate
from muse_airi_bridge.protocol import EMOTION_CODES, LEGACY_PROTOCOL, emotion_footer


@pytest.mark.parametrize("emotion", EMOTION_CODES.values())
def test_footer_controls_act_regardless_of_body_emoji(emotion):
    raw = " 本文😡😊\n" + emotion_footer(emotion)
    marker, body = decorate(raw).split("|>", 1)
    cue = json.loads(marker[len("<|ACT ") :])["emotion"]
    assert cue == {"name": emotion, "intensity": 1 if emotion == "neutral" else 0.6}
    assert body == " 本文😡😊\n"


def test_missing_footer_is_neutral_without_emoji_inference():
    assert '"name":"neutral"' in decorate("😊😄😃")


def test_disabled_act_still_removes_only_the_dedicated_footer():
    raw = "  原文\n😊\n"
    assert decorate(raw + emotion_footer("happy"), False) == raw
    assert decorate(raw, False) == raw


def test_legacy_reply_is_never_decoded_as_the_new_protocol():
    raw = "old cached quote 😊" + emotion_footer("angry")
    result = decorate(raw, protocol=LEGACY_PROTOCOL)
    assert '"name":"neutral"' in result
    assert result.split("|>", 1)[1] == raw
