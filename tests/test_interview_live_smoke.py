"""Real paid API calls (a few cents). Run only with: uv run pytest -m live"""

import pytest

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_keys_and_model_access():
    from openai import AsyncOpenAI

    from liber.interview.brain import AnthropicLLM
    from liber.interview.settings import load_voice_keys

    keys = load_voice_keys()
    model = await AsyncOpenAI(api_key=keys.openai).models.retrieve("gpt-live-1")
    assert model.id == "gpt-live-1"
    reply = await AnthropicLLM(keys.anthropic).complete(
        model="claude-sonnet-5-5", system="Reply with the single word OK.",
        messages=[{"role": "user", "content": "Ping"}], max_tokens=5)
    assert "OK" in reply.text.upper()
