"""`liber transcribe`: download a recording, transcribe it with named speakers, write the transcript to inbox/."""

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from liber.errors import LiberError
from liber.interview.transcript import slugify
from liber.paths import free_name
from liber.server.settings import check_secrets_mode, read_secret_values

SPEECH_MODELS = ["universal-3-5-pro", "universal-2"]
SPLIT_WORDS = 250  # utterances longer than this are split at AssemblyAI's paragraph breaks
AUDIENCE = "Audience"
UNIDENTIFIED = "Unidentified speaker"


@dataclass(frozen=True)
class Recording:
    title: str
    url: str
    date: str | None  # YYYY-MM-DD the recording was published, when the site says
    audio: Path


@dataclass(frozen=True)
class Turn:
    speaker: str
    start_ms: int
    text: str


@dataclass(frozen=True)
class TranscribeResult:
    path: Path
    unmatched: list[str] = field(default_factory=list)


def require_transcribe_extra() -> None:
    try:
        import assemblyai  # noqa: F401
        import yt_dlp  # noqa: F401
    except ImportError as exc:
        raise LiberError("transcription support isn't installed; run: uv tool install --editable '.[transcribe]'") from exc


def load_assemblyai_key() -> str:
    check_secrets_mode()
    key = os.environ.get("ASSEMBLYAI_API_KEY") or read_secret_values().get("assemblyai_api_key", "")
    if not key:
        raise LiberError('missing AssemblyAI API key; add assemblyai_api_key = "…" to ~/.config/liber/secrets.toml')
    return key


def parse_speakers(text: str) -> list[str]:
    names = [name.strip() for name in text.split(",") if name.strip()]
    if not names:
        raise LiberError("give at least one speaker name, e.g. --speakers \"Jake O'Shea, Rick Boone\"")
    seen = set()
    for name in names:
        if name.lower() in seen:
            raise LiberError(f"{name} is listed twice in --speakers")
        seen.add(name.lower())
    return names


def download(url: str, workdir: Path) -> Recording:
    """Fetch the best audio stream with yt-dlp (YouTube, shared Google Drive files and most public sites)."""
    import yt_dlp

    options = {
        "format": "bestaudio/best",
        "outtmpl": str(workdir / "audio.%(ext)s"),
        "js_runtimes": {"deno": {}, "node": {}},  # YouTube needs a JavaScript runtime; use whichever is installed
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise LiberError(f"couldn't download {url}: {exc}") from exc
    files = [p for p in workdir.iterdir() if p.name.startswith("audio.")]
    if not files:
        raise LiberError(f"couldn't download {url}: no audio was saved")
    raw = info.get("upload_date") or ""
    day = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}" if len(raw) == 8 and raw.isdigit() else None
    # The title comes from the web page: keep it to one line so it can't forge header lines such as a note for /ingest.
    title = " ".join((info.get("title") or "").split()) or url
    return Recording(title=title, url=url, date=day, audio=files[0])


def transcribe_audio(audio: Path, speakers: list[str], key: str) -> tuple[list, set[int]]:
    """Utterances labeled with `speakers` where AssemblyAI could match them, and paragraph start times (ms)."""
    import assemblyai as aai

    aai.settings.api_key = key
    config = aai.TranscriptionConfig(
        speech_models=SPEECH_MODELS,
        speaker_labels=True,
        keyterms_prompt=speakers,
        speech_understanding=aai.types.SpeechUnderstandingRequest(request=aai.types.SpeechUnderstandingFeatureRequests(
            speaker_identification=aai.types.SpeakerIdentificationRequest(speaker_type="name", known_values=speakers))),
    )
    transcript = aai.Transcriber(config=config).transcribe(str(audio))
    if transcript.status == aai.TranscriptStatus.error:
        raise LiberError(f"AssemblyAI couldn't transcribe the recording: {transcript.error}")
    breaks = {p.words[0].start for p in transcript.get_paragraphs() if p.words}
    return transcript.utterances or [], breaks


def build_turns(utterances, breaks: set[int], speakers: list[str]) -> list[Turn]:
    """One turn per utterance; long ones split at paragraph breaks, rebuilt from their own words so none are lost."""
    unnamed = AUDIENCE if len(speakers) == 1 else UNIDENTIFIED
    turns = []
    for u in utterances:
        who = u.speaker if u.speaker in speakers else unnamed
        if len(u.words) <= SPLIT_WORDS:
            turns.append(Turn(who, u.start, u.text))
            continue
        current: list = []
        for word in u.words:
            if current and word.start in breaks:
                turns.append(Turn(who, current[0].start, " ".join(w.text for w in current)))
                current = []
            current.append(word)
        turns.append(Turn(who, current[0].start, " ".join(w.text for w in current)))
    return turns


def stamp(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def render(recording: Recording, turns: list[Turn], speakers: list[str], note: str | None) -> str:
    others = "; other voices are audience members" if len(speakers) == 1 else ""
    header = (
        "<!-- liber transcript: AssemblyAI with speaker names; verbatim -->\n"
        f"# {recording.title}\n\n"
        + (f"- Date: {recording.date} (published)\n" if recording.date else "")
        + f"- Source: {recording.url}\n"
        f"- Speakers: {', '.join(speakers)}{others}\n"
        "- Use for: my spoken voice (keep it separate from written style) and any facts about my life and work\n\n"
        + (f"> **Note for /ingest:** {note}\n\n" if note else "")
    )
    return header + "\n\n".join(f"**{t.speaker}** [{stamp(t.start_ms)}]: {t.text}" for t in turns) + "\n"


def run_transcribe(
    url: str,
    speakers: list[str],
    note: str | None,
    vault: Path,
    key: str,
    *,
    download: Callable[[str, Path], Recording] = download,
    transcribe_audio: Callable = transcribe_audio,
) -> TranscribeResult:
    with tempfile.TemporaryDirectory(prefix="liber-transcribe-") as tmp:
        recording = download(url, Path(tmp))
        utterances, breaks = transcribe_audio(recording.audio, speakers, key)
    turns = build_turns(utterances, breaks, speakers)
    inbox = vault / "inbox"
    inbox.mkdir(exist_ok=True)
    stem = "-".join(filter(None, ["transcript", recording.date, slugify(recording.title)]))
    path = inbox / free_name(inbox, f"{stem}.md")
    path.write_text(render(recording, turns, speakers, note), encoding="utf-8")
    heard = {t.speaker for t in turns}
    return TranscribeResult(path, [name for name in speakers if name not in heard])
