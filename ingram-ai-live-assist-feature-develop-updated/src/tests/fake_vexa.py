"""A Vexa look-alike on a real local socket: REST for bots and transcripts, and /ws.

Frames follow what the hosted service sends (docs.vexa.ai and live captures): a
``transcript`` frame carries ``confirmed`` and ``pending`` segments.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

BOT_KEY = "vxa_bot_test_key_0001"
TX_KEY = "vxa_tx_test_key_0002"
BOTH_KEY = "vxa_both_test_key_0003"
SCOPES = {BOT_KEY: {"bot"}, TX_KEY: {"tx"}, BOTH_KEY: {"bot", "tx"}}


def segment(segment_id, text, *, speaker="Helen Brooks", key="csrc:201", completed=False, start=1792745891.67):
    return {"segment_id": segment_id, "speaker": speaker, "speaker_key": key, "text": text,
            "start": start, "end": start + 2, "language": "en", "completed": completed}


def transcript(confirmed=(), pending=(), speaker="csrc:201"):
    return {"type": "transcript", "meeting": {"id": 1}, "speaker": speaker,
            "confirmed": list(confirmed), "pending": list(pending)}


def status(value):
    return {"type": "meeting.status", "meeting": {"platform": "teams", "native_id": "1"}, "payload": {"status": value}}


class FakeVexa:
    def __init__(self) -> None:
        self.bot_requests: list[dict] = []
        self.stop_requests: list[str] = []
        self.bot_response: tuple[int, dict] = (201, {"id": 1})
        self.transcript_status = 200
        self.script: list = []  # Frames (dicts) or ("sleep", seconds), sent after subscribing.
        self.connections = 0
        self.drop_first_connection = False
        self.subscriptions: list[dict] = []
        self.ws_keys: list[str] = []
        self.pings = 0
        self._thread: threading.Thread | None = None
        self._server: uvicorn.Server | None = None
        self.port = 0

    # --- lifecycle -----------------------------------------------------------------------

    def start(self) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        app = Starlette(routes=[
            Route("/bots", self._post_bots, methods=["POST"]),
            Route("/bots/{platform}/{native_id}", self._delete_bot, methods=["DELETE"]),
            Route("/transcripts/{platform}/{native_id}", self._get_transcript, methods=["GET"]),
            WebSocketRoute("/ws", self._ws),
        ])
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        for _ in range(100):
            if self._server.started:
                return
            time.sleep(0.05)
        raise RuntimeError("fake Vexa did not start")

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # --- handlers ------------------------------------------------------------------------

    def _allowed(self, request: Request, scope: str) -> bool:
        return scope in SCOPES.get(request.headers.get("x-api-key", ""), set())

    async def _post_bots(self, request: Request):
        if not self._allowed(request, "bot"):
            return JSONResponse({"detail": "Insufficient scope for this endpoint"}, status_code=403)
        self.bot_requests.append(await request.json())
        code, body = self.bot_response
        return JSONResponse(body, status_code=code)

    async def _delete_bot(self, request: Request):
        self.stop_requests.append(f"{request.path_params['platform']}/{request.path_params['native_id']}")
        return JSONResponse({"status": "stopping"})

    async def _get_transcript(self, request: Request):
        if not self._allowed(request, "tx"):
            return JSONResponse({"detail": "Insufficient scope for this endpoint"}, status_code=403)
        return JSONResponse({"segments": []}, status_code=self.transcript_status)

    async def _ws(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections += 1
        self.ws_keys.append(ws.headers.get("x-api-key", ""))
        try:
            self.subscriptions.append(json.loads(await ws.receive_text()))
            if self.drop_first_connection and self.connections == 1:
                await ws.close()
                return
            await ws.send_text(json.dumps({"type": "subscribed", "meetings": self.subscriptions[-1]["meetings"]}))
            for item in self.script:
                if isinstance(item, tuple) and item[0] == "sleep":
                    await asyncio.sleep(item[1])
                else:
                    await ws.send_text(json.dumps(item) if isinstance(item, dict) else item)
            while True:  # Stay connected, answering pings, until the client leaves.
                message = json.loads(await ws.receive_text())
                if message.get("action") == "ping":
                    self.pings += 1
                    await ws.send_text(json.dumps({"type": "pong"}))
        except WebSocketDisconnect:
            return
