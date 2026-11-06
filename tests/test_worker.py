from concurrent.futures import ThreadPoolExecutor

import pytest

from muse_airi_bridge.errors import BridgeError
from muse_airi_bridge.journal import pending_summary
from muse_airi_bridge.muse import text_hash
from muse_airi_bridge.protocol import INVISIBLE_PROTOCOL, outbound_text
from muse_airi_bridge.requests import ChatRequest, route
from muse_airi_bridge.worker import BridgeWorker, select_reply

from .fakes import REPLY_WIRE, FakeAdapter, conversation, event, settings


def routed(text="original"):
    return route(ChatRequest.model_validate(conversation(text)))


def test_completed_reply_and_cached_replay_use_one_post(tmp_path):
    adapter = FakeAdapter()
    worker = BridgeWorker(settings(tmp_path), adapter)
    try:
        result = worker.submit(routed()).result(2)
        replay = worker.submit(routed()).result(2)
        assert result == replay
        assert result.response == REPLY_WIRE
        assert result.text_hash == text_hash("original")
        assert result.wire_hash == text_hash(outbound_text("original"))
        assert result.protocol == INVISIBLE_PROTOCOL
        assert adapter.sent == [outbound_text("original")]
        assert len(set(adapter.receivers)) == 1
    finally:
        worker.close()
    assert adapter.closed == adapter.opens


def test_concurrent_identical_requests_share_one_job_and_other_turn_is_busy(tmp_path):
    adapter = FakeAdapter(auto_reply=False)
    worker = BridgeWorker(settings(tmp_path), adapter)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = list(pool.map(lambda _: worker.submit(routed()), range(4)))
        assert adapter.posted.wait(1)
        assert len({id(f) for f in futures}) == 1
        with pytest.raises(BridgeError) as error:
            worker.submit(routed("different"))
        assert error.value.code == "muse_busy"
        adapter.release.set()
        assert futures[0].result(2).state == "completed"
        assert adapter.sent == [outbound_text("original")]
    finally:
        worker.close()


def test_baseline_failure_posts_nothing_and_is_safe_to_retry(tmp_path):
    adapter = FakeAdapter(failure="baseline")
    worker = BridgeWorker(settings(tmp_path), adapter)
    try:
        with pytest.raises(BridgeError):
            worker.submit(routed()).result(2)
        assert adapter.sent == []
        adapter.failure = None
        assert worker.submit(routed()).result(2).state == "completed"
        assert adapter.sent == [outbound_text("original")]
    finally:
        worker.close()


def test_unknown_send_failure_is_redacted_and_never_reposted_after_restart(tmp_path):
    adapter = FakeAdapter(failure="send")
    config = settings(tmp_path)
    worker = BridgeWorker(config, adapter)
    with pytest.raises(BridgeError) as error:
        worker.submit(routed()).result(2)
    job_id = error.value.job_id
    assert "SENSITIVE" not in str(error.value.payload())
    worker.close()
    second = BridgeWorker(config, adapter)
    try:
        with pytest.raises(BridgeError) as repeated:
            second.submit(routed())
        assert repeated.value.code == "muse_turn_uncertain"
        adapter.failure = None
        result = second.reconcile(job_id)
        assert result.state == "completed"
        assert second.submit(routed()).result(2).response == REPLY_WIRE
        assert adapter.sent == [outbound_text("original")]
    finally:
        second.close()


def test_timeout_remains_pending_until_operator_reconciles_or_clears(tmp_path):
    adapter = FakeAdapter(auto_reply=False)
    config = settings(tmp_path)
    worker = BridgeWorker(config, adapter)
    try:
        with pytest.raises(BridgeError) as error:
            worker.submit(routed()).result(2)
        assert error.value.status == 504
        job_id = error.value.job_id
        assert pending_summary(config.state_dir)[0]["state"] == "uncertain"
        with pytest.raises(BridgeError, match="No completed reply"):
            worker.reconcile(job_id)
        worker.clear_pending(job_id)
        with pytest.raises(BridgeError) as cleared:
            worker.submit(routed())
        assert cleared.value.code == "job_cleared"
        adapter.auto_reply = True
        assert worker.submit(routed("new deliberate turn")).result(2).state == "completed"
    finally:
        worker.close()


def test_second_process_cannot_take_over_a_live_state_directory(tmp_path):
    config = settings(tmp_path)
    worker = BridgeWorker(config, FakeAdapter())
    try:
        with pytest.raises(BridgeError) as error:
            BridgeWorker(config, FakeAdapter())
        assert error.value.code == "state_in_use"
    finally:
        worker.close()


def test_interrupted_send_is_not_treated_as_safe_to_send_again(tmp_path):
    config = settings(tmp_path)
    worker = BridgeWorker(config, FakeAdapter())
    job, _ = worker.journal.reserve(
        routed().fingerprint,
        text_hash("original"),
        600,
        protocol=INVISIBLE_PROTOCOL,
        wire_hash=text_hash(outbound_text("original")),
    )
    worker.journal.update(job.job_id, "sending", baseline=10)
    worker.close()
    after_restart = BridgeWorker(config, FakeAdapter())
    try:
        assert after_restart.journal.get(job.job_id).state == "uncertain"
        with pytest.raises(BridgeError):
            after_restart.submit(routed())
    finally:
        after_restart.close()


def test_ready_does_not_post(tmp_path):
    adapter = FakeAdapter()
    worker = BridgeWorker(settings(tmp_path), adapter)
    try:
        assert worker.ready()["message_sent"] is False
        assert adapter.sent == []
    finally:
        worker.close()


def test_preexisting_unfinished_muse_turn_blocks_a_new_post(tmp_path):
    adapter = FakeAdapter()
    adapter.events.append(event(11, "message.user", "existing owner turn"))
    worker = BridgeWorker(settings(tmp_path), adapter)
    try:
        with pytest.raises(BridgeError) as error:
            worker.submit(routed()).result(2)
        assert error.value.code == "muse_already_working"
        assert adapter.sent == []
    finally:
        worker.close()


def test_filter_waits_for_matching_owner_input_and_completed_genuine_reply():
    owner = event(11, "message.user", "original")
    stale = event(9)
    late_old_reply = event(10)
    unfinished = event(12, status=None)
    presentation = event(13, "delta.presentation")
    background = event(14, reply_to_message_id="self-reference")
    actual = event(15, text="real answer")
    assert (
        select_reply(
            [stale, late_old_reply, owner, unfinished, presentation, background, actual],
            9,
            text_hash("original"),
        )
        == actual
    )
    assert select_reply([actual], 9, text_hash("original")) is None


def test_other_owner_input_and_failed_reply_are_not_returned_as_success():
    with pytest.raises(BridgeError) as interference:
        select_reply(
            [event(11, "message.user", "other user"), event(12)], 10, text_hash("original")
        )
    assert interference.value.code == "muse_interference"
    with pytest.raises(BridgeError) as failed:
        select_reply(
            [event(11, "message.user", "original"), event(12, status="failed")],
            10,
            text_hash("original"),
        )
    assert failed.value.code == "muse_reply_failed"


def test_header_matching_and_reply_collection_use_content_not_display_text(tmp_path):
    from contextlib import contextmanager

    class DifferentDisplayAdapter(FakeAdapter):
        @contextmanager
        def open(self):
            with super().open() as session:
                read = session.history

                def history():
                    records = read()
                    for item in records:
                        item["payload"]["display_text"] = "different UI representation"
                        item["display_text"] = "different top-level representation"
                    return records

                session.history = history
                yield session

    adapter = DifferentDisplayAdapter()
    worker = BridgeWorker(settings(tmp_path), adapter)
    try:
        result = worker.submit(routed("  original\n")).result(2)
        assert result.response == REPLY_WIRE
        assert adapter.sent == [outbound_text("  original\n")]
    finally:
        worker.close()


def test_new_protocol_never_guesses_from_display_text_if_content_is_missing():
    owner = event(11, "message.user", outbound_text("original"))
    del owner["payload"]["content"]
    with pytest.raises(BridgeError) as error:
        select_reply([owner, event(12)], 10, text_hash(outbound_text("original")))
    assert error.value.code == "muse_content_missing"
