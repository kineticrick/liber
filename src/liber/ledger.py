"""The input ledger: an append-only record of what has been fed into the vault."""

import hashlib
import json
import logging
import re
import tomllib
from datetime import date, datetime
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from liber.config import user_config_path
from liber.errors import LiberError

log = logging.getLogger(__name__)

KINDS = frozenset({
    "note", "document", "journal", "email", "talk-audio", "interview-written",
    "voice-interview", "proposal", "derived", "other",
})


@dataclass(frozen=True)
class Entry:
    key: str
    kind: str
    date: str
    tool: str
    path: str | None = None
    items: int = 1
    words: int | None = None
    owner_words: int | None = None
    audio_seconds: int | None = None
    voice_seconds: int | None = None
    years: list[int] | None = None
    cost_usd: float | None = None
    cost_basis: str | None = None
    raw: dict = field(default_factory=dict)
    label: str | None = None
    files: list[str] | None = None
    amends: bool = False

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise LiberError(f"unknown ledger kind '{self.kind}'")


def ledger_path(vault: Path) -> Path:
    return vault / "ledger.jsonl"


def file_key(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def note_key(text: str) -> str:
    return "sha256:" + hashlib.sha256(" ".join(text.split()).encode()).hexdigest()


def load(vault: Path) -> dict[str, Entry]:
    path = ledger_path(vault)
    if not path.is_file():
        return {}
    known = {f.name for f in fields(Entry)}
    entries: dict[str, Entry] = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            key = data["key"]
            if data.get("amends"):
                if key not in entries:
                    log.warning("%s:%d: amendment for unknown key %s ignored", path, n, key)
                    continue
                overlay = {k: v for k, v in data.items() if k in known and k not in ("key", "amends") and v is not None}
                entries[key] = replace(entries[key], **overlay)
            else:
                entries[key] = Entry(**{k: v for k, v in data.items() if k in known})
        except (ValueError, KeyError, TypeError, AttributeError, LiberError) as exc:
            log.warning("%s:%d: skipping bad ledger line (%s)", path, n, exc)
    return entries


def _append(vault: Path, obj: dict) -> None:
    path = ledger_path(vault)
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    if path.is_file() and path.stat().st_size:
        with path.open("rb") as f:
            f.seek(-1, 2)
            if f.read(1) != b"\n":  # a crash left a partial last line; don't glue this record onto it
                line = "\n" + line
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def record(vault: Path, entry: Entry) -> bool:
    if entry.key in load(vault):
        return False
    _append(vault, {k: v for k, v in asdict(entry).items() if v is not None and k != "amends"})
    return True


def known_file_keys(entries: dict[str, Entry]) -> set[str]:
    keys = set(entries)
    for e in entries.values():
        keys.update(e.files or [])
    return keys


def amend(vault: Path, key: str, **fields_: object) -> None:
    if key not in load(vault):
        raise LiberError(f"no ledger entry for {key}")
    _append(vault, {"key": key, "amends": True, **fields_})


def owner_name() -> str | None:
    path = user_config_path()
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return None
    for section in ("owner", "interview"):
        name = data.get(section, {}).get("name") if isinstance(data.get(section), dict) else None
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


_TURN = re.compile(r"^\*\*(?P<a>[^*]+?)(?::\*\*|\*\*(?:\s*\[[^\]]*\])?\s*:)\s*(?P<text>.*)$")


def _is_owner(label: str, owner: str | None) -> bool:
    label = label.strip().lower()
    if label == "me":
        return True
    if not owner:
        return False
    o = owner.strip().lower()
    return label.startswith(o) or o.startswith(label)


def owner_words(markdown: str, owner: str | None) -> int | None:
    turns: list[tuple[str, list[str]]] = []
    for line in markdown.splitlines():
        m = _TURN.match(line.strip())
        if m:
            turns.append((m["a"], [m["text"]]))
        elif turns:
            turns[-1][1].append(line)
    has_me = any(label.strip().lower() == "me" for label, _ in turns)
    if owner is None and not has_me:
        return None
    return sum(len(" ".join(body).split()) for label, body in turns if _is_owner(label, owner))


_FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def word_count(text: str) -> int:
    text = _FRONTMATTER.sub("", text, count=1)
    return len(_COMMENT.sub("", text).split())


def kind_for(path: Path, text: str | None) -> str:
    first = (text or "").split("\n", 1)[0].strip()
    name = path.name
    if first.startswith("<!-- liber proposal: "):
        return "derived"
    if first == "<!-- liber proposal -->":
        return "proposal"
    if first == "<!-- liber interview transcript -->":
        return "voice-interview"
    if name.startswith("interview-") and name.endswith("-notes.md"):
        return "derived"
    if first.startswith("<!-- liber transcript: AssemblyAI"):
        return "talk-audio"
    if name.startswith("transcript-"):
        return "interview-written"
    if "journal" in name.lower():
        return "journal"
    return "document"


def _read(path: Path) -> str | None:
    if path.suffix.lower() not in (".md", ".txt"):
        path = path.with_name(path.name + ".md")
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def sweep(vault: Path, today: date, include_notes: bool) -> int:
    """Record inbox items that arrived without a liber command; return how many are new."""
    from liber.inbox import is_sidecar, pending_notes  # inbox imports this module

    known = known_file_keys(load(vault))
    recorded = 0
    inbox = vault / "inbox"
    for path in sorted(inbox.iterdir()) if inbox.is_dir() else []:
        if path.name.startswith(".") or not path.is_file() or is_sidecar(path):
            continue
        key = file_key(path)
        if key in known:
            continue
        text = _read(path)
        kind = kind_for(path, text)
        if kind in ("proposal", "derived"):
            words, owner, items = (0, 0, 0) if kind == "derived" else (word_count(text or ""), 0, 1)
        else:
            words = word_count(text) if text is not None else None
            owner = owner_words(text, owner_name()) if kind in ("talk-audio", "interview-written", "voice-interview") and text else words
            items = 1
        day = datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
        known.add(key)
        recorded += record(vault, Entry(
            key=key, kind=kind, date=day, tool="sweep", path=path.relative_to(vault).as_posix(),
            label=path.name, items=items, words=words, owner_words=owner))
    if include_notes:
        for line in pending_notes(vault):
            m = re.match(r"^-\s*(\d{4}-\d{2}-\d{2})\b[^—]*—\s*(.*)$", line)
            body = m[2] if m else line.lstrip("-* ").strip()
            n = word_count(body)
            recorded += record(vault, Entry(
                key=note_key(body), kind="note", date=m[1] if m else today.isoformat(), tool="sweep",
                words=n, owner_words=n))
    for key, e in load(vault).items():
        if e.words is None and e.path:
            sidecar = vault / (e.path + ".md")
            if sidecar.is_file():
                text = sidecar.read_text(encoding="utf-8", errors="replace")
                n = word_count(text)
                amend(vault, key, words=n, owner_words=owner_words(text, owner_name()) if e.kind in ("talk-audio", "interview-written", "voice-interview") else n)
    return recorded
