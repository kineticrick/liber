import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from liber import transcribe
from liber.cli import app
from liber.config import write_user_config
from liber.errors import LiberError
from liber.server.settings import secrets_path, write_secret_values
from liber.transcribe import Recording, Turn


def words(start: int, text: str, step: int = 1000) -> list:
    return [SimpleNamespace(start=start + i * step, text=w) for i, w in enumerate(text.split())]


def utterance(speaker: str, start: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(speaker=speaker, start=start, text=text, words=words(start, text))


def test_parse_speakers_splits_on_commas_and_keeps_order():
    assert transcribe.parse_speakers(" Jake O'Shea, Rick Boone ,") == ["Jake O'Shea", "Rick Boone"]


def test_parse_speakers_requires_a_name():
    with pytest.raises(LiberError, match="at least one"):
        transcribe.parse_speakers(" , ")


def test_parse_speakers_rejects_duplicates():
    with pytest.raises(LiberError, match="twice"):
        transcribe.parse_speakers("Rick, rick")


def test_build_turns_names_unmatched_voices_audience_when_one_speaker():
    turns = transcribe.build_turns(
        [utterance("Rick Boone", 0, "Hello all."), utterance("B", 5000, "A question?")], set(), ["Rick Boone"])
    assert turns == [Turn("Rick Boone", 0, "Hello all."), Turn("Audience", 5000, "A question?")]


def test_build_turns_names_unmatched_voices_unidentified_when_several_speakers():
    turns = transcribe.build_turns([utterance("C", 0, "Hi.")], set(), ["Jake", "Rick"])
    assert turns == [Turn("Unidentified speaker", 0, "Hi.")]


def test_build_turns_splits_long_utterances_at_paragraph_breaks_without_losing_words(monkeypatch):
    monkeypatch.setattr(transcribe, "SPLIT_WORDS", 4)
    u = utterance("Rick", 10_000, "one two three four five six seven")
    # A paragraph that began before the utterance (9_000) must not drop the opening words.
    turns = transcribe.build_turns([u], {9_000, 13_000, 15_000}, ["Rick"])
    assert turns == [
        Turn("Rick", 10_000, "one two three"),
        Turn("Rick", 13_000, "four five"),
        Turn("Rick", 15_000, "six seven"),
    ]


def test_stamp_uses_hours_past_one_hour():
    assert transcribe.stamp(65_000) == "01:05"
    assert transcribe.stamp(3_725_000) == "1:02:05"


RECORDING = Recording(title="AITX Podcast #5", url="https://youtu.be/x", date="2025-09-23", audio=Path("a.m4a"))


def test_render_has_header_turns_and_note():
    text = transcribe.render(
        RECORDING, [Turn("Jake", 0, "Welcome."), Turn("Rick", 3_725_000, "Thanks.")], ["Jake", "Rick"],
        note="Mine this for every section.")
    assert text.startswith("<!-- liber transcript")
    assert "# AITX Podcast #5\n" in text
    assert "- Date: 2025-09-23 (published)\n" in text
    assert "- Source: https://youtu.be/x\n" in text
    assert "- Speakers: Jake, Rick\n" in text
    assert "> **Note for /ingest:** Mine this for every section.\n" in text
    assert text.endswith("**Jake** [00:00]: Welcome.\n\n**Rick** [1:02:05]: Thanks.\n")


def test_render_without_date_or_note_and_one_speaker():
    recording = Recording(title="Talk", url="u", date=None, audio=Path("a"))
    text = transcribe.render(recording, [Turn("Rick", 0, "Hi.")], ["Rick"], note=None)
    assert "- Date:" not in text and "Note for /ingest" not in text
    assert "- Speakers: Rick; other voices are audience members\n" in text


def fake_pipeline(utterances):
    def download(url, workdir):
        audio = workdir / "audio.m4a"
        audio.write_bytes(b"x")
        return Recording(title="AITX Podcast #5", url=url, date="2025-09-23", audio=audio)

    def fake_transcribe(audio, speakers, key):
        assert audio.is_file() and key == "aai-key"
        return utterances, set()

    return download, fake_transcribe


def test_run_transcribe_writes_inbox_file_and_reports_unmatched_names(vault):
    download, fake = fake_pipeline([utterance("Rick", 0, "Hello.")])
    result = transcribe.run_transcribe(
        "https://youtu.be/x", ["Jake", "Rick"], None, vault, "aai-key", download=download, transcribe_audio=fake)
    assert result.path == vault / "inbox" / "transcript-2025-09-23-aitx-podcast-5.md"
    assert "**Rick** [00:00]: Hello." in result.path.read_text(encoding="utf-8")
    assert result.unmatched == ["Jake"]
    again = transcribe.run_transcribe(
        "https://youtu.be/x", ["Rick"], None, vault, "aai-key", download=download, transcribe_audio=fake)
    assert again.path.name == "transcript-2025-09-23-aitx-podcast-5-2.md"


def test_load_key_prefers_environment(monkeypatch):
    write_secret_values({"assemblyai_api_key": "stored"})
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "from-env")
    assert transcribe.load_assemblyai_key() == "from-env"


def test_load_key_from_secrets_file():
    write_secret_values({"assemblyai_api_key": "stored"})
    assert transcribe.load_assemblyai_key() == "stored"


def test_load_key_missing_explains_where_to_put_it():
    with pytest.raises(LiberError, match="assemblyai_api_key"):
        transcribe.load_assemblyai_key()


def test_load_key_refuses_readable_secrets_file():
    write_secret_values({"assemblyai_api_key": "stored"})
    os.chmod(secrets_path(), 0o644)
    with pytest.raises(LiberError, match="chmod 600"):
        transcribe.load_assemblyai_key()


def test_cli_transcribe(vault, monkeypatch):
    write_user_config(vault)
    write_secret_values({"assemblyai_api_key": "aai-key"})
    download, fake = fake_pipeline([utterance("Rick", 0, "Hello.")])
    monkeypatch.setattr(transcribe, "download", download)
    monkeypatch.setattr(transcribe, "transcribe_audio", fake)
    monkeypatch.setattr(transcribe, "require_transcribe_extra", lambda: None)
    result = CliRunner().invoke(app, ["transcribe", "https://youtu.be/x", "--speakers", "Jake, Rick",
                                      "--note", "Use everywhere."])
    assert result.exit_code == 0, result.output
    path = vault / "inbox" / "transcript-2025-09-23-aitx-podcast-5.md"
    assert f"Transcript: {path}" in result.output
    assert "warning: no voice was matched to: Jake" in result.output
    assert "Use everywhere." in path.read_text(encoding="utf-8")


def test_cli_transcribe_requires_speakers(vault):
    result = CliRunner().invoke(app, ["transcribe", "https://youtu.be/x"])
    assert result.exit_code != 0
    assert "--speakers" in result.output
