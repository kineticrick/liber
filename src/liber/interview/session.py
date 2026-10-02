"""One voice interview, from start to the files in the inbox."""

import asyncio
import json
import logging
import os
import shutil
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from liber.errors import LiberError
from liber.interview import prompts
from liber.interview.brain import CONTEXT_TURNS, Brain, LLMError, Opening
from liber.interview.live import LiveClient, LiveConnection
from liber.interview.settings import InterviewSettings, interviews_dir
from liber.interview.transcript import AI, ME, TranscriptAssembler, dialogue, render_transcript, slugify
from liber.paths import free_name

log = logging.getLogger("liber.interview")

FINISHED_TURN_S = 1.5
MIN_FINISHED_WORDS = 3
CLOSE_WAIT_S = 10.0
HEARTBEAT_TIMEOUT_S = 60.0
WATCH_INTERVAL_S = 5.0
NOTE_MAX_CHARS = 2_000


@dataclass(frozen=True)
class InterviewResult:
    transcript: Path | None
    notes: Path | None


class InterviewSession:
    def __init__(
        self,
        *,
        vault: Path,
        settings: InterviewSettings,
        brain: Brain,
        live: LiveClient,
        opening: Opening,
        workdir: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = datetime.now,
        finished_turn_s: float = FINISHED_TURN_S,
        heartbeat_timeout_s: float = HEARTBEAT_TIMEOUT_S,
        watch_interval_s: float = WATCH_INTERVAL_S,
        close_wait_s: float = CLOSE_WAIT_S,
    ) -> None:
        self.vault = vault
        self.settings = settings
        self.brain = brain
        self.live = live
        self.opening = opening
        self.clock = clock
        self.now = now
        self.finished_turn_s = finished_turn_s
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.watch_interval_s = watch_interval_s
        self.close_wait_s = close_wait_s
        self.started_wall = now()
        self.id = f"{self.started_wall:%Y%m%d-%H%M%S}-{slugify(opening.topic)}"
        self.workdir = workdir or interviews_dir() / self.id
        self.assembler = TranscriptAssembler()
        self.coverage = ""
        self.state = "idle"
        self.session_ids: list[str] = []
        self.held = False
        self.message = ""
        self.result: InterviewResult | None = None
        self.done = asyncio.Event()
        self.warned = False
        self._conn: LiveConnection | None = None
        self._closed = asyncio.Event()
        self._ending = False
        self._generation = 0
        self._debounce: asyncio.Task | None = None
        self._llm_tasks: set[asyncio.Task] = set()
        self._background: set[asyncio.Task] = set()
        self._started_at: float | None = None
        self._session_started_at: float | None = None
        self._last_heartbeat = clock()
        self._past_seconds = 0.0
        self._session_seconds = 0.0
        self._watch_task: asyncio.Task | None = None

    # ---- lifecycle ----------------------------------------------------------------------------

    async def start(self, sdp: str) -> str:
        if self.state != "idle":
            raise LiberError("this interview has already started")
        instructions = prompts.interviewer_instructions(self.settings.name, self.opening.topic)
        session_id, answer = await self.live.create_session(
            sdp=sdp, instructions=instructions, voice=self.settings.voice
        )
        self._started_at = self.clock()
        self._last_heartbeat = self.clock()
        self._begin(session_id, prompts.opening_instruction(self.settings.name, self.opening.question))
        self._start_watch()
        return answer

    def _start_watch(self) -> None:
        """Task 7 replaces this with the heartbeat and time-limit watchdog."""

    def _begin(self, session_id: str, first_instruction: str) -> None:
        self.session_ids.append(session_id)
        self.state = "live"
        self.message = ""
        self.held = False
        self._closed = asyncio.Event()
        self._session_started_at = self.clock()
        self._session_seconds = 0.0
        self._spawn(self._background, self._run_sideband(session_id, first_instruction))
        self._save_draft()

    async def _run_sideband(self, session_id: str, first_instruction: str) -> None:
        try:
            async with self.live.attach(session_id) as conn:
                self._conn = conn
                if self._ending:
                    await self._safe(conn.close())
                    return
                await conn.instructions(first_instruction)
                while not self._closed.is_set():
                    event = await conn.recv()
                    if event is None:
                        break
                    try:
                        await self._handle(event)
                    except Exception as exc:  # one bad event must not end the interview
                        log.warning("ignored a malformed voice event (%s)", type(exc).__name__)
        except Exception as exc:  # any network or protocol failure ends this live session
            log.warning("voice connection ended (%s)", type(exc).__name__)
        finally:
            self._conn = None
            self._past_seconds += self._session_seconds
            self._session_seconds = 0.0
            self._closed.set()
            if not self._ending and self.state == "live":
                self.state = "interrupted"
                if not self.message:
                    self.message = "The connection to the voice service ended. Click Resume to continue."
                self._save_draft()

    async def _handle(self, event: dict) -> None:
        etype = event.get("type")
        if etype == "session.input_transcript.delta":
            self.assembler.add_delta(ME, str(event.get("delta", "")), int(event.get("start_ms", 0)), int(event.get("end_ms", 0)))
            self._schedule_finished_check()
        elif etype == "session.output_transcript.delta":
            self.assembler.add_delta(AI, str(event.get("delta", "")), int(event.get("start_ms", 0)), int(event.get("end_ms", 0)))
        elif etype == "session.delegation.created":
            delegation_id = (event.get("delegation") or {}).get("id")
            if delegation_id:
                self._spawn(self._llm_tasks, self._delegate(delegation_id))
        elif etype == "session.usage.updated":
            self._session_seconds = float((event.get("usage") or {}).get("seconds", self._session_seconds))
        elif etype == "session.closed":
            self._session_seconds = float((event.get("usage") or {}).get("seconds", self._session_seconds))
            if not self._ending:
                self.message = f"The voice session ended ({event.get('reason')}). Click Resume to continue."
            self._closed.set()
        elif etype == "error":
            log.warning("voice service error (%s)", (event.get("error") or {}).get("code"))

    # ---- Claude -------------------------------------------------------------------------------

    def _schedule_finished_check(self) -> None:
        if self._ending:
            return
        if self._debounce is not None:
            self._debounce.cancel()
        self._debounce = asyncio.create_task(self._after_pause())

    async def _after_pause(self) -> None:
        await asyncio.sleep(self.finished_turn_s)
        last_me = next((t for t in reversed(self.assembler.turns()) if t.speaker == ME), None)
        if last_me is None or last_me.words < MIN_FINISHED_WORDS:
            return
        self._save_draft()
        self._generation += 1
        self._spawn(self._llm_tasks, self._steer(self._generation))

    async def _steer(self, generation: int) -> None:
        hint = await self.brain.steer(dialogue(self.assembler.turns(), CONTEXT_TURNS), self.coverage)
        if hint is None or generation != self._generation or self._conn is None:
            return
        self.coverage = hint.coverage
        await self._safe(self._conn.thinking(hint.text))

    async def _delegate(self, delegation_id: str) -> None:
        text = await self.brain.answer_delegation(dialogue(self.assembler.turns(), CONTEXT_TURNS), self.coverage)
        if self._conn is not None:
            await self._safe(self._conn.commentary(text, delegation_id))

    # ---- controls -----------------------------------------------------------------------------

    def _now_ms(self) -> int:
        if self._session_started_at is None:
            return self.assembler.end_ms
        return self.assembler.offset_ms + int((self.clock() - self._session_started_at) * 1000)

    async def add_note(self, text: str) -> None:
        if self._ending:
            raise LiberError("the interview is ending")
        text = text.strip()
        if not text:
            raise LiberError("the note is empty")
        if len(text) > NOTE_MAX_CHARS:
            raise LiberError("the note is too long (2,000 characters at most)")
        self.assembler.add_note(text, self._now_ms())
        self._save_draft()
        if self._conn is not None:
            await self._safe(self._conn.thinking(prompts.typed_note_context(self.settings.name, text)))

    async def toggle_hold(self) -> bool:
        if self._conn is None:
            raise LiberError("not connected to the voice service")
        target = not self.held
        try:
            if target:
                await self._conn.mute()
                await self._conn.instructions(prompts.hold_on(self.settings.name))
            else:
                await self._conn.unmute()
                await self._conn.instructions(prompts.hold_off(self.settings.name))
        except Exception as exc:
            log.warning("hold failed (%s)", type(exc).__name__)
            raise LiberError("could not reach the voice service") from exc
        self.held = target
        return self.held

    def heartbeat(self) -> dict:
        self._last_heartbeat = self.clock()
        return self.status()

    def status(self) -> dict:
        elapsed = int(self.clock() - self._started_at) if self._started_at is not None else 0
        result = None
        if self.result is not None:
            result = {
                "transcript": str(self.result.transcript) if self.result.transcript else None,
                "notes": str(self.result.notes) if self.result.notes else None,
            }
        return {
            "state": self.state,
            "topic": self.opening.topic,
            "reason": self.opening.reason,
            "elapsed_s": elapsed,
            "max_minutes": self.settings.max_minutes,
            "held": self.held,
            "message": self.message,
            "result": result,
        }

    @property
    def voice_seconds(self) -> float:
        return self._past_seconds + self._session_seconds

    # ---- ending -------------------------------------------------------------------------------

    async def end(self, reason: str = "ended") -> InterviewResult:
        if self.state == "done" and self.result is not None:
            return self.result
        if self._ending:
            await self.done.wait()
            return self.result
        was_live = self.state == "live"
        self._ending = True
        self.state = "finishing"
        self.message = "Writing your transcript and notes…"
        log.info("ending interview (%s)", reason)
        try:
            if self._conn is not None:
                await self._safe(self._conn.close())
                try:
                    await asyncio.wait_for(self._closed.wait(), self.close_wait_s)
                except TimeoutError:
                    if self.session_ids:
                        await self.live.hangup(self.session_ids[-1])
            elif was_live:
                # ended before the sideband attached: hang up so the session cannot keep running
                if self.session_ids:
                    await self._safe(self.live.hangup(self.session_ids[-1]))
                me = asyncio.current_task()
                for task in list(self._background):
                    if task is not me:
                        task.cancel()
            if self._debounce is not None:
                self._debounce.cancel()
            for task in list(self._llm_tasks):
                task.cancel()
            self.result = await self.finalize()
        except Exception as exc:  # never leave end() hanging; the draft stays on disk
            log.warning("finalizing failed (%s)", type(exc).__name__)
            self.result = InterviewResult(None, None)
            self.message = "Saving failed; your draft is kept. Run: liber interview --recover"
        finally:
            self.state = "done"
            self.done.set()
        return self.result

    async def finalize(self) -> InterviewResult:
        turns = self.assembler.turns()
        if not any(t.speaker == ME for t in turns):
            self._discard_workdir()
            self.message = "Nothing was recorded, so no files were written."
            return InterviewResult(None, None)
        self._save_draft()
        day = self.started_wall.date()
        minutes = max(1, round(self.voice_seconds / 60)) if self.voice_seconds else max(1, round(self.assembler.end_ms / 60_000))
        inbox = self.vault / "inbox"
        inbox.mkdir(exist_ok=True)
        transcript_name = free_name(inbox, f"interview-{day.isoformat()}-{slugify(self.opening.topic)}.md")
        stem = transcript_name[: -len(".md")]
        notes_name = free_name(inbox, f"{stem}-notes.md")
        markdown = render_transcript(
            topic=self.opening.topic, day=day, minutes=minutes, voice=self.settings.voice,
            model=self.settings.model, notes_stem=notes_name[: -len(".md")], turns=turns,
        )
        transcript_path = inbox / transcript_name
        transcript_path.write_text(markdown, encoding="utf-8")
        notes_path: Path | None = None
        try:
            notes = await self.brain.write_notes(
                transcript_md=markdown, topic=self.opening.topic, day=day.isoformat(),
                minutes=minutes, transcript_stem=stem,
            )
            notes_path = inbox / notes_name
            notes_path.write_text(notes, encoding="utf-8")
            self.message = "Done. Your transcript and notes are in the inbox."
        except LLMError as exc:
            log.warning("notes failed (%s)", type(exc).__name__)
            self.message = f"Transcript saved; the notes failed. Retry with: liber interview --notes {transcript_path}"
        self._discard_workdir()
        return InterviewResult(transcript_path, notes_path)

    # ---- persistence and helpers --------------------------------------------------------------

    def state_dict(self) -> dict:
        return {
            "version": 1,
            "id": self.id,
            "topic": self.opening.topic,
            "reason": self.opening.reason,
            "question": self.opening.question,
            "started_at": self.started_wall.isoformat(),
            "session_ids": self.session_ids,
            "coverage": self.coverage,
            "voice_seconds": self.voice_seconds,
            "status": self.state,
            "assembler": self.assembler.to_state(),
        }

    def _save_draft(self) -> None:
        try:
            self.workdir.mkdir(parents=True, exist_ok=True)
            tmp = self.workdir / "state.json.tmp"
            tmp.write_text(json.dumps(self.state_dict()), encoding="utf-8")
            os.replace(tmp, self.workdir / "state.json")
            draft = render_transcript(
                topic=self.opening.topic, day=self.started_wall.date(), minutes=max(1, round(self.voice_seconds / 60)),
                voice=self.settings.voice, model=self.settings.model, notes_stem="(pending)", turns=self.assembler.turns(),
            )
            (self.workdir / "transcript.draft.md").write_text(draft, encoding="utf-8")
        except OSError as exc:
            log.warning("could not save the interview draft (%s)", type(exc).__name__)

    def _discard_workdir(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _spawn(self, bucket: set, coro: Coroutine) -> asyncio.Task:
        task = asyncio.create_task(coro)
        bucket.add(task)
        task.add_done_callback(bucket.discard)
        return task

    async def _safe(self, coro: Coroutine) -> None:
        try:
            await coro
        except Exception as exc:  # a failed send must never break the interview
            log.warning("voice service send failed (%s)", type(exc).__name__)
