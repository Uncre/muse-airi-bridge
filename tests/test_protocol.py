from pathlib import Path

import pytest

from muse_airi_bridge.protocol import (
    EMOTION_CODES,
    FOOTER_PREFIX,
    INPUT_HEADER,
    LEGACY_PROTOCOL,
    ONE,
    ZERO,
    decode_bytes,
    emotion_footer,
    encode_bytes,
    outbound_text,
    parse_reply,
)


@pytest.mark.parametrize(
    "data", [b"", b"AIRI1", b"AE1:H", bytes(range(256)), "ねこ".encode(), "めいど".encode()]
)
def test_msb_first_codec_round_trip(data):
    bits = encode_bytes(data)
    assert set(bits) <= {ZERO, ONE}
    assert len(bits) == len(data) * 8
    assert decode_bytes(bits) == data


def test_independent_published_bit_vectors():
    def visible(value):
        return value.replace(ZERO, "0").replace(ONE, "1")

    assert visible(INPUT_HEADER) == "0100000101001001010100100100100100110001"
    assert visible(FOOTER_PREFIX) == "01000001010001010011000100111010"
    assert visible(emotion_footer("happy"))[-8:] == "01001000"
    assert decode_bytes(encode_bytes(bytes.fromhex("e381ade38193"))).decode() == "ねこ"


@pytest.mark.parametrize("bad", ["0" * 40, ZERO * 7, ZERO * 8 + "x", "\u200d" * 8])
def test_decoder_refuses_visible_bits_and_other_invisible_characters(bad):
    with pytest.raises(ValueError):
        decode_bytes(bad)


@pytest.mark.parametrize(
    "original",
    [
        "  空白\n次行\r\n",
        "ねこ",
        "\t",
        ZERO + "x" + ONE,
        INPUT_HEADER + "already starts with header",
    ],
)
def test_header_is_exactly_40_characters_and_preserves_original(original):
    wire = outbound_text(original)
    assert wire[:40] == INPUT_HEADER
    assert wire[40:] == original
    assert len(wire) == len(original) + 40
    assert outbound_text(original, LEGACY_PROTOCOL) == original


@pytest.mark.parametrize(("code", "emotion"), EMOTION_CODES.items())
def test_all_nine_emotions_and_body_are_decoded(code, emotion):
    body = " 本文\n\u200d" + encode_bytes(b"keep") + "😊\n"
    footer = emotion_footer(emotion)
    assert decode_bytes(footer) == b"AE1:" + code.encode()
    reply = parse_reply(body + footer)
    assert reply.text == body
    assert reply.emotion == emotion
    assert reply.status == "valid"


@pytest.mark.parametrize(
    "raw",
    [
        "normal 😊",
        encode_bytes(b"hello"),
        emotion_footer("happy") + "\n",
        emotion_footer("happy")[:-1],
        "quoted「" + emotion_footer("angry") + "」",
        "```\n" + emotion_footer("angry") + "\n```",
    ],
)
def test_absent_or_nonterminal_footer_is_not_stripped(raw):
    reply = parse_reply(raw)
    assert reply.text == raw
    assert reply.emotion == "neutral"


def test_footer_inside_body_is_preserved_when_a_real_footer_follows():
    body = "本文" + emotion_footer("angry") + "中の符号" + ZERO + ONE
    assert parse_reply(body + emotion_footer("happy")).text == body


@pytest.mark.parametrize(
    "suffix",
    [encode_bytes(b"AE1:X"), encode_bytes(b"AE1:\xff"), FOOTER_PREFIX + ZERO * 7 + "\u200d"],
)
def test_recognizable_invalid_footer_is_removed_but_neutral(suffix):
    reply = parse_reply("body" + suffix)
    assert (reply.text, reply.emotion, reply.status) == ("body", "neutral", "invalid")


def test_damaged_signature_is_not_guessed_or_globally_deleted():
    footer = emotion_footer("happy")
    damaged = (ONE if footer[0] == ZERO else ZERO) + footer[1:]
    reply = parse_reply("body" + damaged)
    assert reply.text == "body" + damaged
    assert reply.emotion == "neutral"


@pytest.mark.parametrize("count", [2, 3])
def test_duplicate_contiguous_protocol_footers_are_removed_as_neutral(count):
    reply = parse_reply("body" + emotion_footer("happy") * count)
    assert (reply.text, reply.emotion, reply.status) == ("body", "neutral", "duplicate")


def test_soul_addendum_contains_actual_copyable_symbols_and_all_footers():
    prompt = (Path(__file__).parents[1] / "docs" / "muse-soul-addendum.md").read_text(
        encoding="utf-8"
    )
    assert INPUT_HEADER in prompt
    for name in EMOTION_CODES.values():
        assert emotion_footer(name) in prompt
    assert "最新のユーザー発言" in prompt
    assert "自発通知" in prompt
