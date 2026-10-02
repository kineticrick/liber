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
