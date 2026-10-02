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


def _check_secrets_mode() -> None:
    path = secrets_path()
    if path.is_file():
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise LiberError(f"{path} can be read by other users (mode {mode:o}); fix it with: chmod 600 {path}")


def load_voice_keys() -> VoiceKeys:
    _check_secrets_mode()
    stored = read_secret_values()
    openai_key = os.environ.get("OPENAI_API_KEY") or stored.get("openai_api_key", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or stored.get("anthropic_api_key", "")
    missing = [label for label, value in (("OpenAI", openai_key), ("Anthropic", anthropic_key)) if not value]
    if missing:
        raise LiberError(f"missing {' and '.join(missing)} API key. {_SETUP_HINT}")
    return VoiceKeys(openai_key, anthropic_key)


def load_anthropic_key() -> str:
    _check_secrets_mode()
    key = os.environ.get("ANTHROPIC_API_KEY") or read_secret_values().get("anthropic_api_key", "")
    if not key:
        raise LiberError(f"missing Anthropic API key. {_SETUP_HINT}")
    return key


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
