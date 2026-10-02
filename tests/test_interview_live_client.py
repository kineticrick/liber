import asyncio
import json

import httpx2
import pytest
import websockets

from liber.interview.live import LiveError, OpenAILiveClient

pytestmark = pytest.mark.anyio


def http(handler):
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


async def test_create_session_body_and_answer():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx2.Response(201, json={"session": {"id": "live_x"}, "transport": {"type": "webrtc", "sdp": "ANSWER"}})

    client = OpenAILiveClient("sk-test", http_client=http(handler))
    seed = [{"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "so far"}]}]
    assert await client.create_session(sdp="OFFER", instructions="INSTR", voice="marin", seed=seed) == ("live_x", "ANSWER")
    assert seen["url"] == "https://api.openai.com/v1/live/sessions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"] == {
        "session": {"model": "gpt-live-1", "audio": {"output": {"voice": "marin"}}, "instructions": "INSTR",
                    "delegation": {"type": "client"}, "input": seed},
        "transport": {"type": "webrtc", "sdp": "OFFER"},
    }


async def test_create_session_errors_are_live_errors_401():
    client = OpenAILiveClient("sk-test", http_client=http(
        lambda r: httpx2.Response(401, json={
            "error": {
                "message": "Incorrect API key provided: sk-proj-****abcd",
                "type": "invalid_request_error",
                "code": "invalid_api_key"
            }
        })))
    with pytest.raises(LiveError) as info:
        await client.create_session(sdp="OFFER", instructions="I", voice="marin")
    exc_str = str(info.value)
    assert "401" in exc_str
    assert "invalid_api_key" in exc_str
    assert "liber interview --setup" in exc_str
    assert "sk-proj" not in exc_str
    assert "****" not in exc_str


async def test_create_session_errors_model_not_found_hint():
    client = OpenAILiveClient("sk-test", http_client=http(
        lambda r: httpx2.Response(403, json={
            "error": {
                "message": "Model not available",
                "type": "invalid_request_error",
                "code": "model_not_found"
            }
        })))
    with pytest.raises(LiveError) as info:
        await client.create_session(sdp="OFFER", instructions="I", voice="marin")
    exc_str = str(info.value)
    assert "403" in exc_str
    assert "model_not_found" in exc_str
    assert "GPT-Live-1 access" in exc_str


async def test_sideband_recv_and_send_against_local_ws():
    received: list[dict] = []

    async def server(ws):
        await ws.send(json.dumps({"type": "session.input_audio.append", "audio": "AAAA"}))
        await ws.send(json.dumps({"type": "session.output_audio.delta", "delta": "BBBB", "start_ms": 0, "end_ms": 1}))
        await ws.send("not json")
        await ws.send(json.dumps({"type": "session.input_transcript.delta", "delta": " hi", "start_ms": 0, "end_ms": 100}))
        for _ in range(6):
            received.append(json.loads(await ws.recv()))
        await ws.close()

    async with websockets.serve(server, "127.0.0.1", 0) as ws_server:
        port = ws_server.sockets[0].getsockname()[1]
        client = OpenAILiveClient("sk-test", websocket_base_url=f"ws://127.0.0.1:{port}/v1")
        async with client.attach("live_x") as conn:
            event = await conn.recv()
            assert event == {"type": "session.input_transcript.delta", "delta": " hi", "start_ms": 0, "end_ms": 100}
            await conn.commentary("say this", "item_1")
            await conn.thinking("quiet")
            await conn.instructions("do this")
            await conn.mute()
            await conn.unmute()
            await conn.close()
            assert await asyncio.wait_for(conn.recv(), 5) is None
    types = [m["type"] for m in received]
    assert types == ["session.commentary.append", "session.thinking.append", "session.instructions.append",
                     "session.input_audio.mute", "session.input_audio.unmute", "session.close"]
    assert received[0]["delegation_id"] == "item_1" and received[0]["content"] == "say this"
    assert received[1]["delegation_id"] is None and received[2]["delegation_id"] is None


async def test_hangup_failure_is_swallowed():
    client = OpenAILiveClient("sk-test", http_client=http(lambda r: httpx2.Response(500, json={"error": {"message": "x"}})))
    await client.hangup("live_x")


async def test_attach_handshake_failure_is_live_error():
    async def reject(connection, request):
        return connection.respond(401, "nope\n")

    async with websockets.serve(lambda ws: ws, "127.0.0.1", 0, process_request=reject) as ws_server:
        port = ws_server.sockets[0].getsockname()[1]
        client = OpenAILiveClient("sk-test", websocket_base_url=f"ws://127.0.0.1:{port}/v1")
        with pytest.raises(LiveError, match="could not attach"):
            async with client.attach("live_x"):
                pass
