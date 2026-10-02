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
