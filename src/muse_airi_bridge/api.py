from __future__ import annotations

import asyncio
import hmac
import json
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from . import __version__
from .config import Settings
from .emotions import decorate
from .errors import BridgeError
from .requests import MODEL, ChatRequest, route
from .worker import BridgeWorker


def completion(content: str, response_id: str, created: int) -> dict:
    return {
        "id": response_id,
        "object": "chat.completion",
        "created": created,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


def sse_chunks(content: str, response_id: str, created: int):
    base = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": MODEL,
    }
    for delta, reason in [
        ({"role": "assistant", "content": ""}, None),
        ({"content": content}, None),
        ({}, "stop"),
    ]:
        event = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
        yield ("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8")
    yield b"data: [DONE]\n\n"


def create_app(settings: Settings | None = None, *, adapter=None) -> FastAPI:
    settings = settings or Settings.from_env()
    if len(settings.api_key) < 16 or settings.api_key.startswith("replace-with-"):
        raise ValueError("Set MUSE_BRIDGE_API_KEY to a random local key of at least 16 characters.")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = BridgeWorker(settings, adapter=adapter)
        app.state.worker = worker
        try:
            yield
        finally:
            await asyncio.to_thread(worker.close)

    app = FastAPI(title="Muse AIRI Bridge", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Muse-Bridge-Response", "X-Muse-Bridge-Job"],
    )

    async def authorize(request: Request):
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            token.encode("utf-8"), settings.api_key.encode("utf-8")
        ):
            raise BridgeError(401, "invalid_api_key", "A valid local bridge API key is required.")

    @app.exception_handler(BridgeError)
    async def bridge_error(_request: Request, error: BridgeError):
        headers = {"WWW-Authenticate": "Bearer"} if error.status == 401 else None
        return JSONResponse(error.payload(), status_code=error.status, headers=headers)

    @app.get("/healthz")
    async def healthz():
        return {"status": "alive", "version": __version__, "muse": "not_checked"}

    @app.get("/readyz", dependencies=[Depends(authorize)])
    async def readyz(request: Request):
        return await asyncio.to_thread(request.app.state.worker.ready)

    @app.get("/v1/models", dependencies=[Depends(authorize)])
    async def models():
        return {
            "object": "list",
            "data": [
                {"id": MODEL, "object": "model", "created": 0, "owned_by": "muse-airi-bridge"}
            ],
        }

    @app.post("/v1/chat/completions", dependencies=[Depends(authorize)])
    async def chat_completions(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > settings.max_body_bytes:
                raise BridgeError(413, "request_too_large", "The request body is too large.")
        try:
            chat = ChatRequest.model_validate_json(bytes(body))
        except ValidationError:
            raise BridgeError(
                400, "invalid_request", "Invalid Chat Completions request body."
            ) from None
        routed = route(chat)
        if routed.kind == "probe":
            return JSONResponse(
                completion("pong", "chatcmpl-local-" + uuid.uuid4().hex, int(time.time())),
                headers={"X-Muse-Bridge-Response": "local-probe"},
            )
        job_future = request.app.state.worker.submit(routed)
        future = asyncio.wrap_future(job_future)
        # Consume safe exceptions even when a disconnected HTTP handler no longer awaits this job.
        future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        while not future.done():
            await asyncio.wait({future}, timeout=0.1)
            if not future.done() and await request.is_disconnected():
                return Response(status_code=499)
        job = await asyncio.shield(future)
        content = decorate(job.response or "", settings.emotions)
        response_id = "chatcmpl-" + job.job_id
        created = int(job.created_at)
        headers = {"X-Muse-Bridge-Response": "muse", "X-Muse-Bridge-Job": job.job_id}
        if not chat.stream:
            return JSONResponse(completion(content, response_id, created), headers=headers)
        # Buffer until completion so failures retain their real HTTP status and never look
        # like successful empty speech. This is SSE transport, not live token generation.
        return StreamingResponse(
            sse_chunks(content, response_id, created),
            media_type="text/event-stream",
            headers={**headers, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
