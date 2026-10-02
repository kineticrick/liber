import asyncio
import json
from datetime import datetime

import pytest

from liber.interview import prompts
from liber.interview.brain import Brain, LLMError, Opening
from liber.interview.brief import build_brief
from liber.interview.live import LiveError
from liber.interview.session import InterviewResult, InterviewSession
from liber.interview.settings import InterviewSettings
from voice_fakes import FakeLiveClient, FakeLLM, settle, text_reply

pytestmark = pytest.mark.anyio
SETTINGS = InterviewSettings("Rick")
OPENING = Opening("my career", "", "How did your career begin?")
NOW = datetime(2026, 10, 2, 9, 30, 0)


def responder(call):
    if call["system"] == prompts.STEER_SYSTEM:
        return text_reply(json.dumps({"hint": "Ask about the payments team.", "coverage": "first job"}))
    if call["system"] == prompts.DELEGATION_SYSTEM:
        return text_reply("What did you build there?")
    if call["system"] == prompts.NOTES_SYSTEM:
        return text_reply("## New facts\n- I started at Acme in 2018 [00:02]")
    raise AssertionError(call["system"])


def make(vault, tmp_path, llm_responder=responder, live=None):
    brain = Brain(FakeLLM(llm_responder), SETTINGS, vault, build_brief(vault, OPENING.topic))
    live = live or FakeLiveClient()
    session = InterviewSession(vault=vault, settings=SETTINGS, brain=brain, live=live, opening=OPENING,
                               workdir=tmp_path / "work", now=lambda: NOW, finished_turn_s=0.01,
                               watch_interval_s=3600, close_wait_s=1.0)
    return session, live


def me(text, start, end):
    return {"type": "session.input_transcript.delta", "delta": text, "start_ms": start, "end_ms": end}


def ai(text, start, end):
    return {"type": "session.output_transcript.delta", "delta": text, "start_ms": start, "end_ms": end}


async def test_start_creates_session_and_sends_opening(vault, tmp_path):
    session, live = make(vault, tmp_path)
    assert await session.start("OFFER") == "ANSWER-live_1"
    await settle()
    assert live.created[0]["sdp"] == "OFFER" and live.created[0]["voice"] == "marin"
    assert "Rick" in live.created[0]["instructions"] and "my career" in live.created[0]["instructions"]
    conn = live.connections[0]
    assert conn.sent[0] == ("instructions", prompts.opening_instruction("Rick", "How did your career begin?"))
    assert session.state == "live" and session.session_ids == ["live_1"]
    assert (tmp_path / "work" / "state.json").is_file()
    await session.end("cleanup")


async def test_full_interview_produces_both_files(vault, tmp_path):
    session, live = make(vault, tmp_path)
    await session.start("OFFER")
    await settle()
    conn = live.connections[0]
    conn.push(ai(" How did it begin?", 1000, 1500))
    conn.push(me(" I started at Acme in 2018.", 2000, 3000))
    await settle()
    assert ("thinking", "Ask about the payments team.") in conn.sent
    assert session.coverage == "first job"
    conn.push({"type": "session.delegation.created", "offset_ms": 4000,
               "delegation": {"id": "item_9", "type": "delegation", "target": "client"}})
    await settle()
    assert ("commentary", "What did you build there?", "item_9") in conn.sent
    conn.push({"type": "session.usage.updated", "usage": {"seconds": 120.0}})
    await settle()
    result = await session.end("ended by user")
    assert conn.kinds()[-1] == "close"
    assert result.transcript == vault / "inbox" / "interview-2026-10-02-my-career.md"
    assert result.notes == vault / "inbox" / "interview-2026-10-02-my-career-notes.md"
    transcript = result.transcript.read_text()
    assert "**Me** [00:02]: I started at Acme in 2018." in transcript
    assert "- Duration: 2 min" in transcript and "[[interview-2026-10-02-my-career-notes]]" in transcript
    notes = result.notes.read_text()
    assert notes.startswith("<!-- liber interview notes -->") and "Source: [[interview-2026-10-02-my-career]]" in notes
    assert session.state == "done" and session.done.is_set()
    assert not (tmp_path / "work").exists()


async def test_short_utterances_do_not_trigger_steering(vault, tmp_path):
    session, live = make(vault, tmp_path)
    await session.start("OFFER")
    await settle()
    live.connections[0].push(me(" Um, yes.", 2000, 2500))
    await settle()
    assert session.brain.llm.calls == []
    await session.end("cleanup")


async def test_stale_hint_is_dropped(vault, tmp_path):
    # Review Focus 4
    release = asyncio.Event()
    calls = []

    async def slow_first(call):
        calls.append(call)
        if len(calls) == 1:
            await release.wait()
            return text_reply(json.dumps({"hint": "STALE", "coverage": "old"}))
        return text_reply(json.dumps({"hint": "FRESH", "coverage": "new"}))

    session, live = make(vault, tmp_path, lambda c: slow_first(c))
    await session.start("OFFER")
    await settle()
    conn = live.connections[0]
    conn.push(me(" First answer has words.", 1000, 2000))
    await settle()
    conn.push(me(" Second answer also has words.", 6000, 7000))
    await settle()
    release.set()
    await settle()
    thinking = [s[1] for s in conn.sent if s[0] == "thinking"]
    assert thinking == ["FRESH"] and session.coverage == "new"
    await session.end("cleanup")


async def test_delegation_failure_sends_fallback(vault, tmp_path):
    # Review Focus 3
    session, live = make(vault, tmp_path, lambda c: LLMError("down"))
    await session.start("OFFER")
    await settle()
    conn = live.connections[0]
    conn.push({"type": "session.delegation.created", "offset_ms": 1, "delegation": {"id": "item_1"}})
    await settle()
    assert ("commentary", prompts.FALLBACK_LINE, "item_1") in conn.sent
    await session.end("cleanup")


async def test_typed_note_and_hold(vault, tmp_path):
    session, live = make(vault, tmp_path)
    await session.start("OFFER")
    await settle()
    conn = live.connections[0]
    await session.add_note("  I also ran hiring. ")
    assert ("thinking", prompts.typed_note_context("Rick", "I also ran hiring.")) in conn.sent
    assert any(t.text == "I also ran hiring." for t in session.assembler.turns())
    with pytest.raises(Exception, match="empty"):
        await session.add_note("   ")
    with pytest.raises(Exception, match="2,000"):
        await session.add_note("x" * 2001)
    assert await session.toggle_hold() is True
    assert conn.sent[-2:] == [("mute",), ("instructions", prompts.hold_on("Rick"))]
    assert await session.toggle_hold() is False
    assert conn.sent[-2:] == [("unmute",), ("instructions", prompts.hold_off("Rick"))]
    await session.end("cleanup")


async def test_no_speech_writes_no_files(vault, tmp_path):
    # Review Focus 5
    session, live = make(vault, tmp_path)
    await session.start("OFFER")
    await settle()
    live.connections[0].push(ai(" Hello Rick?", 1000, 1500))
    await settle()
    result = await session.end("ended by user")
    assert result.transcript is None and result.notes is None
    assert not list((vault / "inbox").glob("interview-*"))


async def test_notes_failure_still_delivers_transcript(vault, tmp_path):
    def responder_no_notes(call):
        if call["system"] == prompts.NOTES_SYSTEM:
            return LLMError("x")
        return responder(call)

    session, live = make(vault, tmp_path, responder_no_notes)
    await session.start("OFFER")
    await settle()
    live.connections[0].push(me(" I started at Acme in 2018.", 2000, 3000))
    await settle()
    result = await session.end("ended by user")
    assert result.transcript.is_file() and result.notes is None
    assert "liber interview --notes" in session.status()["message"]


async def test_start_failure_raises_and_stays_idle(vault, tmp_path):
    session, _ = make(vault, tmp_path, live=FakeLiveClient(fail=LiveError("OpenAI refused the voice session (403): no")))
    with pytest.raises(LiveError, match="403"):
        await session.start("OFFER")
    assert session.state == "idle"


async def test_file_name_collisions(vault, tmp_path):
    (vault / "inbox" / "interview-2026-10-02-my-career.md").write_text("old")
    session, live = make(vault, tmp_path)
    await session.start("OFFER")
    await settle()
    live.connections[0].push(me(" I started at Acme in 2018.", 2000, 3000))
    await settle()
    result = await session.end("ended")
    assert result.transcript.name == "interview-2026-10-02-my-career-2.md"
    assert result.notes.name == "interview-2026-10-02-my-career-2-notes.md"


async def test_status_shape(vault, tmp_path):
    session, _ = make(vault, tmp_path)
    status = session.status()
    assert status["state"] == "idle" and status["topic"] == "my career" and status["result"] is None


async def test_end_before_attach_closes_cleanly(vault, tmp_path):
    session, live = make(vault, tmp_path)
    await session.start("OFFER")
    await session.end("x")
    await settle()
    closed = any(c.kinds()[-1:] == ["close"] for c in live.connections)
    assert closed or live.hangups == ["live_1"]
    assert all(t.done() for t in session._background)
    assert session.state == "done"


async def test_finalize_failure_does_not_hang(vault, tmp_path):
    session, live = make(vault, tmp_path)
    await session.start("OFFER")
    await settle()

    async def boom():
        raise OSError("disk")

    session.finalize = boom
    first = await session.end("x")
    second = await asyncio.wait_for(session.end("y"), 1)
    assert first == second == InterviewResult(None, None, failed=True)
    assert session.state == "done" and "--recover" in session.message
    assert (tmp_path / "work" / "state.json").is_file()


async def test_malformed_event_is_ignored(vault, tmp_path):
    session, live = make(vault, tmp_path)
    await session.start("OFFER")
    await settle()
    conn = live.connections[0]
    conn.push({"type": "session.input_transcript.delta", "delta": " x", "start_ms": None, "end_ms": None})
    conn.push(me(" I started at Acme.", 2000, 3000))
    await settle()
    assert session.state == "live"
    assert any("Acme" in t.text for t in session.assembler.turns())
    await session.end("cleanup")


async def _one_turn_session(vault, tmp_path, responder_fn=responder):
    session, live = make(vault, tmp_path, responder_fn)
    await session.start("OFFER")
    await settle()
    live.connections[0].push(me(" I started at Acme in 2018.", 2000, 3000))
    await settle()
    return session, live


async def test_unexpected_notes_error_still_delivers_transcript(vault, tmp_path):
    def boom(call):
        if call["system"] == prompts.NOTES_SYSTEM:
            raise RuntimeError("sdk exploded")
        return responder(call)

    session, _ = await _one_turn_session(vault, tmp_path, boom)
    result = await session.end("ended by user")
    assert result.transcript.is_file() and result.notes is None and not result.failed
    assert not (tmp_path / "work").exists()
    assert "liber interview --notes" in session.status()["message"]


async def test_finalize_failure_is_flagged_and_reported(vault, tmp_path, monkeypatch):
    session, _ = await _one_turn_session(vault, tmp_path)

    async def broken():
        raise OSError("disk full")

    monkeypatch.setattr(session, "finalize", broken)
    result = await session.end("ended by user")
    assert result.failed and result.transcript is None
    assert "--recover" in session.message
    status = session.status()["result"]
    assert status["failed"] is True and "--recover" in status["message"]


async def test_finalize_reports_voice_seconds(vault, tmp_path):
    session, live = await _one_turn_session(vault, tmp_path)
    live.connections[0].push({"type": "session.usage.updated", "usage": {"seconds": 125}})
    await settle()
    result = await session.end("ended by user")
    assert result.voice_seconds == 125
