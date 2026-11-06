import json

import pytest
from fastapi.testclient import TestClient

from muse_airi_bridge.api import create_app
from muse_airi_bridge.protocol import EMOTION_CODES, emotion_footer, outbound_text

from .fakes import FakeAdapter, conversation, settings


def test_local_probe_and_model_list_work_even_when_muse_is_offline(tmp_path):
    config = settings(tmp_path)
    adapter = FakeAdapter(failure="open")
    with TestClient(create_app(config, adapter=adapter)) as client:
        headers = {"Authorization": "Bearer " + config.api_key}
        models = client.get("/v1/models", headers=headers)
        assert models.status_code == 200
        assert models.json()["data"][0]["id"] == "muse-airi"
        probe = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "muse-airi",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 16,
            },
        )
        assert probe.status_code == 200
        assert probe.headers["X-Muse-Bridge-Response"] == "local-probe"
        assert probe.json()["choices"][0]["message"]["content"] == "pong"
        assert adapter.opens == 0
        ready = client.get("/readyz", headers=headers)
        assert ready.status_code == 503
        assert ready.json()["error"]["code"] == "muse_auth_required"
    assert adapter.sent == []


def test_auth_cors_and_invalid_json_are_handled_before_any_post(tmp_path):
    config = settings(tmp_path)
    adapter = FakeAdapter()
    with TestClient(create_app(config, adapter=adapter)) as client:
        assert client.get("/healthz").json()["muse"] == "not_checked"
        assert client.get("/v1/models").status_code == 401
        preflight = client.options(
            "/v1/chat/completions",
            headers={
                "Origin": "null",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "*"
        invalid = client.post(
            "/v1/chat/completions",
            content=b"not json",
            headers={"Authorization": "Bearer " + config.api_key},
        )
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "invalid_request"
        assert adapter.opens == 0


@pytest.mark.parametrize("stream", [False, True])
def test_openai_response_envelope_and_original_only_post(tmp_path, stream):
    config = settings(tmp_path)
    adapter = FakeAdapter()
    headers = {"Authorization": "Bearer " + config.api_key}
    original = " 原文\n次の行 "
    with TestClient(create_app(config, adapter=adapter)) as client:
        response = client.post(
            "/v1/chat/completions", json=conversation(original, stream=stream), headers=headers
        )
        assert response.status_code == 200
        assert response.headers["X-Muse-Bridge-Response"] == "muse"
        if not stream:
            body = response.json()
            assert body["object"] == "chat.completion"
            content = body["choices"][0]["message"]["content"]
            assert body["choices"][0]["finish_reason"] == "stop"
            assert "tool_calls" not in body["choices"][0]["message"]
            assert "usage" not in body
        else:
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = [s[6:] for s in response.text.splitlines() if s.startswith("data: ")]
            assert lines[-1] == "[DONE]"
            chunks = [json.loads(s) for s in lines[:-1]]
            assert len({chunk["id"] for chunk in chunks}) == 1
            assert all(chunk["object"] == "chat.completion.chunk" for chunk in chunks)
            assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
            assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
            content = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
        marker, reply = content.split("|>", 1)
        # The same JSON extraction contract as AIRI's parseActEmotion.
        assert json.loads(marker[len("<|ACT ") :])["emotion"]["name"] == "happy"
        assert reply == "回答です😊"
        assert adapter.sent == [outbound_text(original)]
        replay = client.post(
            "/v1/chat/completions", json=conversation(original, stream=False), headers=headers
        )
        assert replay.status_code == 200
        assert len(adapter.sent) == 1


@pytest.mark.parametrize(
    "change", ["notice", "director", "unknown", "tool-result", "image", "forced"]
)
def test_auxiliary_calls_are_rejected_with_zero_posts(tmp_path, change):
    config = settings(tmp_path)
    adapter = FakeAdapter()
    body = conversation()
    if change == "notice":
        body = conversation("[Notice from module:x, not a message from the owner.]\nnotice")
    elif change == "director":
        body["messages"][0]["content"] = "You are the Cinematic Director for AIRI."
    elif change == "unknown":
        body["messages"][-1]["content"] = "an undecorated helper prompt"
    elif change == "tool-result":
        body["messages"].append({"role": "tool", "content": "result", "tool_call_id": "call"})
    elif change == "image":
        body["messages"][-1]["content"] = [{"type": "image_url", "image_url": {"url": "x"}}]
    else:
        body["tool_choice"] = "required"
    with TestClient(create_app(config, adapter=adapter)) as client:
        response = client.post(
            "/v1/chat/completions", json=body, headers={"Authorization": "Bearer " + config.api_key}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "unsupported_request_kind"
    assert adapter.sent == []
    assert adapter.opens == 0


def test_timeout_is_http_error_not_successful_empty_stream(tmp_path):
    config = settings(tmp_path)
    adapter = FakeAdapter(auto_reply=False)
    with TestClient(create_app(config, adapter=adapter)) as client:
        headers = {"Authorization": "Bearer " + config.api_key}
        response = client.post(
            "/v1/chat/completions", json=conversation(stream=True), headers=headers
        )
        assert response.status_code == 504
        assert "text/event-stream" not in response.headers["content-type"]
        assert response.json()["error"]["job_id"]
        retry = client.post("/v1/chat/completions", json=conversation(stream=True), headers=headers)
        assert retry.status_code == 409
        assert len(adapter.sent) == 1


def test_oversized_body_and_unknown_model_do_not_post(tmp_path):
    config = settings(tmp_path, max_body_bytes=2048)
    adapter = FakeAdapter()
    with TestClient(create_app(config, adapter=adapter)) as client:
        headers = {"Authorization": "Bearer " + config.api_key}
        response = client.post(
            "/v1/chat/completions", json=conversation("a" * 4096), headers=headers
        )
        assert response.status_code == 413
        body = conversation()
        body["model"] = "other"
        assert client.post("/v1/chat/completions", json=body, headers=headers).status_code == 404
    assert not adapter.sent


def test_emotion_decoration_can_be_disabled(tmp_path):
    config = settings(tmp_path, emotions=False)
    with TestClient(create_app(config, adapter=FakeAdapter())) as client:
        response = client.post(
            "/v1/chat/completions",
            json=conversation(),
            headers={"Authorization": "Bearer " + config.api_key},
        )
        assert response.json()["choices"][0]["message"]["content"] == "回答です😊"


@pytest.mark.parametrize("emotion", EMOTION_CODES.values())
def test_all_nine_muse_emotions_are_translated_and_cached_without_footer_leak(tmp_path, emotion):
    config = settings(tmp_path)
    body = " 本文\n絵文字は😡でも感情はMuseのコードを使う\n"
    adapter = FakeAdapter(reply_text=body + emotion_footer(emotion))
    with TestClient(create_app(config, adapter=adapter)) as client:
        headers = {"Authorization": "Bearer " + config.api_key}
        first = client.post("/v1/chat/completions", json=conversation("原文"), headers=headers)
        second = client.post("/v1/chat/completions", json=conversation("原文"), headers=headers)
        assert first.json() == second.json()
        content = first.json()["choices"][0]["message"]["content"]
        marker, visible = content.split("|>", 1)
        assert json.loads(marker[len("<|ACT ") :])["emotion"]["name"] == emotion
        assert visible == body
        assert adapter.sent == [outbound_text("原文")]


def test_missing_footer_defaults_to_neutral_without_repair_call_or_repost(tmp_path):
    config = settings(tmp_path)
    adapter = FakeAdapter(reply_text="😊嬉しいように見えてもコードなし")
    with TestClient(create_app(config, adapter=adapter)) as client:
        result = client.post(
            "/v1/chat/completions",
            json=conversation("原文"),
            headers={"Authorization": "Bearer " + config.api_key},
        )
        assert result.status_code == 200
        content = result.json()["choices"][0]["message"]["content"]
        assert '"name":"neutral"' in content
        assert content.split("|>", 1)[1] == adapter.reply_text
        assert len(adapter.sent) == 1
        assert adapter.opens == 1
