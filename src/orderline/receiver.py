"""A tiny HTTP endpoint for Debezium Server's HTTP sink."""

from __future__ import annotations

import json
import logging
import threading
import time

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from orderline.spool import Spool

log = logging.getLogger(__name__)


def build_app(spool: Spool) -> Starlette:
    async def events(request: Request) -> PlainTextResponse:
        try:
            body = json.loads(await request.body())
        except ValueError:
            return PlainTextResponse("body is not JSON", status_code=400)
        # One event per request, or a JSON array when the sink batches.
        batch = body if isinstance(body, list) else [body]
        if not all(isinstance(e, dict | None) for e in batch):
            return PlainTextResponse("expected an event object or an array of them", status_code=400)
        spool.add(batch)
        return PlainTextResponse("ok")

    async def health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    return Starlette(routes=[Route("/events", events, methods=["POST"]), Route("/health", health)])


class Receiver:
    """Runs the endpoint on a background thread for the length of a `with` block."""

    def __init__(self, spool: Spool, port: int):
        config = uvicorn.Config(build_app(spool), host="0.0.0.0", port=port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> Receiver:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("the receiver didn't start")
            time.sleep(0.05)
        return self

    def __exit__(self, *_) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
