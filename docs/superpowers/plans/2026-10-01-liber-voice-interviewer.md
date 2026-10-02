# liber Voice Interviewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `liber interview [TOPIC]` runs a local, full-duplex voice interview. GPT-Live-1 handles the voice in a browser tab, and Claude is the brain, reading the vault at the `personal` ceiling. The interview leaves a transcript and session notes in `inbox/` for `/ingest`.

**Architecture:** A new `liber.interview` package.
- The browser does audio over WebRTC, which gives echo cancellation.
- A local Starlette app on `127.0.0.1`, protected by a per-session token, proxies the SDP offer to OpenAI. It also serves the controls.
- `InterviewSession` attaches to the OpenAI sideband. It assembles the transcript, runs Claude steering and delegation through `Brain`, keeps a resumable draft on disk, and finalizes the transcript and notes into the vault inbox.
- The OpenAI and Anthropic SDKs sit behind small interfaces (`LiveClient`, `LLM`), so every test runs offline with fakes.

**Tech Stack:**
- Python 3.12 and uv.
- `openai[realtime]==3.23.0` (Live API: `client.live.create`, `client.live.sideband.connect`) and `anthropic==1.11.0`, both in the optional `voice` extra.
- Starlette and uvicorn, already present via fastmcp.
- Vanilla JS with WebRTC.
- pytest with anyio.

**Spec:** `docs/superpowers/specs/2026-10-01-liber-voice-interviewer-design.md`

**Verified facts.** These come from the SDK source and offline checks (2026-10-01), plus the owner's live go/no-go spike. They override the spec where they differ.

1. **Session creation** is `await client.live.create(session={...}, transport={"type": "webrtc", "sdp": offer})`.
   - It returns `.session.id` and `.transport.sdp`.
   - The voice goes in `session.audio.output.voice`; there is no top-level voice field.
   - The seed messages go in `session.input`: `[{"type": "message", "role": "developer", "content": [{"type": "input_text", "text": ...}]}]`.
   - Errors are `openai.APIStatusError` (with `.status_code` and `.message`) and `openai.APIError`.
2. **The sideband** is `client.live.sideband.connect(session_id=...)`, used as an async context manager.
   - Receive with `recv_bytes()` plus `json.loads`, and **dispatch on the `type` string**. Typed parsing mis-reads unknown types.
   - The sideband also carries reflected audio (`session.input_audio.append` and `session.output_audio.delta`, about 64 KB/s). Skip it and never log it.
   - A dropped socket raises `websockets.exceptions.ConnectionClosed`. There is no automatic reconnect.
   - Sending:
     - `conn.session.commentary.append(content=, delegation_id=, event_id=)`
     - `thinking.append(content=, delegation_id=None, event_id=)`
     - `instructions.append(content=, delegation_id=None, event_id=)`
     - `input_audio.mute(event_id=)`, `input_audio.unmute(event_id=)`
     - `session.close(event_id=)`
     - `delegation_id` is a required keyword.
   - Hang up with `client.live.sessions.hangup(session_id)`.
3. **Events** (confirmed live by the spike):
   - `session.started` has `session.expires_at`.
   - `session.input_transcript.delta` and `session.output_transcript.delta` have `delta`, `start_ms` and `end_ms`. Deltas carry a leading space, e.g. `" Hi Rick"`.
   - `session.delegation.created` has `offset_ms` and `delegation.id`.
   - `session.{commentary,thinking,instructions}.appended` are acknowledgements.
   - `session.usage.updated` has `usage.seconds` (cumulative).
   - `session.closed` has `reason`, one of `close_requested`, `expired`, `content`, `remote_hangup` or `connection_lost`, plus `usage.seconds`.
   - `error` has `error.code` and `error.message`.
4. **The model does not speak first.** Right after attaching, send `instructions.append` (with `delegation_id: None`) containing the greeting and the opening question. The spike confirmed this works.
5. **Behaviour observed in the live spike:**
   - Deliberate 10-second pauses were respected.
   - Interruptions and revisits worked.
   - Hold (mute plus an instruction) worked.
   - There was no echo on speakers.
   - Delegation acknowledgements came back in about 0.5 s, and commentary was paraphrased.
   - The voice delegated only once in about 7 minutes, so background steering through `thinking.append` is the main way Claude influences the interview.
6. **Anthropic 1.11.0:**
   - `AsyncAnthropic(api_key=, max_retries=0, timeout=, http_client=)`.
   - `messages.create(model, system, messages, max_tokens, tools=, tool_choice=)`. There is no `temperature`.
   - The response has `.stop_reason` and `.content` blocks: `text` (with `.text`) and `tool_use` (with `.id`, `.name` and `.input`).
   - All errors subclass `anthropic.APIError`.
   - `timeout=` applies per HTTP attempt, so use `asyncio.wait_for` for an overall budget.
7. **Hold replaces the spec's separate Mute button.** It mutes the microphone and also tells the voice to wait, and the spike validated it. It is the only mute control on the page.
8. **Both SDKs use `httpx2`.** SDK-level mocks use `httpx2.MockTransport` passed as `http_client=`. The sideband points at a local websockets server through `AsyncOpenAI(websocket_base_url="ws://127.0.0.1:PORT/v1")`.

## Global Constraints

- **Dependencies.** The optional extra is `voice = ["openai[realtime]==3.23.0", "anthropic==1.11.0"]`, pinned exactly because the Live API is new. The same two pins go into the `dev` dependency group. Add no other dependencies; if `websockets` is not pulled in by `openai[realtime]`, add `websockets>=15` to both.
- **Interviewer vault ceiling:** `"personal"`. Use `liber.server.knowledge.VaultView(vault, "personal")` everywhere in `liber.interview`. Never read vault files directly.
- **Models:**
  - Live voice model: `"gpt-live-1"`. Default voice: `"marin"`.
  - Claude: `"claude-sonnet-5-5"` for live turns and `"claude-opus-5-5"` for notes.
  - Configure these through `[interview]` in `config.toml`.
- **Live turn budget:** steering and delegation each get an 8-second overall budget (`asyncio.wait_for`).
  - Delegation fallback line: `"Let's keep going — tell me more about that."`
  - Steering failures are silent (skipped and logged).
- **Limits:**
  - At most 3 vault tool calls per delegation.
  - Hints: ≤ 400 characters. Coverage: ≤ 150 words. Delegation text: ≤ 60 words. Typed notes: ≤ 2,000 characters.
  - Brief: ≤ 24,000 characters.
  - Transcript context sent to Claude: the last 40 turns.
- **Timing:**
  - A turn breaks after a same-speaker gap of at least 2,500 ms.
  - A user turn counts as finished after 1.5 s without a user delta, if it has at least 3 words.
  - Time limit `max_minutes` (default 45), with a warning `warn_minutes` (default 5) before.
  - Heartbeat timeout 60 s.
  - Close wait 10 s.
- **Files:**
  - Draft and resume state: `<data_dir>/interviews/<id>/state.json` and `transcript.draft.md`, where `<data_dir>` is `liber.server.settings.data_dir()`.
  - Final transcript: `<vault>/inbox/interview-<YYYY-MM-DD>-<slug>.md`.
  - Final notes: `<vault>/inbox/<transcript stem>-notes.md`.
  - Use `liber.paths.free_name` for collision suffixes.
- **Keys:** `openai_api_key` and `anthropic_api_key` live in `~/.config/liber/secrets.toml` (mode 0600), shared with the server secrets. The environment variables `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` override them. `liber.server.settings.write_secrets` must preserve keys it doesn't own.
- **Local server:**
  - Binds to `127.0.0.1` only.
  - Every `/api/*` route requires the header `X-Liber-Token: <token>`, otherwise it returns 403.
  - API keys never appear in any response or static asset.
- **Logging:** never log transcript text, note text, hints or Claude output; log event types, sizes and error class names only. The logger name is `"liber.interview"`.
- **Tests:**
  - Offline. Use `pytestmark = pytest.mark.anyio` for async tests; the existing `anyio_backend` fixture is in `tests/conftest.py`.
  - The real-API smoke test carries `@pytest.mark.live`, and live tests are deselected by default.
  - Run with `uv run pytest`.
- **CLI failures** print `error: <message>` and exit 1 via `handle_errors()`.
- **Commit messages** end with these two lines:
  ```
  Co-Authored-By: <your model name> <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV
  ```

## Review Focus

1. **A `private` vault file must never reach OpenAI or Claude.** This applies to the brief, steering, delegation tool results and notes input. Tests are in Task 3 (brief) and Task 4 (tools).
2. **Closing the browser tab mid-interview** (no heartbeat for 60 s) must still produce the transcript and notes in `inbox/`. Test in Task 7.
3. **A slow or failing Claude must never stall the conversation.** Delegation must answer with the fallback within the budget, and steering must be skipped. Tests in Task 4 and Task 6.
4. **Rapid answers must not deliver stale hints.** When a newer user turn has finished, the older steering result is dropped. Test in Task 6.
5. **An interview where the owner never spoke** (connected and then ended) must write no files to `inbox/`. Test in Task 6.

## File Structure

```
pyproject.toml                          + optional extra `voice`, dev pins, `live` marker
src/liber/server/settings.py            + read_secret_values / write_secret_values; write_secrets preserves foreign keys
src/liber/interview/__init__.py
src/liber/interview/settings.py         InterviewSettings, VoiceKeys, load/save, interviews_dir
src/liber/interview/transcript.py       Fragment, Turn, TranscriptAssembler, mmss, slugify, dialogue, render_transcript
src/liber/interview/brief.py            VaultBrief, build_brief (personal ceiling)
src/liber/interview/prompts.py          voice instructions and Claude system prompts
src/liber/interview/brain.py            LLMResponse, LLM, LLMError, AnthropicLLM, Opening, Hint, Brain, format_notes
src/liber/interview/live.py             LiveError, LiveConnection, LiveClient, OpenAILiveClient
src/liber/interview/session.py          InterviewResult, InterviewSession (+ from_state), recover/notes/continue helpers
src/liber/interview/web.py              create_app (Starlette)
src/liber/interview/static/             index.html, app.js, style.css
src/liber/interview/runner.py           require_voice_extra, run_interview, setup/recover/notes entry points
src/liber/cli.py                        + `interview` command
tests/voice_fakes.py                    FakeLLM, FakeConnection, FakeLiveClient, helpers
tests/test_interview_*.py               one file per module
docs/manual-test/INTERVIEW-CHECKLIST.md
README.md                               + "Voice interviews"
```

---

### Task 1: Voice extra, interview settings and keys

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/liber/server/settings.py` (the `write_secrets` function, plus two new helpers)
- Create: `src/liber/interview/__init__.py`, `src/liber/interview/settings.py`
- Test: `tests/test_interview_settings.py`, plus one new test in `tests/test_server_settings.py`

**Interfaces:**
- Consumes:
  - `liber.config.user_config_path()`, `liber.errors.LiberError`
  - `liber.server.settings.data_dir()`, `secrets_path()`, `write_private_file(path, text)`, `SECRET_KEYS`, `Secrets`, `load_secrets()`
- Produces:
  - `liber.server.settings.read_secret_values() -> dict[str, str]`
  - `liber.server.settings.write_secret_values(values: dict[str, str]) -> Path`
  - `liber.interview.settings`:
    - constants `DEFAULT_MODEL`, `DEFAULT_NOTES_MODEL`, `DEFAULT_VOICE`, `DEFAULT_MAX_MINUTES`, `DEFAULT_WARN_MINUTES`
    - `@dataclass(frozen=True) InterviewSettings(name, model, notes_model, voice, max_minutes, warn_minutes)`
    - `@dataclass(frozen=True) VoiceKeys(openai, anthropic)`
    - `interviews_dir() -> Path`
    - `load_interview_settings() -> InterviewSettings`
    - `load_voice_keys() -> VoiceKeys`
    - `save_voice_setup(name, openai_key, anthropic_key) -> None`

- [ ] **Step 1: Update pyproject**

In `pyproject.toml`:
- Add this table, after the `[project]` table's `dependencies`:
```toml
[project.optional-dependencies]
voice = ["openai[realtime]==3.23.0", "anthropic==1.11.0"]
```
- Change the `dev` group to:
```toml
[dependency-groups]
dev = ["pytest>=8", "python-docx>=1.1", "openai[realtime]==3.23.0", "anthropic==1.11.0"]
```
- Change `[tool.pytest.ini_options]` to:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["live: makes real paid API calls; run with `uv run pytest -m live`"]
addopts = "-m 'not live'"
```
Run `uv sync`, then:
`uv run python -c "import openai, anthropic, websockets; print(openai.__version__, anthropic.__version__)"`
Expected: `3.23.0 1.11.0`. If `websockets` fails to import, add `"websockets>=15"` to both the `voice` extra and the `dev` group, then run `uv sync` again.

- [ ] **Step 2: Isolate the voice keys in tests**

In `tests/conftest.py`, change the `isolated_env` fixture's signature to `def isolated_env(monkeypatch, tmp_path_factory, request):`. Make its first lines:
```python
    if request.node.get_closest_marker("live"):
        return None  # live smoke tests deliberately use the owner's real config and keys
```
Just before its `return home`, add:
```python
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
```

- [ ] **Step 3: Write failing tests**

Append to `tests/test_server_settings.py`:
```python
def test_write_secrets_preserves_foreign_keys():
    from liber.server.settings import read_secret_values, write_secret_values

    write_secret_values({"openai_api_key": "sk-o", "anthropic_api_key": "sk-a"})
    write_secrets(Secrets("cid", "csecret", new_jwt_signing_key(), new_storage_key()))
    values = read_secret_values()
    assert values["openai_api_key"] == "sk-o" and values["anthropic_api_key"] == "sk-a"
    assert values["github_client_id"] == "cid"
    assert load_secrets().github_client_id == "cid"
    assert stat.S_IMODE(secrets_path().stat().st_mode) == 0o600
```

`tests/test_interview_settings.py`:
```python
import os
import stat

import pytest

from helpers import write
from liber.config import user_config_path, write_user_config
from liber.errors import LiberError
from liber.interview.settings import (
    InterviewSettings, VoiceKeys, interviews_dir, load_interview_settings, load_voice_keys, save_voice_setup,
)
from liber.server.settings import read_secret_values, secrets_path, write_secret_values


def test_interviews_dir(isolated_env):
    assert interviews_dir() == isolated_env / ".local" / "share" / "liber" / "interviews"


def test_settings_defaults():
    write(user_config_path(), '[interview]\nname = "Rick"\n')
    assert load_interview_settings() == InterviewSettings(
        "Rick", "claude-sonnet-5-5", "claude-opus-5-5", "marin", 45, 5)


def test_settings_overrides():
    write(user_config_path(), '[interview]\nname = " Rick "\nmodel = "claude-opus-5-5"\nvoice = "quartz"\n'
                              'max_minutes = 30\nwarn_minutes = 3\n')
    s = load_interview_settings()
    assert (s.name, s.model, s.voice, s.max_minutes, s.warn_minutes) == ("Rick", "claude-opus-5-5", "quartz", 30, 3)


def test_missing_name_points_to_setup():
    with pytest.raises(LiberError, match="liber interview --setup"):
        load_interview_settings()


@pytest.mark.parametrize("body, match", [
    ('name = "R"\nmax_minutes = 1', "max_minutes"),
    ('name = "R"\nwarn_minutes = 0', "warn_minutes"),
    ('name = "R"\nmax_minutes = 10\nwarn_minutes = 10', "smaller"),
    ('name = "R"\nvoice = ""', "voice"),
    ('name = "R"\nmax_minutes = true', "max_minutes"),
])
def test_invalid_settings(body, match):
    write(user_config_path(), f"[interview]\n{body}\n")
    with pytest.raises(LiberError, match=match):
        load_interview_settings()


def test_keys_from_secrets_and_env(monkeypatch):
    write_secret_values({"openai_api_key": "sk-o", "anthropic_api_key": "sk-a"})
    assert load_voice_keys() == VoiceKeys("sk-o", "sk-a")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    assert load_voice_keys() == VoiceKeys("sk-env", "sk-a")


def test_missing_keys(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LiberError, match="OpenAI and Anthropic"):
        load_voice_keys()
    write_secret_values({"openai_api_key": "sk-o"})
    with pytest.raises(LiberError, match="Anthropic API key"):
        load_voice_keys()


def test_open_secret_permissions_refused(monkeypatch):
    path = write_secret_values({"openai_api_key": "sk-o", "anthropic_api_key": "sk-a"})
    os.chmod(path, 0o644)
    with pytest.raises(LiberError, match="chmod 600"):
        load_voice_keys()


def test_save_voice_setup_merges(vault):
    write_user_config(vault)
    write_secret_values({"github_client_id": "cid"})
    save_voice_setup(" Rick ", " sk-o ", "sk-a")
    values = read_secret_values()
    assert values == {"github_client_id": "cid", "openai_api_key": "sk-o", "anthropic_api_key": "sk-a"}
    assert stat.S_IMODE(secrets_path().stat().st_mode) == 0o600
    assert load_interview_settings().name == "Rick"
    assert f'vault = "{vault.resolve()}"' in user_config_path().read_text()
    save_voice_setup("Someone Else", "sk-o2", "sk-a2")  # existing [interview] name is kept
    assert load_interview_settings().name == "Rick"
    assert read_secret_values()["openai_api_key"] == "sk-o2"


def test_save_voice_setup_requires_values_and_config(vault):
    with pytest.raises(LiberError, match="liber init"):
        save_voice_setup("Rick", "sk-o", "sk-a")
    write_user_config(vault)
    with pytest.raises(LiberError, match="required"):
        save_voice_setup("Rick", "", "sk-a")
```

Run: `uv run pytest tests/test_interview_settings.py tests/test_server_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'liber.interview'` and `ImportError` for `read_secret_values`.

- [ ] **Step 4: Implement**

In `src/liber/server/settings.py`, add these two functions directly above `write_secrets`, and replace the whole existing `write_secrets` function with the version below:
```python
def read_secret_values() -> dict[str, str]:
    """Every string value in secrets.toml, unvalidated. Empty if the file is absent or unreadable."""
    path = secrets_path()
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, str)}


def write_secret_values(values: dict[str, str]) -> Path:
    """Replace secrets.toml with `values` (atomic, mode 0600). JSON string escaping is valid TOML."""
    path = secrets_path()
    write_private_file(path, "".join(f"{key} = {json.dumps(value)}\n" for key, value in values.items()))
    return path


def write_secrets(secrets: Secrets) -> Path:
    """Write the server secrets, keeping any other keys already in the file (e.g. voice API keys)."""
    values = read_secret_values()
    values.update({key: getattr(secrets, key) for key in SECRET_KEYS})
    return write_secret_values(values)
```

`src/liber/interview/__init__.py`:
```python
"""liber's local voice interviewer (GPT-Live-1 voice + Claude brain)."""
```

`src/liber/interview/settings.py`:
```python
"""Voice interview settings ([interview] in config.toml) and API keys (secrets.toml or environment)."""

import json
import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

from liber.config import user_config_path
from liber.errors import LiberError
from liber.server.settings import data_dir, read_secret_values, secrets_path, write_secret_values

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_NOTES_MODEL = "claude-opus-5-5"
DEFAULT_VOICE = "marin"
DEFAULT_MAX_MINUTES = 45
DEFAULT_WARN_MINUTES = 5
_SETUP_HINT = "Run `liber interview --setup`."


@dataclass(frozen=True)
class InterviewSettings:
    name: str
    model: str = DEFAULT_MODEL
    notes_model: str = DEFAULT_NOTES_MODEL
    voice: str = DEFAULT_VOICE
    max_minutes: int = DEFAULT_MAX_MINUTES
    warn_minutes: int = DEFAULT_WARN_MINUTES


@dataclass(frozen=True)
class VoiceKeys:
    openai: str
    anthropic: str


def interviews_dir() -> Path:
    return data_dir() / "interviews"


def _config() -> dict:
    path = user_config_path()
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise LiberError(f"{path} is not valid TOML: {exc}") from exc


def load_interview_settings() -> InterviewSettings:
    raw = _config().get("interview")
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw["name"].strip():
        raise LiberError(f"no interview name is configured. {_SETUP_HINT}")

    def text(key: str, default: str) -> str:
        value = raw.get(key, default)
        if not isinstance(value, str) or not value.strip():
            raise LiberError(f"[interview] {key} must be a non-empty string")
        return value.strip()

    def minutes(key: str, default: int, low: int) -> int:
        value = raw.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < low:
            raise LiberError(f"[interview] {key} must be a whole number of at least {low}")
        return value

    max_minutes = minutes("max_minutes", DEFAULT_MAX_MINUTES, 2)
    warn_minutes = minutes("warn_minutes", DEFAULT_WARN_MINUTES, 1)
    if warn_minutes >= max_minutes:
        raise LiberError("[interview] warn_minutes must be smaller than max_minutes")
    return InterviewSettings(
        raw["name"].strip(), text("model", DEFAULT_MODEL), text("notes_model", DEFAULT_NOTES_MODEL),
        text("voice", DEFAULT_VOICE), max_minutes, warn_minutes,
    )


def load_voice_keys() -> VoiceKeys:
    path = secrets_path()
    if path.is_file():
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise LiberError(f"{path} can be read by other users (mode {mode:o}); fix it with: chmod 600 {path}")
    stored = read_secret_values()
    openai_key = os.environ.get("OPENAI_API_KEY") or stored.get("openai_api_key", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or stored.get("anthropic_api_key", "")
    missing = [label for label, value in (("OpenAI", openai_key), ("Anthropic", anthropic_key)) if not value]
    if missing:
        raise LiberError(f"missing {' and '.join(missing)} API key. {_SETUP_HINT}")
    return VoiceKeys(openai_key, anthropic_key)


def save_voice_setup(name: str, openai_key: str, anthropic_key: str) -> None:
    name, openai_key, anthropic_key = name.strip(), openai_key.strip(), anthropic_key.strip()
    config = user_config_path()
    if not config.is_file():
        raise LiberError(f"{config} not found; run `liber init <path>` to create your vault first")
    if not (name and openai_key and anthropic_key):
        raise LiberError("your first name and both API keys are required")
    data = _config()
    if "interview" not in data:
        text = config.read_text(encoding="utf-8")
        if text and not text.endswith("\n"):
            text += "\n"
        config.write_text(text + f"\n[interview]\nname = {json.dumps(name)}\n", encoding="utf-8")
    elif not isinstance(data["interview"], dict) or "name" not in data["interview"]:
        raise LiberError(f"[interview] in {config} has no name; add: name = {json.dumps(name)}")
    values = read_secret_values()
    values.update({"openai_api_key": openai_key, "anthropic_api_key": anthropic_key})
    write_secret_values(values)
```

Run: `uv run pytest -v`
Expected: all pass. That includes every existing test, and in particular `tests/test_server_auth.py::test_logout_all_rotates_key_and_clears_storage`, which still passes.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/liber/server/settings.py src/liber/interview tests/conftest.py tests/test_interview_settings.py tests/test_server_settings.py
git commit -m "feat(interview): voice extra, interview settings and API keys

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 2: Transcript assembly and rendering

**Files:**
- Create: `src/liber/interview/transcript.py`
- Test: `tests/test_interview_transcript.py`

**Interfaces:**
- Produces:
  - Constants: `ME = "me"`, `AI = "ai"`, `NOTE = "note"`, `RESUME = "resume"`, `GAP_MS = 2500`
  - `@dataclass(frozen=True) Fragment(speaker: str, start_ms: int, end_ms: int, text: str)`
  - `@dataclass(frozen=True) Turn(speaker, start_ms, end_ms, text)`, with property `words -> int`
  - `mmss(ms: int) -> str`, e.g. `"02:05"`; minutes grow past 59, e.g. `"61:05"`
  - `slugify(topic: str) -> str`
  - `TranscriptAssembler`, with:
    - `.offset_ms: int`
    - `add_delta(speaker, delta, start_ms, end_ms)`, where times are relative to the current live session
    - `add_note(text, at_ms)`, where `at_ms` is absolute
    - `begin_resume() -> int`, returning the new offset
    - `end_ms -> int` (property)
    - `turns() -> list[Turn]`
    - `to_state() -> dict` and `TranscriptAssembler.from_state(dict)`
  - `dialogue(turns: list[Turn], limit: int | None = None) -> str` (for Claude)
  - `render_transcript(*, topic, day: date, minutes: int, voice: str, model: str, notes_stem: str, turns: list[Turn]) -> str`

- [ ] **Step 1: Write failing tests**

`tests/test_interview_transcript.py`:
```python
from datetime import date

from liber.interview.transcript import (
    AI, ME, NOTE, RESUME, TranscriptAssembler, Turn, dialogue, mmss, render_transcript, slugify,
)


def test_mmss():
    assert mmss(0) == "00:00" and mmss(125_400) == "02:05" and mmss(3_665_000) == "61:05" and mmss(-5) == "00:00"


def test_slugify():
    assert slugify("My Career!") == "my-career"
    assert slugify("  ") == "interview"
    assert slugify("a" * 60) == "a" * 40
    assert slugify("Ünïcode & people") == "n-code-people"


def test_groups_by_speaker_and_gap():
    a = TranscriptAssembler()
    a.add_delta(AI, " Hi Rick", 2000, 2200)
    a.add_delta(AI, ", how are you?", 2200, 2600)
    a.add_delta(ME, " Good", 4000, 4200)
    a.add_delta(ME, " thanks.", 4300, 4500)
    a.add_delta(ME, " Anyway", 7100, 7300)  # gap 2,600 ms -> new turn
    assert a.turns() == [
        Turn(AI, 2000, 2600, "Hi Rick, how are you?"),
        Turn(ME, 4000, 4500, "Good thanks."),
        Turn(ME, 7100, 7300, "Anyway"),
    ]
    assert a.turns()[1].words == 2


def test_out_of_order_fragments_are_sorted():
    a = TranscriptAssembler()
    a.add_delta(ME, " world", 1200, 1400)
    a.add_delta(ME, " hello", 1000, 1200)
    assert a.turns() == [Turn(ME, 1000, 1400, "hello world")]


def test_interleaved_speakers_split_turns():
    a = TranscriptAssembler()
    a.add_delta(ME, " one", 0, 100)
    a.add_delta(AI, " mm", 150, 200)
    a.add_delta(ME, " two", 250, 300)
    assert [t.speaker for t in a.turns()] == [ME, AI, ME]


def test_notes_and_resume_offsets():
    a = TranscriptAssembler()
    a.add_delta(ME, " before", 1000, 2000)
    a.add_note("  typed thing ", 2500)
    offset = a.begin_resume()
    assert offset == 3500 and a.offset_ms == 3500
    a.add_delta(ME, " after", 100, 300)
    turns = a.turns()
    assert [t.speaker for t in turns] == [ME, NOTE, RESUME, ME]
    assert turns[1].text == "typed thing"
    assert turns[3].start_ms == 3600
    assert a.end_ms == 3800


def test_state_roundtrip():
    a = TranscriptAssembler()
    a.add_delta(ME, " hi", 0, 100)
    a.begin_resume()
    b = TranscriptAssembler.from_state(a.to_state())
    assert b.turns() == a.turns() and b.offset_ms == a.offset_ms


def test_empty_deltas_ignored():
    a = TranscriptAssembler()
    a.add_delta(ME, "", 0, 100)
    assert a.turns() == [] and a.end_ms == 0


def test_dialogue_for_claude():
    turns = [Turn(AI, 0, 100, "Question?"), Turn(ME, 2000, 3000, "Answer."), Turn(NOTE, 4000, 4000, "typed"),
             Turn(RESUME, 5000, 5000, ""), Turn(ME, 6000, 7000, "More.")]
    assert dialogue(turns) == (
        "Interviewer [00:00]: Question?\nMe [00:02]: Answer.\nTyped note [00:04]: typed\n— resumed —\nMe [00:06]: More."
    )
    assert dialogue(turns, limit=1) == "Me [00:06]: More."


def test_render_transcript():
    turns = [Turn(AI, 12_000, 13_000, "Hi?"), Turn(ME, 20_000, 21_000, "Hello."),
             Turn(NOTE, 21_400, 21_400, "Also X."), Turn(RESUME, 30_000, 30_000, ""), Turn(ME, 31_000, 32_000, "Back.")]
    md = render_transcript(topic="my career", day=date(2026, 10, 2), minutes=38, voice="marin",
                           model="claude-sonnet-5-5", notes_stem="interview-2026-10-02-my-career-notes", turns=turns)
    assert md == (
        "<!-- liber interview transcript -->\n"
        "# Interview — my career — 2026-10-02\n\n"
        "- Duration: 38 min · Voice: gpt-live-1 (marin) · Brain: claude-sonnet-5-5\n"
        "- Notes: [[interview-2026-10-02-my-career-notes]]\n\n"
        "**Interviewer** [00:12]: Hi?\n\n"
        "**Me** [00:20]: Hello.\n\n"
        "*(typed note)* [00:21]: Also X.\n\n"
        "— resumed —\n\n"
        "**Me** [00:31]: Back.\n"
    )
```

Run: `uv run pytest tests/test_interview_transcript.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'liber.interview.transcript'`.

- [ ] **Step 2: Implement**

`src/liber/interview/transcript.py`:
```python
"""Assembling live transcript fragments into turns, and rendering transcripts."""

import re
from dataclasses import asdict, dataclass
from datetime import date

ME, AI, NOTE, RESUME = "me", "ai", "note", "resume"
GAP_MS = 2_500
_RESUME_LEAD_MS = 1_000
_MD_LABELS = {ME: "**Me**", AI: "**Interviewer**", NOTE: "*(typed note)*"}
_LLM_LABELS = {ME: "Me", AI: "Interviewer", NOTE: "Typed note"}


@dataclass(frozen=True)
class Fragment:
    speaker: str
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class Turn:
    speaker: str
    start_ms: int
    end_ms: int
    text: str

    @property
    def words(self) -> int:
        return len(self.text.split())


def mmss(ms: int) -> str:
    total = max(0, ms) // 1000
    return f"{total // 60:02d}:{total % 60:02d}"


def slugify(topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40].strip("-")
    return slug or "interview"


class TranscriptAssembler:
    def __init__(self) -> None:
        self.offset_ms = 0
        self._fragments: list[Fragment] = []

    def add_delta(self, speaker: str, delta: str, start_ms: int, end_ms: int) -> None:
        if delta:
            self._fragments.append(Fragment(speaker, start_ms + self.offset_ms, end_ms + self.offset_ms, delta))

    def add_note(self, text: str, at_ms: int) -> None:
        self._fragments.append(Fragment(NOTE, at_ms, at_ms, text.strip()))

    def begin_resume(self) -> int:
        """Start the next live session's clock after everything recorded so far."""
        self.offset_ms = self.end_ms + _RESUME_LEAD_MS
        marker = self.offset_ms - _RESUME_LEAD_MS // 2
        self._fragments.append(Fragment(RESUME, marker, marker, ""))
        return self.offset_ms

    @property
    def end_ms(self) -> int:
        return max((f.end_ms for f in self._fragments), default=0)

    def turns(self) -> list[Turn]:
        merged: list[Turn] = []
        for frag in sorted(self._fragments, key=lambda f: (f.start_ms, f.end_ms)):
            last = merged[-1] if merged else None
            if (
                frag.speaker in (ME, AI)
                and last is not None
                and last.speaker == frag.speaker
                and frag.start_ms - last.end_ms < GAP_MS
            ):
                merged[-1] = Turn(last.speaker, last.start_ms, max(last.end_ms, frag.end_ms), last.text + frag.text)
            else:
                merged.append(Turn(frag.speaker, frag.start_ms, frag.end_ms, frag.text))
        return [Turn(t.speaker, t.start_ms, t.end_ms, " ".join(t.text.split())) for t in merged]

    def to_state(self) -> dict:
        return {"offset_ms": self.offset_ms, "fragments": [asdict(f) for f in self._fragments]}

    @classmethod
    def from_state(cls, data: dict) -> "TranscriptAssembler":
        assembler = cls()
        assembler.offset_ms = int(data.get("offset_ms", 0))
        assembler._fragments = [Fragment(**f) for f in data.get("fragments", [])]
        return assembler


def dialogue(turns: list[Turn], limit: int | None = None) -> str:
    chosen = turns[-limit:] if limit else turns
    lines = []
    for turn in chosen:
        if turn.speaker == RESUME:
            lines.append("— resumed —")
        else:
            lines.append(f"{_LLM_LABELS.get(turn.speaker, turn.speaker)} [{mmss(turn.start_ms)}]: {turn.text}")
    return "\n".join(lines)


def render_transcript(
    *, topic: str, day: date, minutes: int, voice: str, model: str, notes_stem: str, turns: list[Turn]
) -> str:
    header = (
        "<!-- liber interview transcript -->\n"
        f"# Interview — {topic} — {day.isoformat()}\n\n"
        f"- Duration: {minutes} min · Voice: gpt-live-1 ({voice}) · Brain: {model}\n"
        f"- Notes: [[{notes_stem}]]\n\n"
    )
    body = []
    for turn in turns:
        if turn.speaker == RESUME:
            body.append("— resumed —")
        else:
            body.append(f"{_MD_LABELS.get(turn.speaker, turn.speaker)} [{mmss(turn.start_ms)}]: {turn.text}")
    return header + "\n\n".join(body) + "\n"
```

Run: `uv run pytest tests/test_interview_transcript.py -v`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add src/liber/interview/transcript.py tests/test_interview_transcript.py
git commit -m "feat(interview): transcript assembly and rendering

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 3: Vault brief and prompts

**Files:**
- Create: `src/liber/interview/brief.py`, `src/liber/interview/prompts.py`
- Test: `tests/test_interview_brief.py`

**Interfaces:**
- Consumes:
  - `liber.server.knowledge.VaultView(vault, ceiling)`, with `.read(path)`, `.search(query, limit=)` returning `[{path, title, score, snippets}]`, `.docs()` returning `dict[rel, Doc]`, and `NotFound`
  - fixture `vault`, helper `write`
- Produces:
  - `INTERVIEW_CEILING = "personal"`, `BRIEF_MAX_CHARS = 24_000`, `MAX_EXCERPTS = 8`
  - `@dataclass(frozen=True) VaultBrief(topic: str | None, profile: str, open_questions: str, excerpts: list[dict], folders: list[str], previous_notes: str = "")`, with `.render() -> str`
  - `build_brief(vault: Path, topic: str | None, previous_notes: str = "") -> VaultBrief`
  - `liber.interview.prompts`:
    - constants `FALLBACK_LINE`, `NOTES_SECTIONS`, `OPENING_SYSTEM`, `STEER_SYSTEM`, `DELEGATION_SYSTEM`, `NOTES_SYSTEM`
    - functions `interviewer_instructions(name, topic)`, `opening_instruction(name, question)`, `resume_instruction(name)`, `hold_on(name)`, `hold_off(name)`, `time_warning(name, minutes_left)`, `time_up(name)`, `typed_note_context(name, text)`, `resume_seed_text(coverage, recent_dialogue)`

- [ ] **Step 1: Write failing tests**

`tests/test_interview_brief.py`:
```python
import pytest

from helpers import write
from liber.interview import prompts
from liber.interview.brief import BRIEF_MAX_CHARS, VaultBrief, build_brief


def fm(type, sens, body):
    return f"---\ntype: {type}\nupdated: 2026-09-30\nsensitivity: {sens}\ntags: []\n---\n\n{body}\n"


def test_brief_has_profile_questions_and_topic_excerpts(vault):
    write(vault / "career" / "acme.md", fm("career", "personal", "# Acme\nLed the payments migration at Acme."))
    write(vault / "open-questions.md", "# Open questions\n\n- What happened in 2019?\n")
    brief = build_brief(vault, "payments")
    assert "About me" in brief.profile
    assert "What happened in 2019?" in brief.open_questions
    assert [e["path"] for e in brief.excerpts] == ["career/acme.md"]
    text = brief.render()
    assert "payments migration" in text and "What happened in 2019?" in text and "career/acme.md" in text
    assert "Topic: payments" in text


def test_private_content_never_in_brief(vault):
    # Review Focus 1
    write(vault / "core" / "secret.md", fm("core", "private", "# Secret\nPRIVATEMARK payments"))
    write(vault / "sources" / "documents" / "x.md", "SOURCEMARK payments\n")
    brief = build_brief(vault, "payments")
    text = brief.render()
    assert "PRIVATEMARK" not in text and "SOURCEMARK" not in text
    assert "sources" not in brief.folders


def test_no_topic_means_no_excerpts(vault):
    assert build_brief(vault, None).excerpts == []
    assert "Topic: (to be chosen)" in build_brief(vault, None).render()


def test_previous_notes_included(vault):
    assert "LASTTIME" in build_brief(vault, "x", previous_notes="LASTTIME notes").render()


def test_render_trims_excerpts_then_questions():
    big = "q" * 30_000
    excerpts = [{"path": f"p{i}.md", "title": "t", "snippets": ["s" * 1000]} for i in range(8)]
    brief = VaultBrief("topic", "PROFILE", big, excerpts, ["core"])
    text = brief.render()
    assert len(text) <= BRIEF_MAX_CHARS
    assert "PROFILE" in text and "p0.md" not in text and big not in text


def test_prompts_mention_name_and_key_rules():
    text = prompts.interviewer_instructions("Rick", "my career")
    assert "Rick" in text and "my career" in text
    assert "Silence is thinking time" in text and "one question at a time" in text.lower()
    assert len(text) < 12_000
    assert "Rick" in prompts.opening_instruction("Rick", "Where did it start?")
    assert "Where did it start?" in prompts.opening_instruction("Rick", "Where did it start?")
    assert prompts.FALLBACK_LINE == "Let's keep going — tell me more about that."
    assert prompts.NOTES_SECTIONS == ("New facts", "Corrections to the vault", "People mentioned",
                                      "Preferences, values and feelings", "Follow-up questions")
    for section in prompts.NOTES_SECTIONS:
        assert f"## {section}" in prompts.NOTES_SYSTEM
    assert '"hint"' in prompts.STEER_SYSTEM and '"question"' in prompts.OPENING_SYSTEM
    assert "3 minutes" in prompts.time_warning("Rick", 3)
    assert "typed note" in prompts.typed_note_context("Rick", "X")
```

Run: `uv run pytest tests/test_interview_brief.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'liber.interview.brief'`.

- [ ] **Step 2: Implement brief.py**

`src/liber/interview/brief.py`:
```python
"""What the interviewer may know: a size-capped brief built from the vault at the `personal` ceiling."""

from dataclasses import dataclass
from pathlib import Path

from liber.server.knowledge import NotFound, VaultView

INTERVIEW_CEILING = "personal"
BRIEF_MAX_CHARS = 24_000  # about 6,000 tokens
MAX_EXCERPTS = 8


@dataclass(frozen=True)
class VaultBrief:
    topic: str | None
    profile: str
    open_questions: str
    excerpts: list[dict]
    folders: list[str]
    previous_notes: str = ""

    def _compose(self, excerpts: list[dict], open_questions: str, previous_notes: str) -> str:
        lines = [
            "# What liber knows about the user (personal level)",
            "",
            f"Topic: {self.topic or '(to be chosen)'}",
            f"Folders: {', '.join(self.folders) or '(none)'}",
            "",
            "## Profile (AGENTS.md)",
            "",
            self.profile.strip() or "(empty)",
            "",
            "## Open questions",
            "",
            open_questions.strip() or "(none)",
            "",
            "## Related notes",
            "",
        ]
        if excerpts:
            for item in excerpts:
                snippets = " … ".join(item.get("snippets") or [])
                lines.append(f"- `{item['path']}` — {item.get('title', '')}: {snippets}")
        else:
            lines.append("(none)")
        if previous_notes.strip():
            lines += ["", "## Notes from the previous interview", "", previous_notes.strip()]
        return "\n".join(lines) + "\n"

    def render(self) -> str:
        excerpts, open_questions, previous = list(self.excerpts), self.open_questions, self.previous_notes
        while True:
            text = self._compose(excerpts, open_questions, previous)
            if len(text) <= BRIEF_MAX_CHARS:
                return text
            if excerpts:
                excerpts.pop()
            elif open_questions:
                open_questions = ""
            elif previous:
                previous = ""
            else:
                return text[:BRIEF_MAX_CHARS]


def build_brief(vault: Path, topic: str | None, previous_notes: str = "") -> VaultBrief:
    view = VaultView(vault, INTERVIEW_CEILING)

    def read(path: str) -> str:
        try:
            return view.read(path)
        except NotFound:
            return ""

    excerpts: list[dict] = []
    if topic and topic.strip():
        excerpts = [
            {"path": r["path"], "title": r["title"], "snippets": r["snippets"]}
            for r in view.search(topic, limit=MAX_EXCERPTS)
        ]
    folders = sorted({rel.split("/")[0] for rel in view.docs() if "/" in rel})
    return VaultBrief(topic, read("AGENTS.md"), read("open-questions.md"), excerpts, folders, previous_notes)
```

- [ ] **Step 3: Implement prompts.py**

`src/liber/interview/prompts.py`:
```python
"""Every prompt the interviewer uses, in one place for tuning."""

FALLBACK_LINE = "Let's keep going — tell me more about that."
NOTES_SECTIONS = (
    "New facts",
    "Corrections to the vault",
    "People mentioned",
    "Preferences, values and feelings",
    "Follow-up questions",
)


def interviewer_instructions(name: str, topic: str) -> str:
    return f"""# Role
You are a warm, curious, unhurried biographer helping {name} build a knowledge base about their life.
Today's topic is: {topic}. Speak English.

# Questions
Ask one question at a time. Keep questions short and open-ended. Invite concrete stories,
names of places and people, and approximate dates.

# Silence
Silence is thinking time. Never fill pauses. {name} often pauses for ten seconds or more in the
middle of an answer; that does not mean the answer is over. Wait until the answer is clearly
complete before you speak. Keep backchannels minimal (an occasional quiet "mm"), and never
interrupt.

# Interruptions
If {name} starts talking while you are speaking, stop and listen.

# Revisits
If {name} goes back to an earlier answer to correct or add something, welcome it, let them
finish, briefly acknowledge it, then return to the thread you were on.

# Facts
Never assert facts about {name} unless they come from the provided context. When unsure, ask.

# Quiet context
Private context hints from liber arrive during the conversation. Use them to choose follow-up
questions. Never read them aloud or mention that you received them.

# Delegation policy
Delegate when a thread is exhausted, when a new direction is needed, or when {name} asks what
liber already knows. While waiting for the result, say nothing or at most a brief "let me think".

# Time
When told time is nearly up, begin wrapping up. When asked to stop, thank {name} and mention
that the notes will be in their inbox.

# Opening
When instructed to begin, greet {name} briefly and ask the opening question.
"""


def opening_instruction(name: str, question: str) -> str:
    return (
        f"Begin now in English. Greet {name} in one short sentence, then ask exactly this opening question: "
        f'"{question}" Then stop and listen.'
    )


def resume_instruction(name: str) -> str:
    return (
        f"Continue the interview with {name} after a short interruption. In one short sentence say you're back "
        "and recall what you were discussing, then ask the next question. Then stop and listen."
    )


def hold_on(name: str) -> str:
    return f"{name} is thinking; wait silently. Do not speak until {name} speaks again."


def hold_off(name: str) -> str:
    return f"{name} is ready to continue. Keep listening; do not speak until {name} has finished."


def time_warning(name: str, minutes_left: int) -> str:
    return (
        f"About {minutes_left} minutes remain. Begin wrapping up: ask at most one or two more questions, "
        f"then thank {name}."
    )


def time_up(name: str) -> str:
    return f"Time is up. Thank {name} warmly in one sentence, mention the notes will be in their inbox, and stop."


def typed_note_context(name: str, text: str) -> str:
    return f"{name} added a typed note: {text}"


def resume_seed_text(coverage: str, recent_dialogue: str) -> str:
    return (
        "This interview was interrupted and is now resuming.\n\n"
        f"What has been covered so far:\n{coverage or '(not recorded)'}\n\n"
        f"Most recent exchange:\n{recent_dialogue or '(none)'}"
    )


OPENING_SYSTEM = """You plan the opening of a voice interview that helps the user build a knowledge base about their life.
You receive what liber already knows about the user (personal level) and either the topic the user chose or a request to choose one.
Reply with JSON only: {"topic": "...", "reason": "...", "question": "..."}
- topic: the user's topic, unchanged, if they chose one; otherwise a short topic (2-5 words) for the most valuable gap: thin areas, stale facts, or open questions.
- reason: one sentence explaining your choice (an empty string if the user chose the topic).
- question: one warm, open opening question on the topic, at most 30 words."""

STEER_SYSTEM = """You are the quiet producer behind a live voice interview. The interviewer is a separate voice model talking with the user right now; you never speak to the user. After each answer you send the interviewer one short private hint.
You receive what liber already knows about the user (personal level), the running coverage list, and the conversation so far.
Reply with JSON only: {"hint": "...", "coverage": "..."}
- hint: at most 80 words. Suggest one or two good follow-up questions grounded in what the user just said, flag any contradiction with what liber knows (quote the fact briefly), and say when the thread is nearly exhausted and what area could come next.
- coverage: an updated running list, at most 150 words, of what this interview has covered so far, as terse fragments.
Never include anything that the conversation or the provided context does not support."""

DELEGATION_SYSTEM = """You are the producer behind a live voice interview. The voice interviewer has handed the conversation to you because it needs a new direction, or because the user asked what liber already knows.
Reply with exactly what the interviewer should say next: at most 50 words, warm and natural, ending with one open question.
You may search or read the user's knowledge base (personal level) with the tools, at most three times. Only state facts about the user that the tools or the provided context support.
No preamble, no quotation marks, no stage directions."""

NOTES_SYSTEM = """You write session notes from a voice interview transcript so the user can update their knowledge base.
You receive what liber already knows about the user (personal level) and the full transcript with [mm:ss] timestamps.
Write Markdown with exactly these five sections, in this order, each heading on its own line:
## New facts
## Corrections to the vault
## People mentioned
## Preferences, values and feelings
## Follow-up questions
Rules:
- Bullet points only. Write facts in the first person, as the user would ("I led ...").
- Every bullet except follow-up questions ends with one or more [mm:ss] citations from the transcript.
- Include approximate dates whenever the user gave them.
- If the user corrected or added to an earlier answer, write one merged bullet with the corrected facts and append *(amended later in the interview)*.
- Corrections to the vault: only where the transcript contradicts the provided context; name the file path in backticks.
- People mentioned: "- [[Full Name]]: relationship / context [mm:ss]".
- Follow-up questions: things the user mentioned but didn't explain, phrased as questions.
- Write "- none" for an empty section. Output only the five sections, with no title."""
```

Run: `uv run pytest tests/test_interview_brief.py -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/liber/interview/brief.py src/liber/interview/prompts.py tests/test_interview_brief.py
git commit -m "feat(interview): personal-level vault brief and prompts

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 4: Brain (Claude) with fakes

**Files:**
- Create: `src/liber/interview/brain.py`, `tests/voice_fakes.py`
- Test: `tests/test_interview_brain.py`

**Interfaces:**
- Consumes:
  - `VaultBrief`, `INTERVIEW_CEILING` (Task 3), `prompts` (Task 3)
  - `InterviewSettings` (Task 1)
  - `VaultView` and `NotFound` from `liber.server.knowledge`
- Produces:
  - `@dataclass(frozen=True) LLMResponse(stop_reason: str, blocks: list[dict])`, with properties `.text` and `.tool_uses`
  - `class LLM(Protocol)`, with `async complete(*, model, system, messages, max_tokens, tools=None, tool_choice=None) -> LLMResponse`
  - `class LLMError(LiberError)`
  - `class AnthropicLLM(api_key, *, http_client=None, request_timeout=60.0)`
  - `@dataclass(frozen=True) Opening(topic: str, reason: str, question: str)`
  - `@dataclass(frozen=True) Hint(text: str, coverage: str)`
  - `VAULT_TOOLS`, `run_tool(view, name, args) -> tuple[str, bool]`
  - `format_notes(body, *, topic, day, minutes, transcript_stem) -> str`
  - Constants `LIVE_TIMEOUT_S = 8.0`, `MAX_TOOL_CALLS = 3`, `HINT_MAX_CHARS = 400`, `COVERAGE_MAX_WORDS = 150`, `DELEGATION_MAX_WORDS = 60`, `CONTEXT_TURNS = 40`
  - `class Brain(llm, settings, vault, brief)`, with:
    - `.brief` (assignable)
    - `async plan_opening(topic: str | None) -> Opening`
    - `async steer(dialogue_text: str, coverage: str) -> Hint | None`
    - `async answer_delegation(dialogue_text: str, coverage: str) -> str`
    - `async write_notes(*, transcript_md, topic, day, minutes, transcript_stem) -> str`, which raises `LLMError` after two failures
  - `tests/voice_fakes.py`:
    - `FakeLLM(responder)`, with `.calls`
    - `text_reply(text)`, `tool_reply(name, args, id="tu1")`
    - `FakeConnection`, `FakeLiveClient` (used from Task 5 on)
    - `async settle(seconds=0.05)`

- [ ] **Step 1: Write the fakes**

`tests/voice_fakes.py`:
```python
"""Offline stand-ins for Claude and the OpenAI Live service."""

import asyncio
from contextlib import asynccontextmanager

from liber.interview.brain import LLMResponse


def text_reply(text: str, stop: str = "end_turn") -> LLMResponse:
    return LLMResponse(stop, [{"type": "text", "text": text}])


def tool_reply(name: str, args: dict, id: str = "tu1") -> LLMResponse:
    return LLMResponse("tool_use", [{"type": "tool_use", "id": id, "name": name, "input": args}])


class FakeLLM:
    """`responder(call) -> LLMResponse | Exception | awaitable`, where call is the recorded kwargs dict."""

    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict] = []

    async def complete(self, *, model, system, messages, max_tokens, tools=None, tool_choice=None):
        call = {"model": model, "system": system, "messages": [dict(m) for m in messages],
                "max_tokens": max_tokens, "tools": tools, "tool_choice": tool_choice}
        self.calls.append(call)
        result = self.responder(call)
        if asyncio.iscoroutine(result):
            result = await result
        if isinstance(result, Exception):
            raise result
        return result


class FakeConnection:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.sent: list[tuple] = []

    def push(self, event: dict) -> None:
        self.queue.put_nowait(event)

    def drop(self) -> None:
        self.queue.put_nowait(None)

    async def recv(self):
        return await self.queue.get()

    async def commentary(self, content, delegation_id):
        self.sent.append(("commentary", content, delegation_id))

    async def thinking(self, content):
        self.sent.append(("thinking", content))

    async def instructions(self, content):
        self.sent.append(("instructions", content))

    async def mute(self):
        self.sent.append(("mute",))

    async def unmute(self):
        self.sent.append(("unmute",))

    async def close(self):
        self.sent.append(("close",))
        self.push({"type": "session.closed", "reason": "close_requested"})

    def kinds(self) -> list[str]:
        return [item[0] for item in self.sent]


class FakeLiveClient:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.created: list[dict] = []
        self.connections: list[FakeConnection] = []
        self.hangups: list[str] = []

    async def create_session(self, *, sdp, instructions, voice, seed=None):
        if self.fail is not None:
            raise self.fail
        self.created.append({"sdp": sdp, "instructions": instructions, "voice": voice, "seed": seed})
        self.connections.append(FakeConnection())
        session_id = f"live_{len(self.created)}"
        return session_id, f"ANSWER-{session_id}"

    @asynccontextmanager
    async def attach(self, session_id):
        yield self.connections[int(session_id.split("_")[1]) - 1]

    async def hangup(self, session_id):
        self.hangups.append(session_id)


async def settle(seconds: float = 0.05) -> None:
    await asyncio.sleep(seconds)
```

- [ ] **Step 2: Write failing tests**

`tests/test_interview_brain.py`:
```python
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
```

Run: `uv run pytest tests/test_interview_brain.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'liber.interview.brain'`.

- [ ] **Step 3: Implement brain.py**

`src/liber/interview/brain.py`:
```python
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
```

Run: `uv run pytest tests/test_interview_brain.py -v`
Expected: all pass. If `test_anthropic_llm_against_mock_transport` fails because the SDK rejects the mocked response shape, print the exception and adjust **only the mocked JSON**, not the product code.

- [ ] **Step 4: Commit**

```bash
git add src/liber/interview/brain.py tests/voice_fakes.py tests/test_interview_brain.py
git commit -m "feat(interview): Claude brain for opening, steering, delegation and notes

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 5: OpenAI Live client

**Files:**
- Create: `src/liber/interview/live.py`
- Test: `tests/test_interview_live_client.py`

**Interfaces:**
- Consumes: the `openai` SDK facts in the plan header
- Produces:
  - `LIVE_MODEL = "gpt-live-1"`
  - `REFLECTED_AUDIO = frozenset({"session.input_audio.append", "session.output_audio.delta"})`
  - `class LiveError(LiberError)`
  - `class LiveConnection(Protocol)`, with:
    - `async recv() -> dict | None`, where `None` means closed
    - `async commentary(content, delegation_id)`
    - `async thinking(content)`
    - `async instructions(content)`
    - `async mute()`, `async unmute()`
    - `async close()`
  - `class LiveClient(Protocol)`, with:
    - `async create_session(*, sdp, instructions, voice, seed=None) -> tuple[str, str]`, returning `(session_id, answer_sdp)`
    - `attach(session_id)`, an async context manager yielding a `LiveConnection`
    - `async hangup(session_id)`
  - `class OpenAILiveClient(api_key, *, http_client=None, websocket_base_url=None)`

- [ ] **Step 1: Write failing tests**

`tests/test_interview_live_client.py`:
```python
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


async def test_create_session_errors_are_live_errors():
    client = OpenAILiveClient("sk-test", http_client=http(
        lambda r: httpx2.Response(403, json={"error": {"message": "no access to gpt-live-1"}})))
    with pytest.raises(LiveError, match="403") as info:
        await client.create_session(sdp="OFFER", instructions="I", voice="marin")
    assert "sk-test" not in str(info.value)


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
```

Run: `uv run pytest tests/test_interview_live_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'liber.interview.live'`.

- [ ] **Step 2: Implement live.py**

`src/liber/interview/live.py`:
```python
"""The OpenAI Live (GPT-Live-1) side: session creation and the sideband connection."""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

from liber.errors import LiberError

log = logging.getLogger("liber.interview")

LIVE_MODEL = "gpt-live-1"
REFLECTED_AUDIO = frozenset({"session.input_audio.append", "session.output_audio.delta"})


class LiveError(LiberError):
    """The voice service refused or failed."""


class LiveConnection(Protocol):
    async def recv(self) -> dict | None: ...
    async def commentary(self, content: str, delegation_id: str) -> None: ...
    async def thinking(self, content: str) -> None: ...
    async def instructions(self, content: str) -> None: ...
    async def mute(self) -> None: ...
    async def unmute(self) -> None: ...
    async def close(self) -> None: ...


class LiveClient(Protocol):
    async def create_session(
        self, *, sdp: str, instructions: str, voice: str, seed: list[dict] | None = None
    ) -> tuple[str, str]: ...
    def attach(self, session_id: str) -> AbstractAsyncContextManager[LiveConnection]: ...
    async def hangup(self, session_id: str) -> None: ...


class _OpenAIConnection:
    def __init__(self, conn) -> None:
        self._conn = conn
        self._seq = 0

    def _event_id(self, prefix: str) -> str:
        self._seq += 1
        return f"liber_{prefix}_{self._seq}"

    async def recv(self) -> dict | None:
        from websockets.exceptions import ConnectionClosed

        while True:
            try:
                raw = await self._conn.recv_bytes()
            except ConnectionClosed:
                return None
            try:
                event = json.loads(raw)
            except ValueError:
                log.warning("ignored an unparseable sideband message")
                continue
            if isinstance(event, dict) and event.get("type") not in REFLECTED_AUDIO:
                return event

    async def commentary(self, content: str, delegation_id: str) -> None:
        await self._conn.session.commentary.append(
            content=content, delegation_id=delegation_id, event_id=self._event_id("c")
        )

    async def thinking(self, content: str) -> None:
        await self._conn.session.thinking.append(content=content, delegation_id=None, event_id=self._event_id("t"))

    async def instructions(self, content: str) -> None:
        await self._conn.session.instructions.append(
            content=content, delegation_id=None, event_id=self._event_id("i")
        )

    async def mute(self) -> None:
        await self._conn.session.input_audio.mute(event_id=self._event_id("m"))

    async def unmute(self) -> None:
        await self._conn.session.input_audio.unmute(event_id=self._event_id("u"))

    async def close(self) -> None:
        await self._conn.session.close(event_id=self._event_id("x"))


class OpenAILiveClient:
    def __init__(self, api_key: str, *, http_client=None, websocket_base_url: str | None = None) -> None:
        from openai import AsyncOpenAI

        kwargs: dict = {"api_key": api_key, "max_retries": 0}
        if http_client is not None:
            kwargs["http_client"] = http_client
        if websocket_base_url is not None:
            kwargs["websocket_base_url"] = websocket_base_url
        self._client = AsyncOpenAI(**kwargs)

    async def create_session(
        self, *, sdp: str, instructions: str, voice: str, seed: list[dict] | None = None
    ) -> tuple[str, str]:
        import openai

        session: dict = {
            "model": LIVE_MODEL,
            "audio": {"output": {"voice": voice}},
            "instructions": instructions,
            "delegation": {"type": "client"},
        }
        if seed:
            session["input"] = seed
        try:
            result = await self._client.live.create(session=session, transport={"type": "webrtc", "sdp": sdp})
        except openai.APIStatusError as exc:
            raise LiveError(f"OpenAI refused the voice session ({exc.status_code}): {exc.message}") from exc
        except openai.APIError as exc:
            raise LiveError(f"could not reach OpenAI ({type(exc).__name__})") from exc
        return result.session.id, result.transport.sdp

    @asynccontextmanager
    async def attach(self, session_id: str) -> AsyncIterator[LiveConnection]:
        async with self._client.live.sideband.connect(session_id=session_id) as conn:
            yield _OpenAIConnection(conn)

    async def hangup(self, session_id: str) -> None:
        import openai

        try:
            await self._client.live.sessions.hangup(session_id)
        except openai.APIError as exc:
            log.warning("hangup failed (%s)", type(exc).__name__)
```

Run: `uv run pytest tests/test_interview_live_client.py -v`
Expected: all pass. If the sideband test hangs, check that `websocket_base_url` ends in `/v1`; the SDK appends `/live/sessions/{id}/attach`. If `live.sessions.hangup` takes `session_id` as a keyword, change the call to `hangup(session_id=session_id)`.

- [ ] **Step 3: Commit**

```bash
git add src/liber/interview/live.py tests/test_interview_live_client.py
git commit -m "feat(interview): OpenAI Live client (WebRTC session creation and sideband)

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 6: InterviewSession core

**Files:**
- Create: `src/liber/interview/session.py`
- Test: `tests/test_interview_session.py`

**Interfaces:**
- Consumes:
  - `Brain`, `Opening`, `LLMError`, `CONTEXT_TURNS` (Task 4)
  - `LiveClient`, `LiveConnection`, `LiveError` (Task 5)
  - `TranscriptAssembler`, `ME`, `AI`, `dialogue`, `render_transcript`, `slugify` (Task 2)
  - `InterviewSettings`, `interviews_dir` (Task 1)
  - `prompts` (Task 3)
  - `liber.paths.free_name`
  - fakes from `tests/voice_fakes.py`
- Produces:
  - `@dataclass(frozen=True) InterviewResult(transcript: Path | None, notes: Path | None)`
  - Constants `FINISHED_TURN_S = 1.5`, `MIN_FINISHED_WORDS = 3`, `CLOSE_WAIT_S = 10.0`, `HEARTBEAT_TIMEOUT_S = 60.0`, `WATCH_INTERVAL_S = 5.0`
  - `class InterviewSession(*, vault, settings, brain, live, opening, workdir=None, clock=time.monotonic, now=datetime.now, finished_turn_s=..., heartbeat_timeout_s=..., watch_interval_s=..., close_wait_s=...)`, with:
    - attributes `.id`, `.workdir`, `.state` (one of `idle`, `live`, `interrupted`, `finishing`, `done`), `.done` (an `asyncio.Event`), `.result`, `.assembler`, `.coverage`, `.session_ids`
    - `async start(sdp) -> str` (the answer SDP)
    - `async add_note(text)`
    - `async toggle_hold() -> bool`
    - `heartbeat() -> dict` and `status() -> dict`
    - `async end(reason) -> InterviewResult`
    - `async finalize() -> InterviewResult`
  - Task 7 adds `resume`, the watchdog, `from_state` and the helpers.

- [ ] **Step 1: Write failing tests**

`tests/test_interview_session.py`:
```python
import json
from datetime import datetime

import pytest

from liber.interview import prompts
from liber.interview.brain import Brain, LLMError, Opening
from liber.interview.brief import build_brief
from liber.interview.live import LiveError
from liber.interview.session import InterviewSession
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
    import asyncio

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
```

Run: `uv run pytest tests/test_interview_session.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'liber.interview.session'`.

- [ ] **Step 2: Implement session.py**

`src/liber/interview/session.py`:
```python
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
                await conn.instructions(first_instruction)
                while not self._closed.is_set():
                    event = await conn.recv()
                    if event is None:
                        break
                    await self._handle(event)
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
        self.held = not self.held
        if self.held:
            await self._conn.mute()
            await self._conn.instructions(prompts.hold_on(self.settings.name))
        else:
            await self._conn.unmute()
            await self._conn.instructions(prompts.hold_off(self.settings.name))
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
        self._ending = True
        self.state = "finishing"
        self.message = "Writing your transcript and notes…"
        log.info("ending interview (%s)", reason)
        if self._conn is not None:
            await self._safe(self._conn.close())
            try:
                await asyncio.wait_for(self._closed.wait(), self.close_wait_s)
            except TimeoutError:
                if self.session_ids:
                    await self.live.hangup(self.session_ids[-1])
        if self._debounce is not None:
            self._debounce.cancel()
        for task in list(self._llm_tasks):
            task.cancel()
        self.result = await self.finalize()
        self.state = "done"
        self.done.set()
        return self.result

    async def finalize(self) -> InterviewResult:
        turns = self.assembler.turns()
        if not any(t.speaker == ME for t in turns):
            self._discard_workdir()
            self.message = "Nothing was recorded, so no files were written."
            return InterviewResult(None, None)
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
```

Run: `uv run pytest tests/test_interview_session.py -v`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add src/liber/interview/session.py tests/test_interview_session.py
git commit -m "feat(interview): interview session (events, steering, delegation, notes, finalize)

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 7: Resume, watchdog, recovery, notes retry and continue

**Files:**
- Modify: `src/liber/interview/session.py`
- Test: `tests/test_interview_resilience.py`

**Interfaces:**
- Consumes: `InterviewSession` (Task 6), `prompts.resume_instruction`, `prompts.resume_seed_text`, `prompts.time_warning`, `prompts.time_up`, `Brain.write_notes`, `TranscriptAssembler.begin_resume`/`from_state`
- Produces:
  - `InterviewSession.resume(sdp) -> str`. It requires `state == "interrupted"`.
  - The watchdog runs every `watch_interval_s` and is responsible for:
    - saving the draft;
    - the heartbeat timeout, which leads to `end("browser closed")`;
    - the warning at `max_minutes - warn_minutes`, which sends `prompts.time_warning` once;
    - the time limit at `max_minutes`, which sends `prompts.time_up`, then calls `end("time limit")`.
  - `InterviewSession.from_state(data: dict, *, vault, settings, brain, workdir) -> InterviewSession`, for finalizing only.
  - `unfinished_workdirs() -> list[Path]`.
  - `async recover_interviews(*, vault, settings, brain_for_topic: Callable[[str], Brain]) -> list[InterviewResult]`.
  - `async regenerate_notes(transcript: Path, brain: Brain) -> Path`.
  - `find_last_notes(vault) -> Path | None`.
  - `notes_topic(path: Path) -> str`.

- [ ] **Step 1: Write failing tests**

`tests/test_interview_resilience.py`:
```python
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
from voice_fakes import FakeLiveClient, FakeLLM, settle, text_reply

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
```

Run: `uv run pytest tests/test_interview_resilience.py -v`
Expected: FAIL with `ImportError: cannot import name 'find_last_notes'`.

- [ ] **Step 2: Implement**

In `src/liber/interview/session.py`:

1. Add `import re` to the imports.

2. Replace the placeholder `_start_watch` method with:
```python
    def _start_watch(self) -> None:
        self._watch_task = self._spawn(self._background, self._watch())

    async def _watch(self) -> None:
        warn_at = (self.settings.max_minutes - self.settings.warn_minutes) * 60
        stop_at = self.settings.max_minutes * 60
        while self.state not in ("finishing", "done"):
            await asyncio.sleep(self.watch_interval_s)
            if self.state in ("finishing", "done"):
                return
            self._save_draft()
            if self.clock() - self._last_heartbeat > self.heartbeat_timeout_s:
                self._spawn(self._background, self.end("browser closed"))
                return
            elapsed = self.clock() - (self._started_at or self.clock())
            if not self.warned and elapsed >= warn_at and self._conn is not None:
                self.warned = True
                await self._safe(self._conn.instructions(prompts.time_warning(self.settings.name, self.settings.warn_minutes)))
            if elapsed >= stop_at:
                if self._conn is not None:
                    await self._safe(self._conn.instructions(prompts.time_up(self.settings.name)))
                self._spawn(self._background, self.end("time limit"))
                return
```

3. Add this method after `start`:
```python
    async def resume(self, sdp: str) -> str:
        if self.state != "interrupted":
            raise LiberError("there is nothing to resume")
        seed_text = prompts.resume_seed_text(self.coverage, dialogue(self.assembler.turns(), 20))[:30_000]
        seed = [{"type": "message", "role": "developer", "content": [{"type": "input_text", "text": seed_text}]}]
        instructions = prompts.interviewer_instructions(self.settings.name, self.opening.topic)
        session_id, answer = await self.live.create_session(
            sdp=sdp, instructions=instructions, voice=self.settings.voice, seed=seed
        )
        self.assembler.begin_resume()
        self._last_heartbeat = self.clock()
        self._begin(session_id, prompts.resume_instruction(self.settings.name))
        return answer
```

4. Add this classmethod after `state_dict`:
```python
    @classmethod
    def from_state(cls, data: dict, *, vault: Path, settings: InterviewSettings, brain: Brain, workdir: Path) -> "InterviewSession":
        """Rebuild an unfinished interview from its draft, so it can be finalized."""
        started = datetime.fromisoformat(data["started_at"])
        opening = Opening(data.get("topic", "interview"), data.get("reason", ""), data.get("question", ""))
        session = cls(vault=vault, settings=settings, brain=brain, live=None, opening=opening,
                      workdir=workdir, now=lambda: started)
        session.id = data.get("id", session.id)
        session.assembler = TranscriptAssembler.from_state(data.get("assembler", {}))
        session.coverage = data.get("coverage", "")
        session.session_ids = list(data.get("session_ids", []))
        session._past_seconds = float(data.get("voice_seconds", 0.0))
        return session
```

5. Append these module-level functions at the end of the file:
```python
_NOTES_TITLE = re.compile(r"^# Interview notes — (.+?) — \d{4}-\d{2}-\d{2}", re.MULTILINE)
_TRANSCRIPT_TITLE = re.compile(r"^# Interview — (.+?) — (\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)
_DURATION = re.compile(r"^- Duration: (\d+) min", re.MULTILINE)


def unfinished_workdirs() -> list[Path]:
    root = interviews_dir()
    if not root.is_dir():
        return []
    return sorted(p.parent for p in root.glob("*/state.json"))


async def recover_interviews(
    *, vault: Path, settings: InterviewSettings, brain_for_topic: Callable[[str], Brain]
) -> list[InterviewResult]:
    results = []
    for workdir in unfinished_workdirs():
        try:
            data = json.loads((workdir / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("skipped an unreadable draft (%s)", type(exc).__name__)
            continue
        brain = brain_for_topic(data.get("topic", "interview"))
        session = InterviewSession.from_state(data, vault=vault, settings=settings, brain=brain, workdir=workdir)
        results.append(await session.finalize())
    return results


async def regenerate_notes(transcript: Path, brain: Brain) -> Path:
    text = transcript.read_text(encoding="utf-8")
    title = _TRANSCRIPT_TITLE.search(text)
    if not text.startswith("<!-- liber interview transcript -->") or title is None:
        raise LiberError(f"{transcript} is not an interview transcript")
    duration = _DURATION.search(text)
    minutes = int(duration.group(1)) if duration else 1
    stem = transcript.name[: -len(".md")]
    notes = await brain.write_notes(
        transcript_md=text, topic=title.group(1), day=title.group(2), minutes=minutes, transcript_stem=stem
    )
    path = transcript.parent / free_name(transcript.parent, f"{stem}-notes.md")
    path.write_text(notes, encoding="utf-8")
    return path


def find_last_notes(vault: Path) -> Path | None:
    candidates = [
        p
        for folder in (vault / "inbox", vault / "sources" / "documents")
        if folder.is_dir()
        for p in folder.glob("interview-*-notes.md")
    ]
    return max(candidates, key=lambda p: (p.name, p.stat().st_mtime), default=None)


def notes_topic(path: Path) -> str:
    match = _NOTES_TITLE.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise LiberError(f"{path} is not an interview notes file")
    return match.group(1)
```

6. `InterviewSession.__init__` declares `live: LiveClient`. `from_state` passes `None`, and the finalize-only path never calls `live`. Keep the annotation as `LiveClient | None`.

Run: `uv run pytest tests/test_interview_resilience.py tests/test_interview_session.py -v`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add src/liber/interview/session.py tests/test_interview_resilience.py
git commit -m "feat(interview): resume, heartbeat and time-limit watchdog, recovery, notes retry

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 8: Local web server and interview page

**Files:**
- Create: `src/liber/interview/web.py`, `src/liber/interview/static/index.html`, `src/liber/interview/static/app.js`, `src/liber/interview/static/style.css`
- Test: `tests/test_interview_web.py`

**Interfaces:**
- Consumes: `InterviewSession` (Tasks 6 and 7), `LiveError`, `LiberError`, fakes
- Produces:
  - `STATIC_DIR: Path`
  - `create_app(session: InterviewSession, token: str, *, static_dir: Path = STATIC_DIR) -> Starlette`
  - Routes:
    - `GET /`, `/app.js`, `/style.css` (no token)
    - `POST /api/session {sdp}` returns `{sdp}`
    - `POST /api/resume {sdp}` returns `{sdp}`
    - `POST /api/note {text}` returns `{ok: true}`
    - `POST /api/hold` returns `{held}`
    - `GET /api/status` returns the status dict and counts as a heartbeat
    - `POST /api/end` returns `{transcript, notes}`

- [ ] **Step 1: Write failing tests**

`tests/test_interview_web.py`:
```python
import json
from datetime import datetime

import pytest
from starlette.testclient import TestClient

from liber.interview import prompts
from liber.interview.brain import Brain, Opening
from liber.interview.brief import build_brief
from liber.interview.live import LiveError
from liber.interview.session import InterviewSession
from liber.interview.settings import InterviewSettings
from liber.interview.web import create_app
from voice_fakes import FakeLiveClient, FakeLLM, text_reply

TOKEN = "tok123"
H = {"X-Liber-Token": TOKEN}


def responder(call):
    if call["system"] == prompts.NOTES_SYSTEM:
        return text_reply("## New facts\n- x [00:01]")
    return text_reply(json.dumps({"hint": "h", "coverage": "c"}))


def make(vault, tmp_path, live=None):
    settings = InterviewSettings("Rick")
    brain = Brain(FakeLLM(responder), settings, vault, build_brief(vault, "my career"))
    live = live or FakeLiveClient()
    session = InterviewSession(vault=vault, settings=settings, brain=brain, live=live,
                               opening=Opening("my career", "", "Q?"), workdir=tmp_path / "w",
                               now=lambda: datetime(2026, 10, 2, 9, 0), watch_interval_s=3600, close_wait_s=0.2)
    return session, live


def test_static_assets_without_token(vault, tmp_path):
    session, _ = make(vault, tmp_path)
    with TestClient(create_app(session, TOKEN)) as c:
        assert "liber interview" in c.get("/").text
        assert c.get("/app.js").headers["content-type"].startswith("text/javascript")
        assert c.get("/style.css").status_code == 200


@pytest.mark.parametrize("method, path", [("post", "/api/session"), ("post", "/api/resume"), ("post", "/api/note"),
                                          ("post", "/api/hold"), ("get", "/api/status"), ("post", "/api/end")])
def test_api_requires_token(vault, tmp_path, method, path):
    session, _ = make(vault, tmp_path)
    with TestClient(create_app(session, TOKEN)) as c:
        assert getattr(c, method)(path).status_code == 403
        assert getattr(c, method)(path, headers={"X-Liber-Token": "wrong"}).status_code == 403


def test_session_flow(vault, tmp_path):
    session, live = make(vault, tmp_path)
    with TestClient(create_app(session, TOKEN)) as c:
        r = c.post("/api/session", json={"sdp": "OFFER"}, headers=H)
        assert r.status_code == 201 and r.json() == {"sdp": "ANSWER-live_1"}
        assert live.created[0]["sdp"] == "OFFER"
        assert c.post("/api/session", json={"sdp": ""}, headers=H).status_code == 400
        assert c.post("/api/note", json={"text": "typed"}, headers=H).json() == {"ok": True}
        assert any(t.text == "typed" for t in session.assembler.turns())
        assert c.post("/api/note", json={"text": " "}, headers=H).status_code == 409
        status = c.get("/api/status", headers=H).json()
        assert status["state"] == "live" and status["topic"] == "my career"
        assert c.post("/api/resume", json={"sdp": "X"}, headers=H).status_code == 409
        end = c.post("/api/end", headers=H).json()
        assert end["transcript"] is None  # no speech, so no files
        assert c.get("/api/status", headers=H).json()["state"] == "done"


def test_openai_failure_is_502_and_key_never_leaks(vault, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    session, _ = make(vault, tmp_path, FakeLiveClient(fail=LiveError("OpenAI refused the voice session (403): no access")))
    with TestClient(create_app(session, TOKEN)) as c:
        r = c.post("/api/session", json={"sdp": "OFFER"}, headers=H)
        assert r.status_code == 502 and "403" in r.json()["error"]
        for path in ("/", "/app.js"):
            assert "sk-secret-value" not in c.get(path).text
        assert "sk-secret-value" not in r.text


def test_page_has_controls(vault, tmp_path):
    session, _ = make(vault, tmp_path)
    with TestClient(create_app(session, TOKEN)) as c:
        html = c.get("/").text
        for element in ('id="start"', 'id="hold"', 'id="end"', 'id="resume"', 'id="note-form"', 'id="captions"'):
            assert element in html
        js = c.get("/app.js").text
        for needle in ("echoCancellation: true", '"oai-events"', "/api/status", "X-Liber-Token", "history.replaceState"):
            assert needle in js
```

Run: `uv run pytest tests/test_interview_web.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'liber.interview.web'`.

- [ ] **Step 2: Implement web.py**

`src/liber/interview/web.py`:
```python
"""The local interview page and its API (127.0.0.1 only; every /api/* call needs the session token)."""

import hmac
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from liber.errors import LiberError
from liber.interview.live import LiveError
from liber.interview.session import InterviewSession

STATIC_DIR = Path(__file__).parent / "static"


def create_app(session: InterviewSession, token: str, *, static_dir: Path = STATIC_DIR) -> Starlette:
    def authorized(request: Request) -> bool:
        return hmac.compare_digest(request.headers.get("X-Liber-Token", ""), token)

    def forbidden() -> JSONResponse:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    def failure(exc: LiberError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=502 if isinstance(exc, LiveError) else 409)

    async def body_text(request: Request, field: str) -> str:
        try:
            data = await request.json()
        except ValueError:
            return ""
        value = data.get(field) if isinstance(data, dict) else None
        return value if isinstance(value, str) else ""

    async def index(request: Request):
        return FileResponse(static_dir / "index.html", media_type="text/html")

    async def app_js(request: Request):
        return FileResponse(static_dir / "app.js", media_type="text/javascript")

    async def style_css(request: Request):
        return FileResponse(static_dir / "style.css", media_type="text/css")

    async def connect(request: Request, resume: bool):
        if not authorized(request):
            return forbidden()
        sdp = await body_text(request, "sdp")
        if not sdp.strip():
            return JSONResponse({"error": "an SDP offer is required"}, status_code=400)
        try:
            answer = await (session.resume(sdp) if resume else session.start(sdp))
        except LiberError as exc:
            return failure(exc)
        return JSONResponse({"sdp": answer}, status_code=201)

    async def api_session(request: Request):
        return await connect(request, resume=False)

    async def api_resume(request: Request):
        return await connect(request, resume=True)

    async def api_note(request: Request):
        if not authorized(request):
            return forbidden()
        try:
            await session.add_note(await body_text(request, "text"))
        except LiberError as exc:
            return failure(exc)
        return JSONResponse({"ok": True})

    async def api_hold(request: Request):
        if not authorized(request):
            return forbidden()
        try:
            return JSONResponse({"held": await session.toggle_hold()})
        except LiberError as exc:
            return failure(exc)

    async def api_status(request: Request):
        if not authorized(request):
            return forbidden()
        return JSONResponse(session.heartbeat())

    async def api_end(request: Request):
        if not authorized(request):
            return forbidden()
        result = await session.end("ended by user")
        return JSONResponse({
            "transcript": str(result.transcript) if result.transcript else None,
            "notes": str(result.notes) if result.notes else None,
        })

    return Starlette(routes=[
        Route("/", index),
        Route("/app.js", app_js),
        Route("/style.css", style_css),
        Route("/api/session", api_session, methods=["POST"]),
        Route("/api/resume", api_resume, methods=["POST"]),
        Route("/api/note", api_note, methods=["POST"]),
        Route("/api/hold", api_hold, methods=["POST"]),
        Route("/api/status", api_status, methods=["GET"]),
        Route("/api/end", api_end, methods=["POST"]),
    ])
```

- [ ] **Step 3: Write the page**

`src/liber/interview/static/index.html`:
```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>liber interview</title>
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <header>
    <h1>liber interview</h1>
    <p class="meta"><span id="topic">…</span> · <span id="timer">00:00</span></p>
  </header>
  <p class="controls">
    <button id="start">Start</button>
    <button id="hold" disabled>Hold — I'm thinking</button>
    <button id="resume" hidden>Resume</button>
    <button id="end" disabled>End</button>
  </p>
  <p id="status">Click Start and allow the microphone. Speakers are fine; the browser cancels echo.</p>
  <div id="captions" aria-live="polite"></div>
  <form id="note-form">
    <input id="note-text" type="text" maxlength="2000" placeholder="Add a note without interrupting…" disabled />
    <button type="submit" id="note-add" disabled>Add note</button>
  </form>
  <p id="result" hidden></p>
  <audio id="remote" autoplay></audio>
  <script type="module" src="/app.js"></script>
</body>
</html>
```

`src/liber/interview/static/style.css`:
```css
:root { color-scheme: light dark; }
body { font: 16px/1.5 system-ui, sans-serif; max-width: 48rem; margin: 2rem auto; padding: 0 1rem; }
header { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; }
.meta { color: #777; }
button { font-size: 1rem; padding: .5rem 1rem; margin-right: .5rem; }
button.held { background: #ffe9a8; color: #000; }
#status { color: #777; min-height: 1.5em; }
#captions { border: 1px solid #8884; border-radius: 6px; padding: .75rem; height: 55vh; overflow-y: auto; }
#captions p { margin: .4rem 0; }
.me { color: #2a6fb0; } .ai { color: #b06a2a; } .note { font-style: italic; color: #6a8a2a; }
.who { font-weight: 600; margin-right: .4rem; }
#note-form { display: flex; gap: .5rem; margin-top: .75rem; }
#note-text { flex: 1; font-size: 1rem; padding: .5rem; }
#result { padding: .75rem; border-radius: 6px; background: #8882; }
```

`src/liber/interview/static/app.js`:
```js
// liber interview page. Audio runs over WebRTC (with the browser's echo cancellation); captions come from
// the "oai-events" data channel; all controls go through the local liber service.
const params = new URLSearchParams(location.search);
const token = params.get("t") || sessionStorage.getItem("liberToken") || "";
sessionStorage.setItem("liberToken", token);
history.replaceState(null, "", "/"); // keep the token out of the address bar

const $ = (id) => document.getElementById(id);
const el = {
  start: $("start"), hold: $("hold"), resume: $("resume"), end: $("end"), status: $("status"),
  topic: $("topic"), timer: $("timer"), captions: $("captions"), remote: $("remote"),
  noteForm: $("note-form"), noteText: $("note-text"), noteAdd: $("note-add"), result: $("result"),
};

let pc = null, dc = null, mic = null, finished = false;
let current = { who: null, node: null };

function api(path, body, method = "POST") {
  return fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-Liber-Token": token },
    body: method === "GET" ? undefined : JSON.stringify(body || {}),
  });
}

async function errorText(res) {
  try { return (await res.json()).error || res.statusText; } catch { return res.statusText; }
}

function caption(who, text) {
  if (current.who !== who || who === "note") {
    const p = document.createElement("p");
    p.className = who;
    const label = document.createElement("span");
    label.className = "who";
    label.textContent = who === "me" ? "Me" : who === "ai" ? "Interviewer" : "Note";
    p.append(label);
    el.captions.append(p);
    current = { who, node: p };
  }
  current.node.append(document.createTextNode(text));
  el.captions.scrollTop = el.captions.scrollHeight;
}

function setLive(on) {
  el.hold.disabled = el.end.disabled = el.noteText.disabled = el.noteAdd.disabled = !on;
}

function stopAudio() {
  mic?.getTracks().forEach((t) => t.stop());
  dc?.close();
  pc?.close();
  pc = dc = mic = null;
  el.remote.srcObject = null;
}

function onEvent({ data }) {
  let ev;
  try { ev = JSON.parse(data); } catch { return; }
  if (ev.type === "session.input_transcript.delta") caption("me", ev.delta);
  else if (ev.type === "session.output_transcript.delta") caption("ai", ev.delta);
  else if (ev.type === "session.started") {
    el.status.textContent = "Connected — the interviewer will begin shortly.";
    setLive(true);
  }
}

async function connect(path) {
  pc = new RTCPeerConnection();
  pc.addEventListener("track", (e) => {
    el.remote.srcObject = new MediaStream([e.track]);
    el.remote.play().catch(() => { el.status.textContent = "Click anywhere on the page to allow audio playback."; });
  });
  mic = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  for (const track of mic.getAudioTracks()) pc.addTrack(track, mic);
  dc = pc.createDataChannel("oai-events"); // must exist before the offer
  dc.addEventListener("message", onEvent);
  await pc.setLocalDescription(await pc.createOffer());
  if (pc.iceGatheringState !== "complete") {
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("Network setup timed out. Try again.")), 10000);
      pc.addEventListener("icegatheringstatechange", () => {
        if (pc.iceGatheringState === "complete") { clearTimeout(timeout); resolve(); }
      });
    });
  }
  const res = await api(path, { sdp: pc.localDescription.sdp });
  if (!res.ok) throw new Error(await errorText(res));
  const { sdp } = await res.json();
  await pc.setRemoteDescription({ type: "answer", sdp });
}

function explain(err) {
  if (err && err.name === "NotAllowedError") return "Microphone access was denied. Allow it in your browser, then click Start again.";
  if (err && err.name === "NotFoundError") return "No microphone was found. Connect one, then click Start again.";
  return err instanceof Error ? err.message : String(err);
}

async function begin(path, button) {
  button.disabled = true;
  el.status.textContent = "Requesting the microphone…";
  try {
    await connect(path);
  } catch (err) {
    stopAudio();
    button.disabled = false;
    el.status.textContent = explain(err);
  }
}

el.start.addEventListener("click", () => begin("/api/session", el.start));
el.resume.addEventListener("click", () => begin("/api/resume", el.resume));

el.hold.addEventListener("click", async () => {
  const res = await api("/api/hold");
  if (!res.ok) { el.status.textContent = await errorText(res); return; }
  const { held } = await res.json();
  el.hold.textContent = held ? "Resume talking" : "Hold — I'm thinking";
  el.hold.classList.toggle("held", held);
});

el.end.addEventListener("click", async () => {
  setLive(false);
  el.status.textContent = "Writing your transcript and notes…";
  const res = await api("/api/end");
  stopAudio();
  if (res.ok) showResult(await res.json());
  else el.status.textContent = await errorText(res);
});

el.noteForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = el.noteText.value.trim();
  if (!text) return;
  const res = await api("/api/note", { text });
  if (!res.ok) { el.status.textContent = await errorText(res); return; }
  caption("note", text);
  el.noteText.value = "";
});

function showResult(result) {
  finished = true;
  el.start.disabled = true;
  el.resume.hidden = true;
  el.result.hidden = false;
  el.result.textContent = result.transcript
    ? `Saved to your inbox: ${result.transcript}${result.notes ? " and " + result.notes : " (notes failed — see the terminal)"}. Run /ingest when you're ready.`
    : "Nothing was recorded, so no files were written.";
}

function render(status) {
  el.topic.textContent = status.topic + (status.reason ? ` — ${status.reason}` : "");
  const m = Math.floor(status.elapsed_s / 60), s = status.elapsed_s % 60;
  el.timer.textContent = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")} / ${status.max_minutes}:00`;
  if (status.message) el.status.textContent = status.message;
  el.resume.hidden = status.state !== "interrupted";
  if (status.state === "interrupted") { stopAudio(); setLive(false); el.resume.disabled = false; }
  if (status.state === "done" && !finished) { stopAudio(); setLive(false); showResult(status.result || {}); }
}

async function poll() {
  try {
    const res = await api("/api/status", null, "GET");
    if (res.ok) render(await res.json());
  } catch { /* the service may be shutting down */ }
}
poll();
setInterval(poll, 5000);
```

Run: `uv run pytest tests/test_interview_web.py -v`
Expected: all pass.

- [ ] **Step 4: Package data check**

Run: `uv run python -c "from liber.interview.web import STATIC_DIR; print(sorted(p.name for p in STATIC_DIR.iterdir()))"`
Expected: `['app.js', 'index.html', 'style.css']`. Hatchling includes package files under `src/liber`; no config change should be needed.

- [ ] **Step 5: Commit**

```bash
git add src/liber/interview/web.py src/liber/interview/static tests/test_interview_web.py
git commit -m "feat(interview): local interview page and API

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 9: Runner and the `liber interview` command

**Files:**
- Create: `src/liber/interview/runner.py`
- Modify: `src/liber/cli.py` (add the `interview` command)
- Test: `tests/test_interview_cli.py`, `tests/test_interview_live_smoke.py`

**Interfaces:**
- Consumes:
  - all of the above
  - `liber.config.resolve_vault`, `liber.vaultconfig.load_vault_config`
  - `handle_errors`
- Produces:
  - `require_voice_extra() -> None`, which raises `LiberError` with the install command
  - `prepare_settings(minutes: int | None, model: str | None) -> InterviewSettings`
  - `async run_interview(*, topic, continue_last, settings, keys, vault, open_browser: bool, announce: Callable[[str], None], llm=None, live=None) -> InterviewResult`
  - `async run_recover(*, settings, keys, vault, llm=None) -> list[InterviewResult]`
  - `async run_notes(*, transcript, settings, keys, vault, llm=None) -> Path`
  - The CLI command `liber interview [TOPIC] [--continue] [--minutes N] [--model ID] [--no-browser] [--setup] [--notes PATH] [--recover]`

- [ ] **Step 1: Write failing tests**

`tests/test_interview_cli.py`:
```python
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
```

`tests/test_interview_live_smoke.py`:
```python
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
```

Run: `uv run pytest tests/test_interview_cli.py -v`
Expected: FAIL with `ImportError: cannot import name 'runner'`.

- [ ] **Step 2: Implement runner.py**

`src/liber/interview/runner.py`:
```python
"""Wiring a real interview together: settings, Claude, OpenAI Live, the local page and the browser."""

import asyncio
import contextlib
import dataclasses
import secrets
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


async def _serve(app, session: InterviewSession, open_browser: bool, announce: Callable[[str], None]) -> InterviewResult:
    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}/?t={app.state.token}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    announce(f"Interview page: {url}")
    if open_browser:
        webbrowser.open(url)
    finished = asyncio.create_task(session.done.wait())
    await asyncio.wait({serving, finished}, return_when=asyncio.FIRST_COMPLETED)
    result = await session.end("stopped from the terminal") if not session.done.is_set() else session.result
    await asyncio.sleep(1.0)  # let the page fetch its final status
    server.should_exit = True
    with contextlib.suppress(Exception):
        await serving
    finished.cancel()
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


async def run_recover(*, settings: InterviewSettings, keys: VoiceKeys, vault: Path, llm=None) -> list[InterviewResult]:
    llm = llm or AnthropicLLM(keys.anthropic)
    return await recover_interviews(
        vault=vault, settings=settings, brain_for_topic=lambda t: Brain(llm, settings, vault, build_brief(vault, t))
    )


async def run_notes(*, transcript: Path, settings: InterviewSettings, keys: VoiceKeys, vault: Path, llm=None) -> Path:
    llm = llm or AnthropicLLM(keys.anthropic)
    return await regenerate_notes(transcript, Brain(llm, settings, vault, build_brief(vault, None)))
```

- [ ] **Step 3: Add the CLI command**

In `src/liber/cli.py`:
- Add `import asyncio` to the stdlib imports, if it isn't already there.
- Append:
```python
@app.command("interview")
def interview_cmd(
    topic: Annotated[Optional[str], typer.Argument(help="What to talk about; omit and liber picks a gap")] = None,
    continue_last: Annotated[bool, typer.Option("--continue", help="Continue the most recent interview's topic")] = False,
    minutes: Annotated[Optional[int], typer.Option("--minutes", help="Time limit for this interview")] = None,
    model: Annotated[Optional[str], typer.Option("--model", help="Claude model for this interview")] = None,
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Print the page URL instead of opening it")] = False,
    setup: Annotated[bool, typer.Option("--setup", help="Store your first name and API keys")] = False,
    notes: Annotated[Optional[Path], typer.Option("--notes", help="Regenerate notes for this transcript")] = None,
    recover: Annotated[bool, typer.Option("--recover", help="Finish interviews that were cut off")] = False,
) -> None:
    """Have a voice interview that fills your vault (transcript + notes land in inbox/)."""
    _configure_server_logging()
    with handle_errors():
        from liber.interview import runner
        from liber.interview.settings import load_voice_keys, save_voice_setup

        if setup:
            name = typer.prompt("Your first name (the interviewer will use it)")
            openai_key = typer.prompt("OpenAI API key", hide_input=True)
            anthropic_key = typer.prompt("Anthropic API key", hide_input=True)
            save_voice_setup(name, openai_key, anthropic_key)
            typer.echo("Saved. Start an interview with: liber interview")
            return
        if topic and continue_last:
            raise LiberError("give either a topic or --continue, not both")
        runner.require_voice_extra()
        settings = runner.prepare_settings(minutes, model)
        keys = load_voice_keys()
        vault = resolve_vault()
        if recover:
            results = asyncio.run(runner.run_recover(settings=settings, keys=keys, vault=vault))
            if not results:
                typer.echo("No unfinished interviews.")
            for result in results:
                typer.echo(f"Recovered: {result.transcript} {result.notes or '(notes failed)'}")
            return
        if notes is not None:
            path = asyncio.run(runner.run_notes(transcript=notes.expanduser(), settings=settings, keys=keys, vault=vault))
            typer.echo(f"Notes written: {path}")
            return
        result = asyncio.run(runner.run_interview(
            topic=topic, continue_last=continue_last, settings=settings, keys=keys, vault=vault,
            open_browser=not no_browser, announce=typer.echo,
        ))
    if result.transcript is None:
        typer.echo("Nothing was recorded, so no files were written.")
        return
    typer.echo(f"Transcript: {result.transcript}")
    typer.echo(f"Notes: {result.notes}" if result.notes else
               f"Notes failed; retry with: liber interview --notes {result.transcript}")
    typer.echo("Next: open Claude Code in your vault and run /ingest.")
```
- If `LiberError` and `Path` aren't already imported in `cli.py`, add them to the existing import lines.

Run: `uv run pytest -v`
Expected: all pass, and the live smoke test is deselected. Check that output with `uv run pytest -q 2>&1 | tail -2`; it should mention "deselected".

- [ ] **Step 4: Commit**

```bash
git add src/liber/interview/runner.py src/liber/cli.py tests/test_interview_cli.py tests/test_interview_live_smoke.py
git commit -m "feat(interview): liber interview command and runner

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 10: Documentation and final verification

**Files:**
- Modify: `README.md`
- Create: `docs/manual-test/INTERVIEW-CHECKLIST.md`

- [ ] **Step 1: README**

1. In "How it fits together", change `voice transcripts (later)┘` to `voice interviews        ┘`. Keep the diagram's column alignment.
2. Replace the sentence that begins `Planned next: a voice interviewer` with: `Voice interviews (below) let you fill the vault by talking.`
3. Insert this section immediately before `## Connect your AI tools`:
````markdown
## Voice interviews

Talk instead of typing. `liber interview` opens a page in your browser where a voice interviewer asks you questions, follows up, and lets you think. Afterwards a transcript and structured session notes land in `inbox/` for `/ingest`.

**How it works**
- **The voice** is OpenAI's GPT-Live-1, in full duplex: it listens while it talks and handles pauses and interruptions. Audio runs in your browser, which cancels echo, so speakers are fine.
- **The brain** is Claude, running on your machine. It reads your vault, but only up to `personal`, so `private` files never reach either company. It quietly steers the interviewer's follow-ups, answers when the interviewer hands off, and writes the session notes at the end.
- **Cost:** about $0.05 per minute of voice, plus Claude. A 45-minute interview costs roughly $2–4.

**Set up once**
```bash
cd ~/code/python/liber && uv tool install --editable '.[voice]'
liber interview --setup     # your first name, OpenAI key (GPT-Live-1 access), Anthropic key
```
The keys are stored in `~/.config/liber/secrets.toml` (mode 600). `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` override them.

**Interview**
```bash
liber interview "my career"     # or just `liber interview` and it picks a gap and tells you why
liber interview --continue      # pick up the last interview's topic, with its notes as context
liber interview --minutes 20 --model claude-opus-5-5
```
- **Start, Hold, Add a note, Resume, End.** Hold mutes you and tells the interviewer to wait. Add a note puts typed text into the transcript without interrupting. Resume appears if the connection drops.
- **Going back.** Say something like "going back to Acme…" at any time. The notes merge the correction into the original fact.
- **When it ends.** You can click End, say you want to stop, or reach the time limit (45 minutes by default, with a warning 5 minutes before). If you close the tab, the interview is finished for you after a minute.
- **The files.** You get `inbox/interview-<date>-<topic>.md` (the transcript) and `…-notes.md` (new facts, corrections, people, preferences and follow-up questions, each cited with a `[mm:ss]` timestamp). Then run `/ingest`.

**If something goes wrong:**
- `liber interview --notes inbox/interview-….md` regenerates the notes.
- `liber interview --recover` finishes an interview that was cut off, for example by a crash.

Settings live in `~/.config/liber/config.toml` under `[interview]`: `name`, `model`, `notes_model`, `voice`, `max_minutes`, `warn_minutes`.
````
4. Add this row to the end of the table in `## Commands`:
```markdown
| `liber interview [TOPIC] [--continue] [--minutes N] [--model ID] [--no-browser]` | Voice interview; transcript and notes land in `inbox/` (`--setup`, `--notes <file>`, `--recover` for maintenance) |
```
5. In `## Development`, add this sentence at the end of the paragraph: `After changing the interviewer, walk through docs/manual-test/INTERVIEW-CHECKLIST.md; uv run pytest -m live checks your real keys (a few cents).`

- [ ] **Step 2: Manual checklist**

`docs/manual-test/INTERVIEW-CHECKLIST.md`:
```markdown
# Manual test: voice interviews

These need a microphone, real keys and a few dollars of API time. Run through them after changing anything in `src/liber/interview/`.

## Setup
- [ ] `uv run pytest -m live` passes. It confirms GPT-Live-1 access and the Anthropic key.
- [ ] `liber interview --setup` stores your name and keys. `stat -c %a ~/.config/liber/secrets.toml` prints `600`.

## A 10-minute interview
- [ ] `liber interview "my career"` opens the page. The topic shows at the top.
- [ ] Click Start. The interviewer greets you by name and asks the opening question.
- [ ] Pause for 10 seconds in the middle of an answer. It waits.
- [ ] Talk over a question. It stops and listens.
- [ ] Revisit an earlier answer ("Actually, going back to…"). It acknowledges the change and returns to the thread.
- [ ] Ask "what does liber already know about me?". You get a short, accurate answer, with no `private` details.
- [ ] Type a note with Add a note. It appears in the captions.
- [ ] Use Hold, then Resume talking. It stays silent while you're on hold.
- [ ] Click End. The page shows two file paths. The terminal prints them too.

## Files
- [ ] The transcript has `**Me**` and `**Interviewer**` turns with `[mm:ss]` stamps, plus the typed note.
- [ ] The notes have all five sections. The revisited fact appears once, marked *(amended later in the interview)*.
- [ ] `/ingest` in the vault processes both files.

## Failure paths
- [ ] Mid-interview, turn off Wi-Fi for 30 seconds. The page shows Resume. Click it; the interviewer picks up, and the final transcript shows `— resumed —`.
- [ ] Start an interview, speak a few sentences, then close the tab. After about a minute, the files appear in `inbox/`.
- [ ] `liber interview` with no topic names a gap and gives a reason.
- [ ] `liber interview --continue` uses the last interview's topic.
- [ ] Press Ctrl-C in the terminal mid-interview. The files are still written.
```

- [ ] **Step 3: Full verification**

Run: `uv run pytest -q`
Expected: every test passes (the existing ones plus the new interview tests), with the live tests deselected.

Run: `uv run liber interview --help`
Expected: it lists `--continue`, `--minutes`, `--model`, `--no-browser`, `--setup`, `--notes` and `--recover`.

Run this offline page check, using fake services:
```bash
uv run python - <<'EOF'
import asyncio, json
from datetime import datetime
from pathlib import Path
from starlette.testclient import TestClient
import sys; sys.path.insert(0, "tests")
from voice_fakes import FakeLiveClient, FakeLLM, text_reply
from liber.interview.brain import Brain, Opening
from liber.interview.brief import VaultBrief
from liber.interview.session import InterviewSession
from liber.interview.settings import InterviewSettings
from liber.interview.web import create_app
s = InterviewSession(vault=Path("/nonexistent"), settings=InterviewSettings("Rick"),
    brain=Brain(FakeLLM(lambda c: text_reply("{}")), InterviewSettings("Rick"), Path("/nonexistent"), VaultBrief(None, "", "", [], [])),
    live=FakeLiveClient(), opening=Opening("t", "", "q"), workdir=Path("/tmp/liber-check"))
with TestClient(create_app(s, "t")) as c:
    print(c.get("/").status_code, c.get("/api/status").status_code, c.get("/api/status", headers={"X-Liber-Token": "t"}).json()["state"])
EOF
```
Expected: `200 403 idle`.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/manual-test/INTERVIEW-CHECKLIST.md
git commit -m "docs: voice interviews section and manual checklist

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

## After the plan: owner steps (not a subagent task)

1. `cd ~/code/python/liber && uv tool install --editable '.[voice]'`
2. `liber interview --setup`
3. `uv run pytest -m live`
4. Walk through `docs/manual-test/INTERVIEW-CHECKLIST.md`.
