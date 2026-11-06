"""Serialized background worker for posting turns and polling Muse history."""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from .config import Settings
from .errors import BridgeError
from .journal import Job, Journal, StateLock
from .muse import MuseAdapter, eligible_reply, event_text, payload, text_hash
from .protocol import INVISIBLE_PROTOCOL, outbound_text
from .requests import RoutedInput


def select_reply(
    events: list[dict],
    baseline: int,
    expected_text_hash: str,
    protocol: str = INVISIBLE_PROTOCOL,
) -> dict | None:
    """Find the completed assistant reply corresponding to our posted user message."""
    newer = [event for event in events if event["seq"] > baseline]
    # If another user message appears after our baseline, reply ownership is ambiguous.
    users = [event for event in newer if event.get("event_name") == "message.user"]
    if len(users) > 1 or any(
        text_hash(event_text(e, protocol)) != expected_text_hash for e in users
    ):
        raise BridgeError(
            409, "muse_interference", "Another client posted to the Muse main chat concurrently."
        )
    if not users:
        # Wait until our own user message appears in history so a late reply from a prior
        # turn cannot be mistaken for the reply to this turn.
        return None
    for event in newer:
        if event["seq"] <= users[0]["seq"]:
            continue
        if eligible_reply(event, baseline, protocol):
            return event
        if (
            event.get("event_name") == "message.assistant"
            and payload(event).get("status") in {"failed", "cancelled", "canceled", "aborted"}
            and not (event.get("reply_to_message_id") or payload(event).get("reply_to_message_id"))
        ):
            raise BridgeError(502, "muse_reply_failed", "Muse did not complete this reply.")
    return None


class BridgeWorker:
    """Coordinate single-turn execution, journal persistence, and manual recovery."""

    def __init__(self, settings: Settings, adapter=None):
        self.settings = settings
        self.state_lock = StateLock(settings.state_dir)
        try:
            self.journal = Journal(settings.state_dir)
            self.journal.recover_interrupted()
            self.adapter = adapter or MuseAdapter(settings, self.journal.node_id())
            self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="muse-owner")
        except BaseException:
            self.state_lock.close()
            raise
        self.stop = threading.Event()
        self.mutex = threading.RLock()
        self.futures: dict[str, Future] = {}
        self.closed = False

    def submit(self, routed: RoutedInput) -> Future:
        """Submit a conversation turn or attach to an existing in-flight/cached job."""
        with self.mutex:
            if self.closed:
                raise BridgeError(503, "bridge_stopping", "The bridge server is shutting down.")
            # Prune finished futures; completed jobs remain available via the SQLite journal cache.
            self.futures = {key: value for key, value in self.futures.items() if not value.done()}
            job, created = self.journal.reserve(
                routed.fingerprint,
                text_hash(routed.text),
                self.settings.cache_ttl,
                protocol=INVISIBLE_PROTOCOL,
                wire_hash=text_hash(outbound_text(routed.text)),
            )
            if not created:
                if job.state == "completed":
                    ready = Future()
                    ready.set_result(job)
                    return ready
                existing = self.futures.get(job.job_id)
                if existing is not None and not existing.done():
                    return existing
                raise BridgeError(
                    409,
                    "muse_turn_uncertain",
                    "Run 'reconcile' before retrying this uncertain turn.",
                    job_id=job.job_id,
                )
            future = self.executor.submit(self._run, job, routed.text)
            self.futures[job.job_id] = future
            return future

    def _run(self, job: Job, original: str) -> Job:
        may_have_sent = False
        try:
            with self.adapter.open() as session:
                history = session.history()
                last_user = next(
                    (e for e in reversed(history) if e.get("event_name") == "message.user"), None
                )
                if last_user and not any(eligible_reply(e, last_user["seq"]) for e in history):
                    raise BridgeError(
                        409,
                        "muse_already_working",
                        "The previous Muse turn has no completed reply yet; wait before posting.",
                    )
                baseline = max((e["seq"] for e in history), default=0)
                self.journal.update(job.job_id, "prepared", baseline=baseline)
                if self.stop.is_set():
                    raise BridgeError(503, "bridge_stopping", "The bridge server is shutting down.")
                # Persist 'sending' before network I/O so an unexpected crash never triggers
                # an automatic duplicate post on restart.
                self.journal.update(job.job_id, "sending")
                may_have_sent = True
                session.send(outbound_text(original, job.protocol))
                self.journal.update(job.job_id, "waiting")
                deadline = time.monotonic() + self.settings.reply_timeout
                while time.monotonic() < deadline:
                    if self.stop.is_set():
                        raise BridgeError(
                            503, "bridge_stopping", "The bridge stopped after posting the message."
                        )
                    reply = select_reply(session.history(), baseline, job.wire_hash, job.protocol)
                    if reply is not None:
                        self.journal.update(
                            job.job_id,
                            "completed",
                            response=event_text(reply, job.protocol),
                            response_seq=reply["seq"],
                            error_code=None,
                        )
                        return self.journal.get(job.job_id)
                    self.stop.wait(
                        min(self.settings.poll_interval, max(0, deadline - time.monotonic()))
                    )
                raise BridgeError(
                    504, "muse_reply_timeout", "Timed out waiting for Muse to complete a reply."
                )
        except Exception as error:
            public = (
                error
                if isinstance(error, BridgeError)
                else BridgeError(
                    503, "muse_unavailable", "The Muse operation could not be completed."
                )
            )
            self.journal.update(
                job.job_id, "uncertain" if may_have_sent else "failed", error_code=public.code
            )
            raise BridgeError(
                public.status, public.code, public.message, job_id=job.job_id
            ) from None

    def ready(self) -> dict:
        """Verify Muse connectivity on a dedicated session without posting."""
        # Open an independent Gateway connection so its WebSocket receiver never competes
        # with the background chat worker thread.
        with self.adapter.open() as session:
            session.history()
        return {"status": "ready", "muse": "connected", "message_sent": False}

    def reconcile(self, job_id: str) -> Job:
        """Recover the completed reply for an uncertain job from recent Muse history."""
        job = self.journal.get(job_id)
        if job.state == "completed":
            return job
        if job.state != "uncertain" or job.baseline is None:
            raise BridgeError(409, "job_not_uncertain", "Only an uncertain job can be reconciled.")
        with self.adapter.open() as session:
            events = session.history()
        # Require proof in history that this exact outbound message reached Muse.
        users = [
            e for e in events if e["seq"] > job.baseline and e.get("event_name") == "message.user"
        ]
        if not users or text_hash(event_text(users[0], job.protocol)) != job.wire_hash:
            raise BridgeError(
                409,
                "reconcile_unresolved",
                "The posted input was not found in recent Muse history.",
                job_id=job_id,
            )
        reply = select_reply(events, job.baseline, job.wire_hash, job.protocol)
        if reply is None:
            raise BridgeError(
                409,
                "reconcile_unresolved",
                "No completed reply is visible in recent Muse history yet.",
                job_id=job_id,
            )
        self.journal.update(
            job_id,
            "completed",
            response=event_text(reply, job.protocol),
            response_seq=reply["seq"],
            error_code=None,
        )
        return self.journal.get(job_id)

    def clear_pending(self, job_id: str):
        """Mark an uncertain job as cleared so new turns are no longer blocked."""
        job = self.journal.get(job_id)
        if job.state != "uncertain":
            raise BridgeError(409, "job_not_uncertain", "Only an uncertain job may be cleared.")
        self.journal.update(job_id, "cleared", response=None, error_code="operator_cleared")

    def close(self):
        with self.mutex:
            if self.closed:
                return
            self.closed = True
        self.stop.set()
        self.executor.shutdown(wait=True, cancel_futures=False)
        self.journal.close()
        self.state_lock.close()
