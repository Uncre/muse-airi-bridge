"""Actual HTTP/SSE and disconnect recovery, with no network calls to Muse."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from .fakes import conversation


def test_socket_disconnect_does_not_cancel_post_or_duplicate_on_retry(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = {**os.environ, "BRIDGE_TEST_STATE_DIR": str(tmp_path / "http-state")}
    log_path = tmp_path / "server.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "tests.fakes:create_socket_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
                "--log-level",
                "error",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            base_url = f"http://127.0.0.1:{port}"
            with httpx.Client(base_url=base_url, trust_env=False) as client:
                deadline = time.monotonic() + 10
                while True:
                    if process.poll() is not None:
                        pytest.fail("Test-only server exited before startup")
                    try:
                        if client.get("/healthz", timeout=0.2).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() >= deadline:
                        pytest.fail("Test-only server did not start")
                    time.sleep(0.05)
                headers = {"Authorization": "Bearer test-key-at-least-16-characters"}
                body = conversation("socket original", stream=True)
                with pytest.raises(httpx.ReadTimeout):
                    client.post("/v1/chat/completions", json=body, headers=headers, timeout=0.1)
                # The client is gone; the posted job still owns and collects its reply.
                time.sleep(0.6)
                retry = client.post("/v1/chat/completions", json=body, headers=headers, timeout=3)
                assert retry.status_code == 200
                assert retry.headers["content-type"].startswith("text/event-stream")
                assert retry.text.endswith("data: [DONE]\n\n")
                stats = client.get("/test-only-stats").json()
                assert stats["posts"] == 1
                assert stats["open"] == stats["closed"]
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    assert "Traceback" not in log_path.read_text(encoding="utf-8")
