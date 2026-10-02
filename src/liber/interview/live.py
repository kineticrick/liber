"""The OpenAI Live (GPT-Live-1) side: session creation and the sideband connection."""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

from liber.errors import LiberError

log = logging.getLogger("liber.interview")

LIVE_MODEL = "gpt-live-1"
REFLECTED_AUDIO = frozenset({"session.input_audio.append", "session.output_audio.delta"})


class LiveError(LiberError):
    """The voice service refused or failed."""


class LiveConnection(Protocol):
    async def recv(self) -> dict | None: ...
    async def commentary(self, content: str, delegation_id: str) -> None: ...
    async def thinking(self, content: str) -> None: ...
    async def instructions(self, content: str) -> None: ...
    async def mute(self) -> None: ...
    async def unmute(self) -> None: ...
    async def close(self) -> None: ...


class LiveClient(Protocol):
    async def create_session(
        self, *, sdp: str, instructions: str, voice: str, seed: list[dict] | None = None
    ) -> tuple[str, str]: ...
    def attach(self, session_id: str) -> AbstractAsyncContextManager[LiveConnection]: ...
    async def hangup(self, session_id: str) -> None: ...


class _OpenAIConnection:
    def __init__(self, conn) -> None:
        self._conn = conn
        self._seq = 0

    def _event_id(self, prefix: str) -> str:
        self._seq += 1
        return f"liber_{prefix}_{self._seq}"

    async def recv(self) -> dict | None:
        from websockets.exceptions import ConnectionClosed

        while True:
            try:
                raw = await self._conn.recv_bytes()
            except ConnectionClosed:
                return None
            try:
                event = json.loads(raw)
            except ValueError:
                log.warning("ignored an unparseable sideband message")
                continue
            if isinstance(event, dict) and event.get("type") not in REFLECTED_AUDIO:
                return event

    async def commentary(self, content: str, delegation_id: str) -> None:
        await self._conn.session.commentary.append(
            content=content, delegation_id=delegation_id, event_id=self._event_id("c")
        )

    async def thinking(self, content: str) -> None:
        await self._conn.session.thinking.append(content=content, delegation_id=None, event_id=self._event_id("t"))

    async def instructions(self, content: str) -> None:
        await self._conn.session.instructions.append(
            content=content, delegation_id=None, event_id=self._event_id("i")
        )

    async def mute(self) -> None:
        await self._conn.session.input_audio.mute(event_id=self._event_id("m"))

    async def unmute(self) -> None:
        await self._conn.session.input_audio.unmute(event_id=self._event_id("u"))

    async def close(self) -> None:
        await self._conn.session.close(event_id=self._event_id("x"))


class OpenAILiveClient:
    def __init__(self, api_key: str, *, http_client=None, websocket_base_url: str | None = None) -> None:
        from openai import AsyncOpenAI

        kwargs: dict = {"api_key": api_key, "max_retries": 0}
        if http_client is not None:
            kwargs["http_client"] = http_client
        if websocket_base_url is not None:
            kwargs["websocket_base_url"] = websocket_base_url
        self._client = AsyncOpenAI(**kwargs)

    async def create_session(
        self, *, sdp: str, instructions: str, voice: str, seed: list[dict] | None = None
    ) -> tuple[str, str]:
        import openai

        session: dict = {
            "model": LIVE_MODEL,
            "audio": {"output": {"voice": voice}},
            "instructions": instructions,
            "delegation": {"type": "client"},
        }
        if seed:
            session["input"] = seed
        try:
            result = await self._client.live.create(session=session, transport={"type": "webrtc", "sdp": sdp})
        except openai.APIStatusError as exc:
            code = getattr(exc, "code", None)
            hint = ""
            if exc.status_code == 401:
                hint = " — check your OpenAI API key (liber interview --setup)"
            elif exc.status_code == 403 or code == "model_not_found":
                hint = " — your OpenAI account may not have GPT-Live-1 access"
            elif exc.status_code == 429:
                hint = " — rate limit or quota reached"
            msg = f"OpenAI refused the voice session ({exc.status_code}{', ' + code if code else ''}){hint}"
            raise LiveError(msg) from exc
        except openai.APIError as exc:
            raise LiveError(f"could not reach OpenAI ({type(exc).__name__})") from exc
        try:
            session_id = result.session.id
            answer_sdp = result.transport.sdp
            if not session_id or not answer_sdp:
                raise AttributeError("empty result")
        except AttributeError as exc:
            raise LiveError("OpenAI returned an unexpected response") from exc
        return session_id, answer_sdp

    @asynccontextmanager
    async def attach(self, session_id: str) -> AsyncIterator[LiveConnection]:
        import openai
        from websockets.exceptions import WebSocketException

        manager = self._client.live.sideband.connect(session_id=session_id)
        try:
            conn = await manager.__aenter__()
        except (WebSocketException, OSError, openai.OpenAIError) as exc:
            raise LiveError(f"could not attach to the voice session ({type(exc).__name__})") from exc
        try:
            yield _OpenAIConnection(conn)
        finally:
            await manager.__aexit__(None, None, None)

    async def hangup(self, session_id: str) -> None:
        import openai

        try:
            await self._client.live.sessions.hangup(session_id)
        except openai.APIError as exc:
            log.warning("hangup failed (%s)", type(exc).__name__)
