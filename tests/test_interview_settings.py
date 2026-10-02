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
