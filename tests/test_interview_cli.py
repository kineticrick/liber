import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from liber.cli import app
from liber.config import user_config_path, write_user_config
from liber.interview import prompts, runner
from liber.interview.settings import InterviewSettings, VoiceKeys, load_interview_settings
from liber.server.settings import read_secret_values, write_secret_values
from voice_fakes import FakeLiveClient, FakeLLM, text_reply


def responder(call):
    if call["system"] == prompts.OPENING_SYSTEM:
        return text_reply(json.dumps({"topic": "early life", "reason": "Thin area.", "question": "Where did you grow up?"}))
    if call["system"] == prompts.NOTES_SYSTEM:
        return text_reply("## New facts\n- x [00:01]")
    return text_reply(json.dumps({"hint": "h", "coverage": "c"}))


@pytest.fixture
def configured(vault):
    write_user_config(vault)
    with user_config_path().open("a", encoding="utf-8") as handle:
        handle.write('\n[interview]\nname = "Rick"\n')
    write_secret_values({"openai_api_key": "sk-o", "anthropic_api_key": "sk-a"})
    return vault


def test_prepare_settings_overrides(configured):
    s = runner.prepare_settings(20, "claude-opus-5-5")
    assert (s.max_minutes, s.model) == (20, "claude-opus-5-5")
    with pytest.raises(Exception, match="minutes"):
        runner.prepare_settings(3, None)  # must exceed warn_minutes (5)


@pytest.mark.anyio
async def test_run_interview_picks_topic_and_serves(configured, monkeypatch):
    announced = []
    llm = FakeLLM(responder)
    live = FakeLiveClient()

    async def fake_serve(app, session, open_browser, announce):
        assert open_browser is False
        announce("served")
        return await session.end("test")

    monkeypatch.setattr(runner, "_serve", fake_serve)
    result = await runner.run_interview(
        topic=None, continue_last=False, settings=InterviewSettings("Rick"), keys=VoiceKeys("sk-o", "sk-a"),
        vault=configured, open_browser=False, announce=announced.append, llm=llm, live=live)
    assert result.transcript is None
    assert any("early life" in line and "Thin area." in line for line in announced)
    assert "served" in announced


@pytest.mark.anyio
async def test_continue_uses_last_notes(configured, monkeypatch):
    notes = configured / "inbox" / "interview-2026-10-02-my-career-notes.md"
    notes.write_text("<!-- liber interview notes -->\n# Interview notes — my career — 2026-10-02 (38 min)\n\n## New facts\n- PREVIOUSFACT\n")
    llm = FakeLLM(responder)

    async def fake_serve(app, session, open_browser, announce):
        return await session.end("test")

    monkeypatch.setattr(runner, "_serve", fake_serve)
    await runner.run_interview(topic=None, continue_last=True, settings=InterviewSettings("Rick"),
                               keys=VoiceKeys("o", "a"), vault=configured, open_browser=False,
                               announce=lambda s: None, llm=llm, live=FakeLiveClient())
    first = llm.calls[0]["messages"][0]["content"]
    assert "The user chose the topic: my career" in first and "PREVIOUSFACT" in first


@pytest.mark.anyio
async def test_continue_without_previous_interview(configured):
    with pytest.raises(Exception, match="no previous interview"):
        await runner.run_interview(topic=None, continue_last=True, settings=InterviewSettings("Rick"),
                                   keys=VoiceKeys("o", "a"), vault=configured, open_browser=False,
                                   announce=lambda s: None, llm=FakeLLM(responder), live=FakeLiveClient())


def test_cli_setup(vault):
    write_user_config(vault)
    r = CliRunner().invoke(app, ["interview", "--setup"], input="Rick\nsk-open\nsk-anth\n")
    assert r.exit_code == 0, r.output
    assert load_interview_settings().name == "Rick"
    assert read_secret_values()["anthropic_api_key"] == "sk-anth"
    assert "sk-open" not in r.output and "sk-anth" not in r.output


def test_cli_missing_extra(configured, monkeypatch):
    def missing():
        raise runner.LiberError("voice support isn't installed; run: uv tool install --editable '.[voice]'")

    monkeypatch.setattr(runner, "require_voice_extra", missing)
    r = CliRunner().invoke(app, ["interview", "my career"])
    assert r.exit_code == 1 and "[voice]" in r.output


def test_cli_missing_setup(vault):
    write_user_config(vault)
    r = CliRunner().invoke(app, ["interview", "my career"])
    assert r.exit_code == 1 and "liber interview --setup" in r.output


def test_cli_conflicting_flags(configured):
    r = CliRunner().invoke(app, ["interview", "x", "--continue"])
    assert r.exit_code == 1 and "either" in r.output


def test_cli_runs_interview(configured, monkeypatch):
    seen = {}

    async def fake_run(**kwargs):
        seen.update(kwargs)
        kwargs["announce"]("hello")
        return runner.InterviewResult(Path("/v/inbox/t.md"), Path("/v/inbox/t-notes.md"))

    monkeypatch.setattr(runner, "run_interview", fake_run)
    r = CliRunner().invoke(app, ["interview", "my career", "--minutes", "20", "--no-browser"])
    assert r.exit_code == 0, r.output
    assert seen["topic"] == "my career" and seen["open_browser"] is False and seen["settings"].max_minutes == 20
    assert "/v/inbox/t.md" in r.output and "/v/inbox/t-notes.md" in r.output


def test_cli_recover_needs_only_anthropic_key(vault, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    write_user_config(vault)
    with user_config_path().open("a", encoding="utf-8") as handle:
        handle.write('\n[interview]\nname = "Rick"\n')
    write_secret_values({"anthropic_api_key": "sk-a"})
    r = CliRunner().invoke(app, ["interview", "--recover"])
    assert r.exit_code == 0, r.output
    assert "No unfinished interviews." in r.output


def _serve_parts(vault):
    from liber.interview.brain import Brain, Opening
    from liber.interview.brief import build_brief
    from liber.interview.session import InterviewSession
    from liber.interview.web import create_app

    settings = InterviewSettings("Rick")
    opening = Opening("my career", "", "How did your career begin?")
    brain = Brain(FakeLLM(responder), settings, vault, build_brief(vault, opening.topic))
    session = InterviewSession(vault=vault, settings=settings, brain=brain, live=FakeLiveClient(), opening=opening)
    app = create_app(session, "tok")
    app.state.token = "tok"
    return app, session


async def _wait_for_line(lines, text):
    for _ in range(100):
        if any(text in line for line in lines):
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"never announced {text!r}: {lines}")


@pytest.mark.anyio
async def test_serve_sigint_finalizes(configured):
    import os
    import signal

    app, session = _serve_parts(configured)
    lines = []
    before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    task = asyncio.create_task(runner._serve(app, session, open_browser=False, announce=lines.append))
    await _wait_for_line(lines, "Interview page:")
    os.kill(os.getpid(), signal.SIGINT)
    result = await asyncio.wait_for(task, 5)
    assert isinstance(result, runner.InterviewResult)
    assert session.done.is_set()
    assert any("Finishing the interview" in line for line in lines)
    assert {s: signal.getsignal(s) for s in before} == before


@pytest.mark.anyio
async def test_serve_returns_when_session_ends(configured):
    app, session = _serve_parts(configured)
    lines = []
    task = asyncio.create_task(runner._serve(app, session, open_browser=False, announce=lines.append))
    await _wait_for_line(lines, "Interview page:")
    await session.end("x")
    result = await asyncio.wait_for(task, 5)
    assert isinstance(result, runner.InterviewResult)
