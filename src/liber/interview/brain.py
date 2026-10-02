"""Claude's side of the interview: opening, steering hints, delegated answers and session notes."""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from liber.errors import LiberError
from liber.interview import prompts
from liber.interview.brief import INTERVIEW_CEILING, VaultBrief
from liber.interview.settings import InterviewSettings
from liber.server.knowledge import VaultView

log = logging.getLogger("liber.interview")

LIVE_TIMEOUT_S = 8.0
MAX_TOOL_CALLS = 3
HINT_MAX_CHARS = 400
COVERAGE_MAX_WORDS = 150
DELEGATION_MAX_WORDS = 60
CONTEXT_TURNS = 40
_OPENING_TIMEOUT_S = 30.0
_TOOL_READ_MAX_CHARS = 8_000

VAULT_TOOLS = [
    {
        "name": "search_user_knowledge",
        "description": "Keyword search across the user's knowledge base (personal level). Returns paths, titles and snippets.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "read_user_knowledge",
        "description": "Read one file from the user's knowledge base by its path.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
]


@dataclass(frozen=True)
class LLMResponse:
    stop_reason: str
    blocks: list[dict]

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.blocks if b.get("type") == "text").strip()

    @property
    def tool_uses(self) -> list[dict]:
        return [b for b in self.blocks if b.get("type") == "tool_use"]


class LLM(Protocol):
    async def complete(
        self, *, model: str, system: str, messages: list[dict], max_tokens: int,
        tools: list[dict] | None = None, tool_choice: dict | None = None,
    ) -> LLMResponse: ...


class LLMError(LiberError):
    """Claude could not answer."""


class AnthropicLLM:
    def __init__(self, api_key: str, *, http_client=None, request_timeout: float = 60.0):
        from anthropic import AsyncAnthropic

        kwargs = {"api_key": api_key, "max_retries": 0, "timeout": request_timeout}
        if http_client is not None:
            kwargs["http_client"] = http_client
        self._client = AsyncAnthropic(**kwargs)

    async def complete(self, *, model, system, messages, max_tokens, tools=None, tool_choice=None) -> LLMResponse:
        import anthropic

        kwargs = {"model": model, "system": system, "messages": messages, "max_tokens": max_tokens}
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        try:
            msg = await self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            raise LLMError(f"Claude request failed ({type(exc).__name__})") from exc
        blocks: list[dict] = []
        for block in msg.content:
            if block.type == "text":
                blocks.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                blocks.append({"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)})
        return LLMResponse(str(msg.stop_reason), blocks)


@dataclass(frozen=True)
class Opening:
    topic: str
    reason: str
    question: str


@dataclass(frozen=True)
class Hint:
    text: str
    coverage: str


def _json_object(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in reply")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("reply is not a JSON object")
    return data


def _clip_words(text: str, limit: int) -> str:
    return " ".join(text.split()[:limit])


def _clip_chars(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut or text[:limit]


def _default_question(topic: str) -> str:
    return f"Let's talk about {topic}. Where would you like to start?"


def run_tool(view: VaultView, name: str, args: dict) -> tuple[str, bool]:
    try:
        if name == "search_user_knowledge":
            return json.dumps(view.search(str(args.get("query", "")), limit=5)), False
        if name == "read_user_knowledge":
            return view.read(str(args.get("path", "")))[:_TOOL_READ_MAX_CHARS], False
    except LiberError as exc:
        return str(exc), True
    return f"unknown tool: {name}", True


def format_notes(body: str, *, topic: str, day: str, minutes: int, transcript_stem: str) -> str:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            current = match.group(1).strip().lower()
            sections.setdefault(current, [])
        elif current is not None and line.strip():
            sections[current].append(line.rstrip())
    parts = [
        "<!-- liber interview notes -->",
        f"# Interview notes — {topic} — {day} ({minutes} min)",
        "",
        f"Source: [[{transcript_stem}]]",
        "",
    ]
    for title in prompts.NOTES_SECTIONS:
        parts += [f"## {title}", *(sections.get(title.lower()) or ["- none"]), ""]
    return "\n".join(parts).rstrip() + "\n"


class Brain:
    def __init__(self, llm: LLM, settings: InterviewSettings, vault: Path, brief: VaultBrief):
        self.llm = llm
        self.settings = settings
        self.vault = vault
        self.brief = brief

    async def plan_opening(self, topic: str | None) -> Opening:
        ask = f"The user chose the topic: {topic}" if topic else "The user asked you to choose the topic."
        try:
            reply = await asyncio.wait_for(
                self.llm.complete(
                    model=self.settings.model, system=prompts.OPENING_SYSTEM, max_tokens=400,
                    messages=[{"role": "user", "content": f"{self.brief.render()}\n\n{ask}"}],
                ),
                _OPENING_TIMEOUT_S,
            )
            data = _json_object(reply.text)
        except (LLMError, TimeoutError, ValueError) as exc:
            log.warning("opening plan failed (%s); using a default", type(exc).__name__)
            chosen = topic or "my life story"
            return Opening(chosen, "", _default_question(chosen))
        chosen = topic or str(data.get("topic", "")).strip() or "my life story"
        question = str(data.get("question", "")).strip() or _default_question(chosen)
        reason = "" if topic else str(data.get("reason", "")).strip()
        return Opening(chosen, reason, question)

    async def steer(self, dialogue_text: str, coverage: str) -> Hint | None:
        content = (
            f"{self.brief.render()}\n\n## Coverage so far\n{coverage or '(nothing yet)'}\n\n"
            f"## Conversation (most recent last)\n{dialogue_text}"
        )
        try:
            reply = await asyncio.wait_for(
                self.llm.complete(
                    model=self.settings.model, system=prompts.STEER_SYSTEM, max_tokens=500,
                    messages=[{"role": "user", "content": content}],
                ),
                LIVE_TIMEOUT_S,
            )
            data = _json_object(reply.text)
        except (LLMError, TimeoutError, ValueError) as exc:
            log.warning("steering skipped (%s)", type(exc).__name__)
            return None
        hint = _clip_chars(str(data.get("hint", "")), HINT_MAX_CHARS)
        new_coverage = _clip_words(str(data.get("coverage", "")), COVERAGE_MAX_WORDS) or coverage
        return Hint(hint, new_coverage) if hint else None

    async def answer_delegation(self, dialogue_text: str, coverage: str) -> str:
        try:
            text = await asyncio.wait_for(self._delegation_loop(dialogue_text, coverage), LIVE_TIMEOUT_S)
        except (LLMError, TimeoutError) as exc:
            log.warning("delegation fell back (%s)", type(exc).__name__)
            return prompts.FALLBACK_LINE
        return _clip_words(text, DELEGATION_MAX_WORDS) or prompts.FALLBACK_LINE

    async def _delegation_loop(self, dialogue_text: str, coverage: str) -> str:
        view = VaultView(self.vault, INTERVIEW_CEILING)
        messages: list[dict] = [{
            "role": "user",
            "content": (
                f"{self.brief.render()}\n\n## Coverage so far\n{coverage or '(nothing yet)'}\n\n"
                f"## Conversation (most recent last)\n{dialogue_text}\n\nWhat should the interviewer say next?"
            ),
        }]
        tool_calls = 0
        while True:
            exhausted = tool_calls >= MAX_TOOL_CALLS
            reply = await self.llm.complete(
                model=self.settings.model, system=prompts.DELEGATION_SYSTEM, messages=messages, max_tokens=300,
                tools=VAULT_TOOLS, tool_choice={"type": "none"} if exhausted else None,
            )
            uses = reply.tool_uses
            if exhausted or reply.stop_reason != "tool_use" or not uses:
                return reply.text
            messages.append({"role": "assistant", "content": reply.blocks})
            results = []
            for use in uses:
                tool_calls += 1
                content, is_error = run_tool(view, use.get("name", ""), use.get("input") or {})
                result = {"type": "tool_result", "tool_use_id": use["id"], "content": content}
                if is_error:
                    result["is_error"] = True
                results.append(result)
            messages.append({"role": "user", "content": results})

    async def write_notes(self, *, transcript_md: str, topic: str, day: str, minutes: int, transcript_stem: str) -> str:
        content = f"{self.brief.render()}\n\n## Transcript\n\n{transcript_md}"
        last_error: Exception | None = None
        for _ in range(2):
            try:
                reply = await self.llm.complete(
                    model=self.settings.notes_model, system=prompts.NOTES_SYSTEM, max_tokens=4000,
                    messages=[{"role": "user", "content": content}],
                )
                return format_notes(reply.text, topic=topic, day=day, minutes=minutes, transcript_stem=transcript_stem)
            except LLMError as exc:
                last_error = exc
                log.warning("notes attempt failed (%s)", type(exc).__name__)
        raise LLMError("could not write the session notes") from last_error
