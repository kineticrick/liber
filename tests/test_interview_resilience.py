import json
from datetime import datetime

import pytest

from liber.errors import LiberError
from liber.interview import prompts
from liber.interview.brain import Brain, Opening
from liber.interview.brief import build_brief
from liber.interview.session import (
    InterviewSession, find_last_notes, notes_topic, recover_interviews, regenerate_notes, unfinished_workdirs,
)
from liber.interview.settings import InterviewSettings, interviews_dir
from voice_fakes import FakeConnection, FakeLiveClient, FakeLLM, settle, text_reply

pytestmark = pytest.mark.anyio
SETTINGS = InterviewSettings("Rick", max_minutes=10, warn_minutes=2)
OPENING = Opening("my career", "", "How did your career begin?")
NOW = datetime(2026, 10, 2, 9, 30, 0)


def responder(call):
    if call["system"] == prompts.NOTES_SYSTEM:
        return text_reply("## New facts\n- I started at Acme [00:02]")
    return text_reply(json.dumps({"hint": "h", "coverage": "c"}))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(vault, tmp_path, clock=None, live=None, **kw):
    brain = Brain(FakeLLM(responder), SETTINGS, vault, build_brief(vault, OPENING.topic))
    live = live or FakeLiveClient()
    session = InterviewSession(vault=vault, settings=SETTINGS, brain=brain, live=live, opening=OPENING,
                               workdir=tmp_path / "work", now=lambda: NOW, finished_turn_s=0.01,
                               close_wait_s=0.2, clock=clock or Clock(), **kw)
    return session, live


def me(text, start, end):
    return {"type": "session.input_transcript.delta", "delta": text, "start_ms": start, "end_ms": end}


async def test_expired_session_can_be_resumed(vault, tmp_path):
    session, live = make(vault, tmp_path, watch_interval_s=3600)
    await session.start("OFFER")
    await settle()
    first = live.connections[0]
    first.push(me(" Part one has words.", 1000, 2000))
    first.push({"type": "session.closed", "reason": "expired", "usage": {"seconds": 60.0}})
    await settle()
    assert session.state == "interrupted" and "Resume" in session.status()["message"]
    assert await session.resume("OFFER2") == "ANSWER-live_2"
    await settle()
    seed = live.created[1]["seed"]
    assert seed[0]["role"] == "developer" and "Part one has words." in seed[0]["content"][0]["text"]
    second = live.connections[1]
    assert second.sent[0] == ("instructions", prompts.resume_instruction("Rick"))
    second.push(me(" Part two.", 500, 900))
    await settle()
    result = await session.end("ended")
    text = result.transcript.read_text()
    assert text.index("Part one") < text.index("— resumed —") < text.index("Part two")
    assert session.session_ids == ["live_1", "live_2"]


async def test_dropped_connection_is_interrupted(vault, tmp_path):
    session, live = make(vault, tmp_path, watch_interval_s=3600)
    await session.start("OFFER")
    await settle()
    live.connections[0].drop()
    await settle()
    assert session.state == "interrupted"


async def test_resume_requires_interrupted(vault, tmp_path):
    session, _ = make(vault, tmp_path, watch_interval_s=3600)
    with pytest.raises(LiberError, match="nothing to resume"):
        await session.resume("OFFER")


async def test_heartbeat_timeout_finalizes(vault, tmp_path):
    # Review Focus 2
    clock = Clock()
    session, live = make(vault, tmp_path, clock=clock, watch_interval_s=0.01, heartbeat_timeout_s=60)
    await session.start("OFFER")
    await settle()
    live.connections[0].push(me(" I started at Acme in 2018.", 2000, 3000))
    await settle()
    clock.t += 61
    await settle(0.2)
    assert session.state == "done"
    assert session.result.transcript.is_file() and session.result.notes.is_file()


async def test_time_warning_then_limit(vault, tmp_path):
    clock = Clock()
    session, live = make(vault, tmp_path, clock=clock, watch_interval_s=0.01)
    await session.start("OFFER")
    await settle()
    conn = live.connections[0]
    conn.push(me(" I started at Acme in 2018.", 2000, 3000))
    clock.t += 8 * 60 + 1
    session.heartbeat()
    await settle(0.1)
    assert conn.sent.count(("instructions", prompts.time_warning("Rick", 2))) == 1
    clock.t += 2 * 60
    session.heartbeat()
    await settle(0.3)
    assert ("instructions", prompts.time_up("Rick")) in conn.sent
    assert session.state == "done"


async def test_recover_unfinished_interview(vault, tmp_path, monkeypatch):
    session, live = make(vault, tmp_path, watch_interval_s=3600)
    session.workdir = interviews_dir() / session.id
    await session.start("OFFER")
    await settle()
    live.connections[0].push(me(" I started at Acme in 2018.", 2000, 3000))
    await settle(0.1)
    session._save_draft()
    assert unfinished_workdirs() == [session.workdir]
    brains = []

    def brain_for_topic(topic):
        brains.append(topic)
        return Brain(FakeLLM(responder), SETTINGS, vault, build_brief(vault, topic))

    results = await recover_interviews(vault=vault, settings=SETTINGS, brain_for_topic=brain_for_topic)
    assert brains == ["my career"] and results[0].transcript.is_file() and results[0].notes.is_file()
    assert unfinished_workdirs() == []


async def test_regenerate_notes(vault):
    transcript = vault / "inbox" / "interview-2026-10-02-my-career.md"
    transcript.write_text(
        "<!-- liber interview transcript -->\n# Interview — my career — 2026-10-02\n\n"
        "- Duration: 38 min · Voice: gpt-live-1 (marin) · Brain: m\n- Notes: [[x]]\n\n**Me** [00:02]: Hi.\n")
    brain = Brain(FakeLLM(responder), SETTINGS, vault, build_brief(vault, "my career"))
    path = await regenerate_notes(transcript, brain)
    assert path.name == "interview-2026-10-02-my-career-notes.md"
    assert "# Interview notes — my career — 2026-10-02 (38 min)" in path.read_text()
    with pytest.raises(LiberError, match="not an interview transcript"):
        other = vault / "inbox" / "x.md"
        other.write_text("hello")
        await regenerate_notes(other, brain)


def test_find_last_notes_and_topic(vault):
    assert find_last_notes(vault) is None
    older = vault / "sources" / "documents" / "interview-2026-09-01-early-life-notes.md"
    older.parent.mkdir(parents=True, exist_ok=True)
    older.write_text("<!-- liber interview notes -->\n# Interview notes — early life — 2026-09-01 (20 min)\n")
    newer = vault / "inbox" / "interview-2026-10-02-my-career-notes.md"
    newer.write_text("<!-- liber interview notes -->\n# Interview notes — my career — 2026-10-02 (38 min)\n")
    assert find_last_notes(vault) == newer
    assert notes_topic(newer) == "my career"


class SilentCloseConnection(FakeConnection):
    async def close(self):
        self.sent.append(("close",))


class BadHangupClient(FakeLiveClient):
    async def create_session(self, **kw):
        result = await super().create_session(**kw)
        self.connections[-1] = SilentCloseConnection()
        return result

    async def hangup(self, session_id):
        raise RuntimeError("hangup failed")


async def test_end_survives_hangup_failure_on_close_timeout(vault, tmp_path):
    session, live = make(vault, tmp_path, live=BadHangupClient(), watch_interval_s=3600)
    session.close_wait_s = 0.05
    await session.start("OFFER")
    await settle()
    live.connections[0].push(me(" I started at Acme in 2018.", 2000, 3000))
    await settle()
    result = await session.end("ended")
    assert result.transcript is not None and result.transcript.is_file()
    assert session.state == "done"


async def test_resume_cancels_lingering_sideband(vault, tmp_path):
    session, live = make(vault, tmp_path, watch_interval_s=3600)
    await session.start("OFFER")
    await settle()
    old = [t for t in session._background if t is not session._watch_task]
    assert len(old) == 1
    session.state = "interrupted"  # as if the connection dropped but the old task is stuck
    await session.resume("OFFER2")
    await settle()
    assert old[0].done()
    await session.end("ended")

