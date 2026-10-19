"""Counting what the vault stores (from the working tree) and how it grew (from git history)."""

import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import frontmatter

from liber import ledger
from liber.docs import is_content_rel, iter_content_files
from liber.vaultconfig import VaultConfig, load_vault_config

FACT_LINE = re.compile(r"^\s*[-*] .*\(src: ", re.M)
_SRC = re.compile(r"\(src:.*?\)")
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


@dataclass
class Output:
    facts: int = 0
    private_facts: int = 0
    people: int = 0
    interests: int = 0
    projects: int = 0
    files: int = 0
    words: int = 0
    sources: dict[str, int] = field(default_factory=dict)
    earliest_year: int | None = None


@dataclass(frozen=True)
class MonthRow:
    month: str
    words_in: int
    owner_words_in: int
    facts: int
    facts_added: int


def _meta(text: str) -> dict:
    try:
        return dict(frontmatter.loads(text).metadata)
    except Exception:  # malformed YAML: count the file, treat it as having no metadata
        return {}


def output_from_files(files: list[tuple[str, str]], cfg: VaultConfig) -> Output:
    """Count facts, people and words in (relative path, text) pairs of content files."""
    out = Output()
    years: list[int] = []
    counted = {"person": "people", "interest": "interests", "project": "projects"}
    for rel, text in files:
        out.files += 1
        out.words += ledger.word_count(text)
        kind = cfg.type_for(rel.rpartition("/")[0])
        if kind in counted:
            setattr(out, counted[kind], getattr(out, counted[kind]) + 1)
        private = _meta(text).get("sensitivity") == "private"
        for line in text.splitlines():
            if not FACT_LINE.search(line):
                continue
            out.facts += 1
            out.private_facts += private
            years += [int(y) for y in _YEAR.findall(_SRC.sub("", line))]
    out.earliest_year = min(years) if years else None
    return out


def _count_sources(vault: Path) -> dict[str, int]:
    from liber.inbox import is_sidecar  # inbox imports ledger; keep this module light

    root = vault / "sources"
    counts: dict[str, int] = {}
    if not root.is_dir():
        return counts
    for sub in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        n = sum(1 for f in sub.rglob("*")
                if f.is_file() and not any(part.startswith(".") for part in f.relative_to(sub).parts)
                and not is_sidecar(f))
        if n:
            counts[sub.name] = n
    return counts


def output_now(vault: Path) -> Output:
    cfg = load_vault_config(vault)
    out = output_from_files([(f.rel, f.text) for f in iter_content_files(vault)], cfg)
    out.sources = _count_sources(vault)
    return out


def _git(vault: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _next_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y + (m == 12):04d}-{m % 12 + 1:02d}"


def _facts_at(vault: Path, sha: str, cfg: VaultConfig) -> int | None:
    names = _git(vault, "ls-tree", "-r", "-z", "--name-only", sha)
    if names is None:
        return None
    files = []
    for name in names.split("\0"):
        if not is_content_rel(name):
            continue
        text = _git(vault, "show", f"{sha}:{name}")
        if text is not None:
            files.append((name, text))
    return output_from_files(files, cfg).facts


def _words_by_month(vault: Path) -> dict[str, tuple[int, int]]:
    sums: dict[str, tuple[int, int]] = {}
    for key, e in ledger.load(vault).items():
        if e.kind == "derived" or key.startswith("meta:"):
            continue
        w, o = sums.get(e.date[:7], (0, 0))
        sums[e.date[:7]] = (w + (e.words or 0), o + (e.owner_words or 0))
    return sums


def growth(vault: Path, today: date) -> list[MonthRow]:
    """One row per month from the first commit to today. Without git history, just the current month."""
    cfg = load_vault_config(vault)
    this_month = today.isoformat()[:7]
    inputs = _words_by_month(vault)
    # Group commits by their own local month and take each month's latest, so labels and selection share one frame.
    log = _git(vault, "log", "--format=%ct %H %cI")
    last: dict[str, tuple[int, str]] = {}
    for line in (log or "").splitlines():
        ts, sha, iso = line.split()
        month = iso[:7]
        if month not in last or int(ts) > last[month][0]:
            last[month] = (int(ts), sha)
    months = sorted(last)
    if not months:
        facts = output_now(vault).facts
        w, o = inputs.get(this_month, (0, 0))
        return [MonthRow(this_month, w, o, facts, facts)]
    rows: list[MonthRow] = []
    previous = 0
    month = months[0]
    while month <= this_month:
        facts = _facts_at(vault, last[month][1], cfg) if month in last else None
        if facts is None:
            facts = previous
        w, o = inputs.get(month, (0, 0))
        rows.append(MonthRow(month, w, o, facts, facts - previous))
        previous = facts
        month = _next_month(month)
    return rows


# --- the report: one dict feeds every renderer ---

Report = dict  # the shape build_report returns; --json prints it as is

BAR_WIDTH = 30


def build_report(vault: Path, today: date) -> Report:
    entries = {k: e for k, e in ledger.load(vault).items() if e.kind != "derived" and not k.startswith("meta:")}
    kinds: dict[str, dict] = {}
    raw: dict = {}
    costs: list[float] = []
    years: list[int] = []
    unknown = 0
    for e in entries.values():
        row = kinds.setdefault(e.kind, dict(kind=e.kind, items=0, words=0, owner_words=0, audio_seconds=0, voice_seconds=0))
        row["items"] += e.items
        row["words"] += e.words or 0
        row["owner_words"] += e.owner_words or 0
        row["audio_seconds"] += e.audio_seconds or 0
        row["voice_seconds"] += e.voice_seconds or 0
        unknown += e.words is not None and e.owner_words is None
        for k, v in e.raw.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                raw[k] = raw.get(k, 0) + v  # numeric values only; anything else is never guessed at
        if e.cost_usd is not None:
            costs.append(e.cost_usd)
        years += e.years or []
    input_ = [dict(kind=r["kind"], items=r["items"], words=r["words"], owner_words=r["owner_words"],
                   audio_hours=r["audio_seconds"] / 3600, voice_hours=r["voice_seconds"] / 3600)
              for r in sorted(kinds.values(), key=lambda r: r["kind"])]
    out = output_now(vault)
    return dict(
        input=input_, read_to_get_there=raw,
        recorded_cost_usd=round(sum(costs), 2) if costs else None,
        years=[min(years), max(years)] if years else None,
        owner_words_unknown=unknown,
        output=asdict(out), growth=[asdict(r) for r in growth(vault, today)],
        has_ledger=ledger.ledger_path(vault).is_file(),
    )


def _plural(n: int, noun: str) -> str:
    return noun if n == 1 else noun + "s"


def _headline(r: Report) -> list[str]:
    owner_words = sum(k["owner_words"] for k in r["input"])
    hours = sum(k["audio_hours"] for k in r["input"])
    lines = []
    if owner_words:
        s = f"liber has taken in {owner_words:,} {_plural(owner_words, 'word')} I've written or said"
        if round(hours, 1) > 0:
            s += f", plus {hours:.1f} {'hour' if round(hours, 1) == 1 else 'hours'} of talks"
        if r["years"]:
            s += f", spanning {r['years'][0]}–{r['years'][1]}"
        lines.append(s + ".")
    o = r["output"]
    s = f"It stores {o['facts']:,} {_plural(o['facts'], 'fact')} about me ({o['private_facts']:,} private)"
    if o["earliest_year"]:
        s += f", going back to {o['earliest_year']}"
    sources = sum(o["sources"].values())
    if sources:
        s += f", drawn from {sources:,} archived {_plural(sources, 'source')}"
    lines.append(s + ".")
    if r["owner_words_unknown"]:
        lines.append(f"Note: {r['owner_words_unknown']} item(s) have no owner word count yet "
                     "(set [owner] name in ~/.config/liber/config.toml).")
    return lines


def _bar(value: int, top: int) -> str:
    if value <= 0 or top <= 0:
        return ""
    return "█" * (value * BAR_WIDTH // top) or "▏"


def _input_cells(r: Report) -> list[list[str]]:
    rows = []
    for k in r["input"]:
        extra = []
        if k["audio_hours"]:
            extra.append(f"{k['audio_hours']:.1f} h audio")
        if k["voice_hours"]:
            extra.append(f"{k['voice_hours']:.1f} h voice")
        rows.append([k["kind"], f"{k['items']:,}", f"{k['words']:,}", f"{k['owner_words']:,}", ", ".join(extra)])
    return rows


def _output_cells(r: Report) -> list[list[str]]:
    o = r["output"]
    rows = [["facts", f"{o['facts']:,}"], ["private facts", f"{o['private_facts']:,}"],
            ["people", f"{o['people']:,}"], ["interests", f"{o['interests']:,}"],
            ["projects", f"{o['projects']:,}"], ["files", f"{o['files']:,}"], ["words", f"{o['words']:,}"]]
    rows += [[f"sources/{name}", f"{n:,}"] for name, n in sorted(o["sources"].items())]
    return rows


def _extras(r: Report) -> list[str]:
    lines = [f"{k.replace('_', ' ')}: {v:,}" if isinstance(v, int) else f"{k.replace('_', ' ')}: {v}"
             for k, v in sorted(r["read_to_get_there"].items())]
    if r["recorded_cost_usd"] is not None:
        lines.append(f"recorded cost: ${r['recorded_cost_usd']:,.2f}")
    return lines


def _growth_cells(r: Report) -> list[list[str]]:
    top = max((g["facts"] for g in r["growth"]), default=0)
    return [[g["month"], _bar(g["facts"], top), f"{g['facts']:,}", f"{g['facts_added']:+,}", f"{g['owner_words_in']:,}"]
            for g in r["growth"]]


NO_LEDGER = "No input ledger yet. Run: liber ledger backfill"


def render_text(r: Report) -> str:
    out = _headline(r) + [""]
    out.append("Input")
    if r["has_ledger"]:
        out += [f"  {c[0]:<18}{c[1]:>8} items {c[2]:>12} words {c[3]:>12} mine  {c[4]}".rstrip() for c in _input_cells(r)]
        out += [f"  {e}" for e in _extras(r)]
    out += ["", "Output"] + [f"  {c[0]:<24}{c[1]:>10}" for c in _output_cells(r)]
    out += ["", "Growth (facts per month)"]
    out += [f"  {c[0]}  {c[1]:<{BAR_WIDTH}}  {c[2]:>7} ({c[3]}), {c[4]} of my words in".rstrip() for c in _growth_cells(r)]
    if not r["has_ledger"]:
        out += ["", NO_LEDGER]
    return "\n".join(out) + "\n"


def _table(head: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in head) + " |"] + \
           ["| " + " | ".join(c.replace("|", "\\|") for c in row) + " |" for row in rows]


def render_markdown(r: Report) -> str:
    out: list[str] = []
    for line in _headline(r):
        out += [line, ""]
    out += ["## Input", ""]
    if r["has_ledger"]:
        out += _table(["Kind", "Items", "Words", "My words", "Audio / voice"], _input_cells(r)) + [""]
        out += [f"- {e}" for e in _extras(r)] + ([""] if _extras(r) else [])
    else:
        out += [NO_LEDGER, ""]
    out += ["## Output", ""] + _table(["What", "Count"], _output_cells(r)) + [""]
    out += ["## Growth", ""] + _table(["Month", "Facts", "Total", "Added", "My words in"],
                                      [[c[0], c[1], c[2], c[3], c[4]] for c in _growth_cells(r)]) + [""]
    return "\n".join(out)
