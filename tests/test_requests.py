import pytest

from muse_airi_bridge.errors import BridgeError
from muse_airi_bridge.requests import ChatRequest, extract_original, route

from .fakes import conversation


@pytest.mark.parametrize(
    "original",
    [
        "こんにちは",
        "  前後空白  ",
        "改行\n\n末尾\n",
        "ping",
        "emoji🙂 and English",
        "\tタブ\r\n次行\t",
    ],
)
def test_only_original_text_survives(original):
    routed = route(ChatRequest.model_validate(conversation(original)))
    assert routed.text == original
    assert routed.kind == "conversation"


@pytest.mark.parametrize(
    "reference", ["[Replying to: 以前の ] 発言]\n", "[Replying to message: message-id]\n"]
)
def test_reply_skill_and_runtime_context_are_removed(reference):
    rendered = (
        "[2026-11-06 12:34] " + reference + " 原文\n\n"
        '\n\n[The owner used the skill "skill". Follow its steps in this reply '
        "and in later replies while they fit.]\nSECRET_SKILL"
        "\n[Context]\n- source: SECRET_CONTEXT"
    )
    assert extract_original(rendered) == " 原文\n\n"


def test_text_arrays_join_without_adding_newlines():
    body = conversation()
    body["messages"][-1]["content"] = [
        {"type": "text", "text": "[2026-11-06 12:34] "},
        {"type": "text", "text": "音声認識済みの原文\n"},
        {"type": "text", "text": "\n[Context]\n- source: generated"},
    ]
    assert route(ChatRequest.model_validate(body)).text == "音声認識済みの原文\n"


@pytest.mark.parametrize(
    "rendered",
    [
        "plain user input",
        "[2026-99-31 12:00] input",
        "[2026-11-06 12:00] \n  ",
        "[2026-11-06 12:00] [Replying to: malformed",
    ],
)
def test_unknown_or_invalid_envelopes_are_refused(rendered):
    with pytest.raises(BridgeError, match="."):
        extract_original(rendered)


def test_input_marker_collision_is_explicitly_not_supported():
    assert extract_original("[2026-11-06 12:00] a\n[Context]\nuser-typed") == "a"


def test_only_exact_validation_probe_is_local():
    probe = {
        "model": "muse-airi",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 16,
    }
    assert route(ChatRequest.model_validate(probe)).kind == "probe"
    probe["max_tokens"] = 32
    with pytest.raises(BridgeError):
        route(ChatRequest.model_validate(probe))
    assert route(ChatRequest.model_validate(conversation("ping"))).kind == "conversation"


@pytest.mark.parametrize(
    "suffix",
    [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "result"},
    ],
)
def test_continuations_never_fall_back_to_a_previous_user(suffix):
    body = conversation()
    body["messages"].append(suffix)
    with pytest.raises(BridgeError):
        route(ChatRequest.model_validate(body))


@pytest.mark.parametrize(
    "system",
    [
        "You are the Cinematic Director for AIRI. Analyze the latest user input.",
        'You run the recipe "x" in its own space for your conversation with the owner.',
        "This is AIRI system, the life pod hosting your consciousness.",
        "Unrecognized helper instruction",
    ],
)
def test_auxiliary_system_prompts_are_refused_even_with_dated_user(system):
    body = conversation()
    body["messages"][0]["content"] += "\n" + system
    if system == "Unrecognized helper instruction":
        body["messages"][0]["content"] = system
    with pytest.raises(BridgeError):
        route(ChatRequest.model_validate(body))


def test_notices_are_not_owner_speech():
    with pytest.raises(BridgeError):
        route(
            ChatRequest.model_validate(
                conversation(
                    "[Notice from recipe:x, not a message from the owner.]\nTask finished."
                )
            )
        )


@pytest.mark.parametrize(
    "override",
    [
        {"tool_choice": "required"},
        {"tool_choice": {"type": "function", "function": {"name": "weather"}}},
        {"response_format": {"type": "json_object"}},
        {"n": 2},
        {"stop": "end"},
        {"functions": [{"name": "legacy"}]},
        {"modalities": ["audio"]},
    ],
)
def test_unsupported_generation_modes_are_refused(override):
    with pytest.raises(BridgeError):
        route(ChatRequest.model_validate(conversation(**override)))


@pytest.mark.parametrize(
    "part",
    [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        {"type": "input_audio", "input_audio": {"data": "AAA", "format": "wav"}},
        {"type": "file", "file": {"file_data": "AAA"}},
    ],
)
def test_multimodal_parts_are_never_silently_discarded(part):
    body = conversation()
    body["messages"][-1]["content"] = [
        {"type": "text", "text": "[2026-11-06 12:00] describe"},
        part,
    ]
    with pytest.raises(BridgeError):
        route(ChatRequest.model_validate(body))


def test_auto_tools_and_inference_knobs_do_not_change_forwarded_text():
    body = conversation(
        "原文",
        tools=[{"type": "function", "function": {"name": "local"}}],
        tool_choice="auto",
        temperature=0.1,
        max_tokens=16,
    )
    assert route(ChatRequest.model_validate(body)).text == "原文"


def test_retry_identity_ignores_transport_but_distinguishes_later_turns():
    first = conversation("same")
    stream = {**first, "stream": True, "stream_options": {"include_usage": True}}
    original = route(ChatRequest.model_validate(first))
    assert original.fingerprint == route(ChatRequest.model_validate(stream)).fingerprint
    later = conversation("same")
    later["messages"].insert(-1, {"role": "assistant", "content": "new preceding answer"})
    assert original.fingerprint != route(ChatRequest.model_validate(later)).fingerprint
