"""The input ledger: an append-only record of what has been fed into the vault."""

import fnmatch
import hashlib
import json
import logging
import re
import subprocess
import tomllib
from datetime import date, datetime
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from liber.config import user_config_path
from liber.errors import LiberError

log = logging.getLogger(__name__)

KINDS = frozenset({
    "note", "document", "journal", "email", "message", "talk-audio", "interview-written",
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


def build_manual_entry(*, kind: str, key: str, day: str, items: int, words: int | None, owner_words: int | None,
                       audio_seconds: int | None, years: str | None, cost_usd: float | None, cost_basis: str | None,
                       raw: str | None, label: str | None, path: str | None, files: list[Path]) -> Entry:
    """Validate the flags of `liber ledger record` and build the Entry."""
    try:
        day = date.fromisoformat(day).isoformat()
    except ValueError:
        raise LiberError(f"--date must be YYYY-MM-DD, not '{day}'")
    span: list[int] | None = None
    if years:
        try:
            parts = [int(p) for p in years.split("-")]
        except ValueError:
            parts = []
        if len(parts) not in (1, 2):
            raise LiberError(f"--years must be Y or Y1-Y2, not '{years}'")
        span = [parts[0], parts[-1]]
    data: dict = {}
    if raw:
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise LiberError(f"--raw is not valid JSON ({exc})")
        if not isinstance(data, dict):
            raise LiberError("--raw must be a JSON object")
    if cost_usd is not None and not cost_basis:
        raise LiberError("--cost-usd needs --cost-basis saying how it was figured")
    return Entry(key=key, kind=kind, date=day, tool="liber ledger record", path=path, items=items, words=words,
                 owner_words=owner_words, audio_seconds=audio_seconds, years=span, cost_usd=cost_usd,
                 cost_basis=cost_basis, raw=data, label=label, files=[file_key(f) for f in files] or None)


def load(vault: Path) -> dict[str, Entry]:
    path = ledger_path(vault)
    if not path.is_file():
        return {}
    known = {f.name for f in fields(Entry)}
    entries: dict[str, Entry] = {}
    # Undecodable bytes become U+FFFD, so the line fails to parse and is skipped like any other bad line.
    for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
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
    if not turns or (owner is None and not has_me):  # no speaker turns at all: whose words is unknown
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
    if first == "<!-- liber interview notes -->" or fnmatch.fnmatchcase(name, "interview-*-notes*.md"):
        return "derived"  # AI-written session notes, including regenerated (-notes-2.md) and renamed copies
    if first.startswith("<!-- liber transcript: AssemblyAI"):
        return "talk-audio"
    if name.startswith("transcript-"):
        return "interview-written"
    if "journal" in name.lower():
        return "journal"
    return "document"


def _read(path: Path) -> str | None:
    """The text of a text file, or of a binary's extracted sidecar; None if there is none yet."""
    from liber.inbox import NO_TEXT_MARKER  # inbox imports this module

    sidecar = path.suffix.lower() not in (".md", ".txt")
    if sidecar:
        path = path.with_name(path.name + ".md")
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    return None if sidecar and NO_TEXT_MARKER in text else text  # "no text found" is not a count of 0


def _words_for(kind: str, text: str | None) -> tuple[int | None, int | None, int]:
    """(words, owner_words, items) for an archived or inboxed text."""
    if kind in ("proposal", "derived"):
        return (0, 0, 0) if kind == "derived" else (word_count(text or ""), 0, 1)
    words = word_count(text) if text is not None else None
    interview = kind in TRANSCRIPT_KINDS
    owner = owner_words(text, owner_name()) if interview and text else words
    return words, owner, 1


TRANSCRIPT_KINDS = frozenset({"talk-audio", "interview-written", "voice-interview"})


_NOTE_LINE = re.compile(r"^-\s*(\d{4}-\d{2}-\d{2})\b[^—]*—\s*(.*)$")


def parse_note_line(line: str) -> tuple[str | None, str]:
    """(date or None, body) of a note line, normalized as sweep and backfill hash it."""
    m = _NOTE_LINE.match(line)
    return (m[1], m[2]) if m else (None, line.lstrip("-* ").strip())


def sweep(vault: Path, today: date, include_notes: bool) -> int:
    """Record inbox items that arrived without a liber command; return how many are new."""
    from liber.inbox import is_sidecar, pending_notes  # inbox imports this module

    entries = load(vault)
    known = known_file_keys(entries)
    by_path = {e.path: e for e in entries.values() if e.path and e.path.startswith("inbox/")}
    recorded = 0
    inbox = vault / "inbox"
    for path in sorted(inbox.iterdir()) if inbox.is_dir() else []:
        if path.name.startswith(".") or not path.is_file() or is_sidecar(path):
            continue
        key = file_key(path)
        if key in known:
            continue
        known.add(key)
        rel = path.relative_to(vault).as_posix()
        text = _read(path)
        if rel in by_path:  # the same inbox file, edited since it was recorded: update it, don't count it twice
            e = by_path[rel]
            words, owner, _ = _words_for(e.kind, text)
            amend(vault, e.key, words=words, owner_words=owner, files=[*(e.files or []), key])
            continue
        kind = kind_for(path, text)
        words, owner, items = _words_for(kind, text)
        day = datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
        recorded += record(vault, Entry(
            key=key, kind=kind, date=day, tool="sweep", path=rel,
            label=path.name, items=items, words=words, owner_words=owner))
    if include_notes:
        for line in pending_notes(vault):
            day, body = parse_note_line(line)
            n = word_count(body)
            recorded += record(vault, Entry(
                key=note_key(body), kind="note", date=day or today.isoformat(), tool="sweep",
                words=n, owner_words=n))
    owner = owner_name()
    for key, e in load(vault).items():
        if not e.path or e.kind in ("proposal", "derived"):
            continue
        if e.words is None and Path(e.path).suffix.lower() not in (".md", ".txt"):
            text = _read(vault / e.path)  # its extracted sidecar, once real text exists
            if text is not None:
                n = word_count(text)
                amend(vault, key, words=n, owner_words=owner_words(text, owner) if e.kind in TRANSCRIPT_KINDS else n)
        elif e.kind in TRANSCRIPT_KINDS and e.owner_words is None and e.tool != "liber ledger record":
            text = _read(vault / e.path)  # owner words were unknown; the owner's name may be set now
            own = owner_words(text, owner) if text is not None else None
            if own is not None:
                amend(vault, key, owner_words=own)
    return recorded


def merge(vault: Path, other: Path) -> int:
    """Append the entries of a sync-conflict copy whose keys are new; delete the copy."""
    have = set(load(vault))
    added: set[str] = set()
    for n, line in enumerate(other.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            key = obj["key"]
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("%s:%d: skipping bad ledger line (%s)", other, n, exc)
            continue
        if key in have:
            continue
        added.add(key)
        _append(vault, obj)
    other.unlink()
    return len(added)


def _git_added_date(vault: Path, path: Path) -> str:
    rel = path.relative_to(vault).as_posix()
    result = subprocess.run(["git", "-C", str(vault), "log", "--diff-filter=A", "--no-renames", "--format=%as", "--", rel],
                            capture_output=True, text=True)
    lines = result.stdout.split() if result.returncode == 0 else []
    return lines[-1] if lines else datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()


def backfill(vault: Path, cutoff: date) -> int:
    """Record everything archived in sources/ on or before the cutoff; return how many items (not derived) are new."""
    from liber.inbox import _is_note_line, is_sidecar  # inbox imports this module

    entries = load(vault)
    keys = known_file_keys(entries)

    def put(entry: Entry) -> None:
        _append(vault, {k: v for k, v in asdict(entry).items() if v is not None and k != "amends"})
        keys.add(entry.key)

    if "meta:backfill" in entries:
        stored = entries["meta:backfill"].raw.get("backfill_cutoff")
        cutoff = date.fromisoformat(stored) if stored else cutoff
    else:
        put(Entry(key="meta:backfill", kind="other", items=0, date=cutoff.isoformat(), tool="backfill",
                  raw={"backfill_cutoff": cutoff.isoformat()}))
    recorded = 0
    sources = vault / "sources"
    for path in sorted(p for p in sources.rglob("*") if p.is_file()) if sources.is_dir() else []:
        if path.name.startswith(".") or is_sidecar(path):
            continue
        day = _git_added_date(vault, path)
        if day > cutoff.isoformat():
            continue
        rel = path.relative_to(vault).as_posix()
        if path.parent.name == "notes" and path.parent.parent.name == "sources" and re.fullmatch(r"\d{4}-\d{2}\.md", path.name):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not _is_note_line(line):
                    continue
                note_day, body = parse_note_line(line.strip())
                key = note_key(body)
                if key in keys:
                    continue
                n = word_count(body)
                put(Entry(key=key, kind="note", date=note_day or day, tool="backfill", path=rel, words=n, owner_words=n))
                recorded += 1
            continue
        key = file_key(path)
        if key in keys:
            continue
        text = _read(path)
        kind = kind_for(path, text)
        words, owner, items = _words_for(kind, text) if path.suffix.lower() in (".md", ".txt") else (None, None, 1)
        put(Entry(key=key, kind=kind, date=day, tool="backfill", path=rel, label=path.name,
                  items=items, words=words, owner_words=owner))
        recorded += kind != "derived"  # derived files are recorded for traceability but are not items
        if words is None and text is not None:
            n = word_count(text)
            _append(vault, {"key": key, "amends": True, "words": n,
                            "owner_words": _words_for(kind, text)[1]})
    return recorded
