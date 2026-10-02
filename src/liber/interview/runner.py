"""Wiring a real interview together: settings, Claude, OpenAI Live, the local page and the browser."""

import asyncio
import contextlib
import dataclasses
import secrets
import signal
import socket
import webbrowser
from collections.abc import Callable
from pathlib import Path

from liber.errors import LiberError
from liber.interview.brain import AnthropicLLM, Brain
from liber.interview.brief import build_brief
from liber.interview.settings import InterviewSettings, VoiceKeys, load_interview_settings
from liber.interview.session import (
    InterviewResult, InterviewSession, find_last_notes, notes_topic, recover_interviews, regenerate_notes,
)

__all__ = ["InterviewResult", "LiberError", "prepare_settings", "require_voice_extra", "run_interview",
           "run_notes", "run_recover"]


def require_voice_extra() -> None:
    try:
        import anthropic  # noqa: F401
        import openai  # noqa: F401
        import websockets  # noqa: F401
    except ImportError as exc:
        raise LiberError("voice support isn't installed; run: uv tool install --editable '.[voice]'") from exc


def prepare_settings(minutes: int | None, model: str | None) -> InterviewSettings:
    settings = load_interview_settings()
    if minutes is not None:
        if minutes <= settings.warn_minutes:
            raise LiberError(f"--minutes must be more than warn_minutes ({settings.warn_minutes})")
        settings = dataclasses.replace(settings, max_minutes=minutes)
    if model:
        settings = dataclasses.replace(settings, model=model)
    return settings


FINAL_STATUS_GRACE_S = 1.0  # lets the page fetch its final status before the server stops


async def _serve(app, session: InterviewSession, open_browser: bool, announce: Callable[[str], None]) -> InterviewResult:
    import uvicorn

    class _Server(uvicorn.Server):
        def capture_signals(self):  # we own SIGINT/SIGTERM so Ctrl-C finalizes the interview
            return contextlib.nullcontext()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server = None
    helpers: list[asyncio.Task] = []
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    handled: list[signal.Signals] = []

    def on_signal() -> None:
        if stop.is_set():
            announce("Still finishing; please wait.")
        else:
            announce("Finishing the interview — writing your transcript and notes…")
            stop.set()

    serving = None
    startup_failed = False
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.add_signal_handler(sig, on_signal)
                handled.append(sig)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}/?t={app.state.token}"
        server = _Server(uvicorn.Config(app, log_level="warning", access_log=False))
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        announce(f"Interview page: {url}")
        if open_browser:
            webbrowser.open(url)
        finished = asyncio.create_task(session.done.wait())
        stopping = asyncio.create_task(stop.wait())
        helpers = [finished, stopping]
        await asyncio.wait({serving, finished, stopping}, return_when=asyncio.FIRST_COMPLETED)
        startup_failed = serving.done() and not session.done.is_set() and not stop.is_set()
    finally:
        try:
            if session.done.is_set():
                result = session.result
                if server is not None and serving is not None and not serving.done():
                    await asyncio.sleep(FINAL_STATUS_GRACE_S)
            else:
                result = await asyncio.shield(session.end("stopped from the terminal"))
        finally:
            if server is not None:
                server.should_exit = True
            if serving is not None:
                with contextlib.suppress(BaseException):
                    await serving
            for task in helpers:
                task.cancel()
            for sig in handled:
                loop.remove_signal_handler(sig)
            sock.close()
    if startup_failed:
        raise LiberError("the local interview page could not start")
    return result


async def run_interview(
    *,
    topic: str | None,
    continue_last: bool,
    settings: InterviewSettings,
    keys: VoiceKeys,
    vault: Path,
    open_browser: bool,
    announce: Callable[[str], None],
    llm=None,
    live=None,
) -> InterviewResult:
    from liber.interview.live import OpenAILiveClient
    from liber.interview.web import create_app

    previous = ""
    if continue_last:
        last = find_last_notes(vault)
        if last is None:
            raise LiberError("there is no previous interview to continue")
        topic = notes_topic(last)
        previous = last.read_text(encoding="utf-8")
    llm = llm or AnthropicLLM(keys.anthropic)
    brain = Brain(llm, settings, vault, build_brief(vault, topic, previous))
    opening = await brain.plan_opening(topic)
    if topic is None:
        brain.brief = build_brief(vault, opening.topic, previous)
        announce(f"Topic: {opening.topic} — {opening.reason}".rstrip(" —"))
    else:
        announce(f"Topic: {opening.topic}")
    session = InterviewSession(vault=vault, settings=settings, brain=brain,
                               live=live or OpenAILiveClient(keys.openai), opening=opening)
    token = secrets.token_urlsafe(24)
    app = create_app(session, token)
    app.state.token = token
    return await _serve(app, session, open_browser, announce)


async def run_recover(*, settings: InterviewSettings, anthropic_key: str, vault: Path, llm=None) -> list[InterviewResult]:
    llm = llm or AnthropicLLM(anthropic_key)
    return await recover_interviews(
        vault=vault, settings=settings, brain_for_topic=lambda t: Brain(llm, settings, vault, build_brief(vault, t))
    )


async def run_notes(*, transcript: Path, settings: InterviewSettings, anthropic_key: str, vault: Path, llm=None) -> Path:
    llm = llm or AnthropicLLM(anthropic_key)
    return await regenerate_notes(transcript, Brain(llm, settings, vault, build_brief(vault, None)))
