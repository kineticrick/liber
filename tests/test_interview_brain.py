import asyncio
import json

import pytest

from helpers import write
from liber.interview import prompts
from liber.interview.brain import (
    DELEGATION_MAX_WORDS, HINT_MAX_CHARS, Brain, LLMError, LLMResponse, Opening, format_notes, run_tool,
)
from liber.interview.brief import build_brief
from liber.interview.settings import InterviewSettings
from liber.server.knowledge import VaultView
from voice_fakes import FakeLLM, text_reply, tool_reply

pytestmark = pytest.mark.anyio
SETTINGS = InterviewSettings("Rick")


def fm(type, sens, body):
    return f"---\ntype: {type}\nupdated: 2026-09-30\nsensitivity: {sens}\ntags: []\n---\n\n{body}\n"


def make_brain(vault, responder, topic="my career"):
    return Brain(FakeLLM(responder), SETTINGS, vault, build_brief(vault, topic))


async def test_plan_opening_with_chosen_topic(vault):
    brain = make_brain(vault, lambda c: text_reply(json.dumps(
        {"topic": "ignored", "reason": "", "question": "How did it start?"})))
    assert await brain.plan_opening("my career") == Opening("my career", "", "How did it start?")
    call = brain.llm.calls[0]
    assert call["system"] == prompts.OPENING_SYSTEM and call["model"] == "claude-sonnet-5-5"
    assert "The user chose the topic: my career" in call["messages"][0]["content"]


async def test_plan_opening_choose_and_fallback(vault):
    brain = make_brain(vault, lambda c: text_reply('Sure! {"topic": "early life", "reason": "Thin.", "question": "Where did you grow up?"}'), None)
    assert await brain.plan_opening(None) == Opening("early life", "Thin.", "Where did you grow up?")
    broken = make_brain(vault, lambda c: LLMError("down"), None)
    opening = await broken.plan_opening(None)
    assert opening.topic == "my life story" and opening.question


async def test_steer_returns_clipped_hint(vault):
    long_hint = "word " * 200
    brain = make_brain(vault, lambda c: text_reply(json.dumps({"hint": long_hint, "coverage": "acme; payments"})))
    hint = await brain.steer("Me [00:01]: I worked at Acme.", "")
    assert hint is not None and len(hint.text) <= HINT_MAX_CHARS and hint.coverage == "acme; payments"
    assert brain.llm.calls[0]["system"] == prompts.STEER_SYSTEM


async def test_steer_failures_return_none(vault):
    assert await make_brain(vault, lambda c: LLMError("x")).steer("d", "c") is None
    assert await make_brain(vault, lambda c: text_reply("not json")).steer("d", "c") is None


async def test_steer_timeout_returns_none(vault, monkeypatch):
    # Review Focus 3
    monkeypatch.setattr("liber.interview.brain.LIVE_TIMEOUT_S", 0.05)

    async def slow():
        await asyncio.sleep(1)
        return text_reply("{}")

    assert await make_brain(vault, lambda c: slow()).steer("d", "c") is None


async def test_delegation_with_tool_use(vault):
    write(vault / "career" / "acme.md", fm("career", "personal", "# Acme\nPayments lead."))
    replies = iter([tool_reply("search_user_knowledge", {"query": "acme"}), text_reply("You led payments at Acme. What came next?")])
    brain = make_brain(vault, lambda c: next(replies))
    assert await brain.answer_delegation("dialogue", "coverage") == "You led payments at Acme. What came next?"
    second = brain.llm.calls[1]["messages"]
    assert second[1]["role"] == "assistant" and second[2]["content"][0]["type"] == "tool_result"
    assert "career/acme.md" in second[2]["content"][0]["content"]


async def test_delegation_tool_cap_forces_text(vault):
    calls = []

    def responder(call):
        calls.append(call)
        if call["tool_choice"] == {"type": "none"}:
            return text_reply("Tell me more.")
        return tool_reply("search_user_knowledge", {"query": "x"}, id=f"t{len(calls)}")

    brain = make_brain(vault, responder)
    assert await brain.answer_delegation("d", "c") == "Tell me more."
    assert len(calls) == 4 and calls[-1]["tool_choice"] == {"type": "none"}


async def test_delegation_clips_and_falls_back(vault, monkeypatch):
    long = make_brain(vault, lambda c: text_reply("word " * 100))
    assert len((await long.answer_delegation("d", "c")).split()) == DELEGATION_MAX_WORDS
    assert await make_brain(vault, lambda c: LLMError("x")).answer_delegation("d", "c") == prompts.FALLBACK_LINE
    assert await make_brain(vault, lambda c: text_reply("")).answer_delegation("d", "c") == prompts.FALLBACK_LINE
    monkeypatch.setattr("liber.interview.brain.LIVE_TIMEOUT_S", 0.05)

    async def slow():
        await asyncio.sleep(1)
        return text_reply("late")

    assert await make_brain(vault, lambda c: slow()).answer_delegation("d", "c") == prompts.FALLBACK_LINE


def test_tools_never_expose_private(vault):
    # Review Focus 1
    write(vault / "core" / "secret.md", fm("core", "private", "PRIVATEMARK"))
    view = VaultView(vault, "personal")
    content, is_error = run_tool(view, "read_user_knowledge", {"path": "core/secret.md"})
    assert is_error and "not found" in content and "PRIVATEMARK" not in content
    content, _ = run_tool(view, "search_user_knowledge", {"query": "privatemark"})
    assert json.loads(content) == []
    assert run_tool(view, "nope", {}) == ("unknown tool: nope", True)
    content, is_error = run_tool(view, "search_user_knowledge", {"query": " "})
    assert is_error


def test_format_notes_canonical_sections():
    body = "## New facts\n- I led payments [12:40]\n\n## People mentioned\n- [[Priya Nair]]: manager [20:15]\n## Extra\n- ignored\n"
    notes = format_notes(body, topic="career", day="2026-10-02", minutes=38, transcript_stem="interview-2026-10-02-career")
    assert notes == (
        "<!-- liber interview notes -->\n"
        "# Interview notes — career — 2026-10-02 (38 min)\n\n"
        "Source: [[interview-2026-10-02-career]]\n\n"
        "## New facts\n- I led payments [12:40]\n\n"
        "## Corrections to the vault\n- none\n\n"
        "## People mentioned\n- [[Priya Nair]]: manager [20:15]\n\n"
        "## Preferences, values and feelings\n- none\n\n"
        "## Follow-up questions\n- none\n"
    )


async def test_write_notes_retries_once(vault):
    attempts = []

    def responder(call):
        attempts.append(call)
        return LLMError("x") if len(attempts) == 1 else text_reply("## New facts\n- A [00:01]")

    brain = make_brain(vault, responder)
    notes = await brain.write_notes(transcript_md="T", topic="t", day="2026-10-02", minutes=1, transcript_stem="s")
    assert "- A [00:01]" in notes and attempts[1]["model"] == "claude-opus-5-5"
    assert attempts[1]["system"] == prompts.NOTES_SYSTEM
    with pytest.raises(LLMError):
        await make_brain(vault, lambda c: LLMError("x")).write_notes(
            transcript_md="T", topic="t", day="d", minutes=1, transcript_stem="s")


def test_llm_response_helpers():
    r = LLMResponse("tool_use", [{"type": "text", "text": " a "}, {"type": "tool_use", "id": "1", "name": "n", "input": {}}])
    assert r.text == "a" and r.tool_uses == [{"type": "tool_use", "id": "1", "name": "n", "input": {}}]


async def test_anthropic_llm_against_mock_transport():
    import httpx2

    from liber.interview.brain import AnthropicLLM

    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [{"type": "text", "text": "hi"}, {"type": "tool_use", "id": "tu", "name": "n", "input": {"q": 1}}],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1}})

    llm = AnthropicLLM("sk-test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    r = await llm.complete(model="claude-sonnet-5-5", system="S", messages=[{"role": "user", "content": "x"}],
                           max_tokens=10, tools=[{"name": "n", "description": "d", "input_schema": {"type": "object"}}],
                           tool_choice={"type": "none"})
    assert r.stop_reason == "tool_use"
    assert r.blocks == [{"type": "text", "text": "hi"}, {"type": "tool_use", "id": "tu", "name": "n", "input": {"q": 1}}]
    assert seen["body"]["system"] == "S" and seen["body"]["tool_choice"] == {"type": "none"}

    failing = AnthropicLLM("sk-test", http_client=httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda r: httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}))))
    with pytest.raises(LLMError):
        await failing.complete(model="m", system="s", messages=[{"role": "user", "content": "x"}], max_tokens=5)
