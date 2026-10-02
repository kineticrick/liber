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


async def test_delegation_parallel_tool_uses_respect_cap(vault, monkeypatch):
    """Parallel tool_use blocks (multiple in one reply) respect the cap per call."""
    tool_call_count = 0
    original_run_tool = run_tool

    def tracked_run_tool(view, name, args):
        nonlocal tool_call_count
        tool_call_count += 1
        return original_run_tool(view, name, args)

    monkeypatch.setattr("liber.interview.brain.run_tool", tracked_run_tool)

    # First reply has 5 parallel tool_uses (t1..t5); second reply is text
    replies = iter([
        LLMResponse("tool_use", [
            {"type": "tool_use", "id": f"t{i}", "name": "search_user_knowledge", "input": {"query": "x"}}
            for i in range(1, 6)
        ]),
        text_reply("ok")
    ])
    brain = make_brain(vault, lambda c: next(replies))
    result = await brain.answer_delegation("dialogue", "coverage")
    assert result == "ok"
    # Only 3 tools were actually called
    assert tool_call_count == 3
    # Second call has 5 tool_results: 3 normal, 2 with "tool limit reached" error
    second_call_messages = brain.llm.calls[1]["messages"]
    tool_results = second_call_messages[2]["content"]
    assert len(tool_results) == 5
    normal_results = [r for r in tool_results if "is_error" not in r]
    error_results = [r for r in tool_results if r.get("is_error")]
    assert len(normal_results) == 3
    assert len(error_results) == 2
    assert all(r["content"] == "tool limit reached" for r in error_results)


async def test_unexpected_exceptions_in_delegation(vault):
    """Unexpected exceptions (not LLMError or TimeoutError) are caught and logged."""
    brain = make_brain(vault, lambda c: KeyError("boom"))
    result = await brain.answer_delegation("d", "c")
    assert result == prompts.FALLBACK_LINE


async def test_unexpected_exceptions_in_steer(vault):
    """Unexpected exceptions in steer are caught and logged."""
    brain = make_brain(vault, lambda c: KeyError("boom"))
    result = await brain.steer("d", "c")
    assert result is None


async def test_unexpected_exceptions_in_plan_opening(vault):
    """Unexpected exceptions in plan_opening are caught and logged."""
    brain = make_brain(vault, lambda c: KeyError("boom"), "career")
    result = await brain.plan_opening("career")
    assert result == Opening("career", "", "Let's talk about career. Where would you like to start?")
