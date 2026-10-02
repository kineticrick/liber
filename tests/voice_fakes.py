"""Offline stand-ins for Claude and the OpenAI Live service."""

import asyncio
from contextlib import asynccontextmanager

from liber.interview.brain import LLMResponse


def text_reply(text: str, stop: str = "end_turn") -> LLMResponse:
    return LLMResponse(stop, [{"type": "text", "text": text}])


def tool_reply(name: str, args: dict, id: str = "tu1") -> LLMResponse:
    return LLMResponse("tool_use", [{"type": "tool_use", "id": id, "name": name, "input": args}])


class FakeLLM:
    """`responder(call) -> LLMResponse | Exception | awaitable`, where call is the recorded kwargs dict."""

    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict] = []

    async def complete(self, *, model, system, messages, max_tokens, tools=None, tool_choice=None):
        call = {"model": model, "system": system, "messages": [dict(m) for m in messages],
                "max_tokens": max_tokens, "tools": tools, "tool_choice": tool_choice}
        self.calls.append(call)
        result = self.responder(call)
        if asyncio.iscoroutine(result):
            result = await result
        if isinstance(result, Exception):
            raise result
        return result


class FakeConnection:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.sent: list[tuple] = []

    def push(self, event: dict) -> None:
        self.queue.put_nowait(event)

    def drop(self) -> None:
        self.queue.put_nowait(None)

    async def recv(self):
        return await self.queue.get()

    async def commentary(self, content, delegation_id):
        self.sent.append(("commentary", content, delegation_id))

    async def thinking(self, content):
        self.sent.append(("thinking", content))

    async def instructions(self, content):
        self.sent.append(("instructions", content))

    async def mute(self):
        self.sent.append(("mute",))

    async def unmute(self):
        self.sent.append(("unmute",))

    async def close(self):
        self.sent.append(("close",))
        self.push({"type": "session.closed", "reason": "close_requested"})

    def kinds(self) -> list[str]:
        return [item[0] for item in self.sent]


class FakeLiveClient:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.created: list[dict] = []
        self.connections: list[FakeConnection] = []
        self.hangups: list[str] = []

    async def create_session(self, *, sdp, instructions, voice, seed=None):
        if self.fail is not None:
            raise self.fail
        self.created.append({"sdp": sdp, "instructions": instructions, "voice": voice, "seed": seed})
        self.connections.append(FakeConnection())
        session_id = f"live_{len(self.created)}"
        return session_id, f"ANSWER-{session_id}"

    @asynccontextmanager
    async def attach(self, session_id):
        yield self.connections[int(session_id.split("_")[1]) - 1]

    async def hangup(self, session_id):
        self.hangups.append(session_id)


async def settle(seconds: float = 0.05) -> None:
    await asyncio.sleep(seconds)
