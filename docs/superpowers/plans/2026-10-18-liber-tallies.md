# liber Tallies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every capture into an input ledger, count what the vault stores, and report both, with month-by-month growth, through `liber stats`.

**Architecture:**
- **Input:** a new `src/liber/ledger.py` owns `ledger.jsonl` at the vault root. It is append-only JSON lines; the last line for a key wins. Each capture path calls `ledger.record()`. A sweep catches anything that arrived without one.
- **Output:** a new `src/liber/stats.py` computes counts from the working tree, and from git history for growth, then renders text, Markdown or JSON.
- **Interface:** `cli.py` stays thin and gains `stats` and a `ledger` sub-app.

**Tech Stack:** Python 3.12, typer, python-frontmatter, git (subprocess), pytest.

**Spec:** `docs/superpowers/specs/2026-10-18-liber-tallies-design.md`. Read it first. Section 8 holds the binding rules.

## Global Constraints

- The ledger file is `<vault>/ledger.jsonl`: UTF-8, one JSON object per line, append-only. It is never rewritten, except by `ledger merge`, which only appends.
- `kind` is one of: `note`, `document`, `journal`, `email`, `talk-audio`, `interview-written`, `voice-interview`, `proposal`, `derived`, `other`.
- The key prefixes are `sha256:` (file bytes or normalized note text), `url:` (`liber transcribe` source URL) and `session:` (voice interview id).
- AI-written material records `owner_words: 0`: MCP proposals, interview notes and `derived`. A `derived` entry has `items: 0` and `words: 0`.
- Derived marker: a file whose first line starts with `<!-- liber proposal: ` (colon, then a space). MCP proposals start with `<!-- liber proposal -->` and are `proposal`, not `derived`.
- The owner's name comes from `[owner] name` in `user_config_path()`, with `[interview] name` as a prefix-match fallback. In voice-interview transcripts the owner's turns are labeled `**Me**`.
- `liber stats` never prints `label`, `key` or `path`, in any mode. Private facts are counted, and their content is never shown.
- No price tables. Tools record `audio_seconds`/`voice_seconds`. `cost_usd` comes only from explicit `liber ledger record` entries, and is shown as "recorded cost".
- Capture commands never commit. `/ingest` commits `ledger.jsonl` (`ingest: ledger`).
- Numbers are exact counts. If something is unknown, leave it out and never guess.

## Review Focus

1. **A truncated or corrupt last line in `ledger.jsonl`** (crash mid-append, sync glitch). `load()` skips unparseable lines with a warning, and stats still run. Tested in Task 1.
2. **Extraction sidecars** (`inbox/report.pdf.md`) are not separate items. They only amend the original's words. Tested in Task 3.
3. **CRLF line endings and trailing whitespace in phone notes** give the same key as the same note typed on the desktop. Tested in Task 1.
4. **A vault with no `ledger.jsonl`** (created before this feature), and a vault with no git history. `liber stats` works, shows zero input, and suggests `liber ledger backfill`. Growth shows the current month only. Tested in Task 6.
5. **A private source's file name** never appears in `liber stats`, `--md` or `--json`. Tested in Task 6 with a ledger entry labeled `bloodwork.pdf`.

---

### Task 1: Ledger core

**Files:**
- Create: `src/liber/ledger.py`
- Create: `src/liber/templates/vault/ledger.jsonl` (empty file)
- Test: `tests/test_ledger.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Entry`, with the fields `key: str`, `kind: str`, `date: str` (YYYY-MM-DD), `tool: str`, `path: str | None = None`, `items: int = 1`, `words: int | None = None`, `owner_words: int | None = None`, `audio_seconds: int | None = None`, `voice_seconds: int | None = None`, `years: list[int] | None = None`, `cost_usd: float | None = None`, `cost_basis: str | None = None`, `raw: dict = {}` (use `field(default_factory=dict)`), `label: str | None = None`, `files: list[str] | None = None` (the `file_key`s of every inbox file this entry produced) and `amends: bool = False`. `None` fields are omitted when written.
  - `KINDS: frozenset[str]` holds the Global Constraints list. `Entry.__post_init__` raises `LiberError` for an unknown kind.
  - `ledger_path(vault: Path) -> Path`
  - `file_key(path: Path) -> str` returns `"sha256:" + hexdigest of the bytes`.
  - `note_key(text: str) -> str` returns `"sha256:" + hexdigest of " ".join(text.split()).encode()`.
  - `load(vault: Path) -> dict[str, Entry]` returns the last line per key, with amendments merged over the original (non-`None` fields win). It skips bad lines with `logging.warning`, and a missing file gives `{}`.
  - `record(vault: Path, entry: Entry) -> bool` appends only if `entry.key` isn't loaded already, and returns whether it appended.
  - `known_file_keys(entries: dict[str, Entry]) -> set[str]` returns every entry key plus every value in any entry's `files`. The sweep and backfill skip a file whose `file_key` is in this set.
  - `amend(vault: Path, key: str, **fields) -> None` appends `{"key": key, "amends": true, **fields}`. It raises `LiberError` if the key is unknown.
  - `owner_name() -> str | None` reads `[owner] name`, else `[interview] name`, else `None`.
  - `owner_words(markdown: str, owner: str | None) -> int | None` counts the words in turns whose bold label is `Me`, or starts with `owner` (or `owner` starts with the label). It matches both `**Name** [mm:ss]: text` and `**Name:** text`. It returns `None` if `owner` is `None` and there are no `**Me**` turns.
  - `word_count(text: str) -> int` counts `len(text.split())` after stripping YAML frontmatter and HTML comments.

- [ ] **Step 1: Write the failing tests** in `tests/test_ledger.py`:
  - `test_record_is_idempotent`: recording the same key twice gives `True` then `False`, and the file has 1 line.
  - `test_known_file_keys_include_files`: an entry keyed `url:x` with `files=["sha256:f"]` gives `{"url:x", "sha256:f"} <= known_file_keys(load(vault))`.
  - `test_amend_merges_last_wins`: record `Entry(key="sha256:a", kind="document", date="2026-10-18", tool="liber add")`, then `amend(vault, "sha256:a", words=120, owner_words=120)`. `load()` gives `words == 120` and `kind == "document"`.
  - `test_load_skips_corrupt_line`: write a valid line plus `{"key": "sha256:b", "kin`. `load()` returns 1 entry and logs a warning (`caplog`).
  - `test_missing_ledger_is_empty`: `load(tmp_path) == {}`.
  - `test_unknown_kind_rejected`: `Entry(kind="podcast", …)` raises `LiberError`.
  - `test_note_key_ignores_whitespace_and_crlf`: `note_key("buy  milk\r\n") == note_key("buy milk")`.
  - `test_file_key_is_content_based`: two files with the same bytes and different names give equal keys, and different bytes give different keys.
  - `test_owner_words_transcript_formats`: given `"**Jake** [00:01]: hi there\n\n**Rick Boone** [00:05]: one two three\n\n**Rick Boone:** four five"`, with owner `"Rick Boone"` the result is `5`. With owner `"Rick"` (prefix fallback) it is also `5`.
  - `test_owner_words_me_label`: `"**Me** [00:01]: a b\n\n**Interviewer** [00:02]: c"` with owner `None` gives `2`.
  - `test_word_count_strips_frontmatter_and_comments`: `"---\ntype: x\n---\n<!-- c -->\nhello world"` gives `2`.
  - `test_owner_name_prefers_owner_section`: a config with `[owner] name = "Rick Boone"` and `[interview] name = "Rick"` gives `"Rick Boone"`. With only `[interview]` it gives `"Rick"`.
  - In `tests/test_scaffold.py`, add `test_init_creates_empty_ledger`: after `init_vault`, `ledger.jsonl` exists and is empty.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_ledger.py tests/test_scaffold.py -q`
Expected: collection error, `No module named 'liber.ledger'`.

- [ ] **Step 3: Implement `src/liber/ledger.py`** per the Interfaces block, and add the empty template file.
  - Write with `open(path, "a", encoding="utf-8")` and `json.dumps(…, ensure_ascii=False) + "\n"`.
  - Read the config with `tomllib`, from `liber.config.user_config_path()`.

- [ ] **Step 4: Run them and confirm they pass**

Run: `uv run pytest tests/test_ledger.py tests/test_scaffold.py -q`
Expected: all pass. Then run the full suite with `uv run pytest -q`, which should pass too.

- [ ] **Step 5: Commit**

```bash
git add src/liber/ledger.py src/liber/templates/vault/ledger.jsonl tests/test_ledger.py tests/test_scaffold.py
git commit -m "feat(ledger): append-only input ledger with content keys and owner word counts"
```

---

### Task 2: Record at capture time

**Files:**
- Modify: `src/liber/inbox.py` (`add_note`, `add_files`)
- Modify: `src/liber/server/knowledge.py` (`propose`)
- Modify: `src/liber/transcribe.py` (`Recording`, `download`, `run_transcribe`)
- Modify: `src/liber/interview/session.py` (`finalize`)
- Test: `tests/test_inbox.py`, `tests/test_server_search_propose.py`, `tests/test_transcribe.py`, `tests/test_interview_session.py`

**Interfaces:**
- Consumes: `ledger.Entry`, `record`, `file_key`, `note_key`, `owner_words`, `owner_name` and `word_count` from Task 1.
- Produces:
  - `Recording` gains `duration: int | None = None` (seconds, from yt-dlp `info.get("duration")`, rounded).
  - A ledger entry after each capture.
  - `date` is the local date at capture. `tool` is the command name, e.g. `"liber note"` or `"mcp propose_update"`.

| Path | Entry |
|---|---|
| `add_note` | `note`, `key=note_key(text)`, `words=owner_words=word_count(text)` |
| `add_files` | `document` (or `journal` if the file name contains "journal", case-insensitive), `key=file_key(dest)`, `path` relative, `label=dest.name`. Words are `word_count` for `.md`/`.txt`, otherwise `None` (the sweep fills them in later) |
| `propose` | `proposal`, `key=file_key(file)`, `words=word_count(body)`, `owner_words=0` |
| `run_transcribe` | `talk-audio`, `key="url:"+url`, `words=word_count(markdown)`, `owner_words=owner_words(markdown, owner_name())`, `audio_seconds=recording.duration`, `years=[y, y]` from `recording.date`, `label=recording.title`, `files=[file_key(path)]` |
| `finalize` | `voice-interview`, `key="session:"+self.id`, `words` of the transcript, `owner_words` from the `**Me**` turns, `voice_seconds=round(self.voice_seconds)`, `files` = the `file_key`s of the transcript and (if written) the notes. The notes file gets no separate entry |

- [ ] **Step 1: Write the failing tests:**
  - `test_add_note_records_ledger`: after `add_note(vault, "hello there world", now)`, `load(vault)` has one `note` entry with `words == 3`.
  - `test_add_note_same_text_twice_counts_once`: two identical notes give 1 entry, and `inbox.md` still has both lines.
  - `test_add_files_records_by_content`: adding the same file twice (giving `report.pdf` and `report-2.pdf`) gives 1 ledger entry. A different file named `report.pdf`, added after the first was moved away, gives a second entry.
  - `test_add_journal_kind`: adding `Dream Journal.md` records `kind == "journal"` and `words` set.
  - `test_propose_records_proposal_with_zero_owner_words`: after `propose()`, the ledger has `kind == "proposal"` and `owner_words == 0`.
  - `test_run_transcribe_records_ledger`: using the existing `fake_pipeline`, with `Recording` given `duration=1385`, with `[owner] name = "Rick"` written to the user config, and a turn labeled `Rick`. The entry has `kind == "talk-audio"`, `key == "url:https://youtu.be/x"`, `audio_seconds == 1385` and `owner_words == 1`. Running it again leaves 1 entry. Afterwards, `sweep(vault, today, include_notes=False)` (Task 3) records nothing for the transcript file, which pins the no-double-count rule once Task 3 lands. Add this assertion in Task 3's tests instead if `sweep` doesn't exist yet.
  - `test_download_reads_duration`: extend `test_download_keeps_the_page_title_on_one_line`'s fake info with `"duration": 1385.4`, and assert `recording.duration == 1385`.
  - `test_finalize_records_voice_interview`: in the existing session tests' fake setup, after `finalize()` the ledger has a `voice-interview` entry keyed `session:<id>`, with `owner_words` equal to the words in the `ME` turns.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_inbox.py tests/test_server_search_propose.py tests/test_transcribe.py tests/test_interview_session.py -q`
Expected: the new tests fail with `assert {} …` or a missing `duration`.

- [ ] **Step 3: Add the `record(...)` call to each capture path** per the table.
  - In `propose`, record after the file is written, inside the lock.
  - Ledger failures must not lose the capture. Wrap each `record` in `try/except OSError` and log a warning, because the item is already safe in the inbox.

- [ ] **Step 4: Run them and confirm they pass**

Run: the same command as Step 2, then `uv run pytest -q`.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/liber tests
git commit -m "feat(ledger): record notes, documents, proposals, transcripts and interviews at capture"
```

---

### Task 3: The sweep

**Files:**
- Modify: `src/liber/ledger.py` (add `sweep`, `kind_for`)
- Modify: `src/liber/cli.py` (`status_cmd`, and a new `ledger` sub-app with `sweep`)
- Modify: `src/liber/extract.py` (amend words after a successful extraction)
- Test: `tests/test_ledger.py`, `tests/test_extract.py`, `tests/test_cli_shell.py`

**Interfaces:**
- Consumes: Task 1, and `inbox.pending_notes`, `inbox.sidecar_for`, `inbox._is_sidecar` (rename it to the public `is_sidecar`, and update its callers).
- Produces:
  - `kind_for(path: Path, text: str | None) -> str`:
    - The first line starts with `<!-- liber proposal: ` → `derived`.
    - `<!-- liber proposal -->` → `proposal`.
    - `<!-- liber interview transcript -->` → `voice-interview`.
    - The name matches `interview-*-notes.md` → `derived`.
    - `<!-- liber transcript: AssemblyAI` → `talk-audio`.
    - The name starts with `transcript-` → `interview-written`.
    - "journal" in the name → `journal`.
    - Otherwise `document`.
  - `sweep(vault: Path, today: date, include_notes: bool) -> int` returns the number of new entries.
    - Each non-hidden, non-sidecar file in `inbox/` whose `file_key` is not in `known_file_keys(load(vault))` is recorded with the inferred kind, dated by the file's mtime (`tool="sweep"`). Words come from the file or its sidecar. `owner_words` is 0 for `proposal`/`derived`, `owner_words()` for transcripts, and `words` otherwise.
    - If `include_notes`, each `pending_notes()` line without a `note_key` entry is recorded, dated by its own `YYYY-MM-DD` prefix if it has one, else `today`.
    - Existing entries with `words is None` whose sidecar now exists get `amend(words=…, owner_words=…)`.
  - `liber ledger sweep [--notes]` prints `Recorded N new item(s).`
  - `liber status` calls `sweep(include_notes=False)` silently before printing.
  - After a successful extraction, `extract_pending` calls `amend` for that file's key, if the ledger has it.

- [ ] **Step 1: Write the failing tests:**
  - `test_sweep_records_unrecorded_files_and_skips_sidecars`: an inbox with `a.pdf` plus `a.pdf.md` (text "one two") and `b.md` gives `sweep` → `2`. The `a.pdf` entry has `words == 2`, and there is no entry for `a.pdf.md`.
  - `test_sweep_notes_only_when_asked`: an `inbox.md` with `- 2026-10-01 09:00 — phone note here` gives `sweep(include_notes=False) == 0`. Then `sweep(include_notes=True) == 1`, with `date == "2026-10-01"`.
  - `test_sweep_is_idempotent`: a second sweep gives `0`.
  - `test_sweep_skips_files_owned_by_other_entries`: record `Entry(key="url:x", kind="talk-audio", files=[file_key(inbox/t.md)], …)`, and `sweep` doesn't record `t.md`. Do the same for a voice-interview entry owning the transcript and notes files.
  - `test_kind_for_markers`: one assertion per rule above, including `<!-- liber proposal: writing analysis, 2026-10-18 -->` → `derived`, and `<!-- liber proposal -->` → `proposal`.
  - `test_sweep_derived_counts_nothing`: a derived file is recorded with `items == 0` and `words == 0`.
  - `test_extract_amends_words` (in `test_extract.py`): `liber add` a PDF (words `None`), run `extract_pending` with a fake converter returning "a b c", and the ledger entry gives `words == 3`.
  - `test_status_sweeps_files_not_notes` (CLI): after `liber status`, inbox files are in the ledger and notes are not.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_ledger.py tests/test_extract.py tests/test_cli_shell.py -q`
Expected: the new tests fail (`sweep` is not defined).

- [ ] **Step 3: Implement `kind_for`, `sweep`, the CLI wiring and the extract amendment** per the Interfaces block. Read only the first line to detect markers.

- [ ] **Step 4: Run them and confirm they pass**

Run: the same command as Step 2, then `uv run pytest -q`.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/liber tests
git commit -m "feat(ledger): sweep unrecorded inbox items; status and extract keep the ledger current"
```

---

### Task 4: `liber ledger record | list | merge | backfill`

**Files:**
- Modify: `src/liber/ledger.py` (add `backfill`, `merge`)
- Modify: `src/liber/cli.py` (the `ledger` sub-app)
- Test: `tests/test_ledger.py`, `tests/test_cli_shell.py`

**Interfaces:**
- Consumes: Tasks 1 and 3.
- Produces:
  - `liber ledger record --kind K --key KEY [--date D] [--items N] [--words N] [--owner-words N] [--audio-seconds N] [--years Y1-Y2] [--cost-usd X --cost-basis TEXT] [--raw JSON] [--label TEXT] [--path P] [--file PATH ...]` builds an `Entry` (each `--file` adds `file_key(PATH)` to `files`) with `tool="liber ledger record"` and prints `Recorded.` or `Already recorded: KEY`.
  - `liber ledger list` is local inspection. It prints one line per entry (date, kind, items, words, label), oldest first. This is the only place labels are printed.
  - `merge(vault: Path, other: Path) -> int` appends every line of `other` whose key is not already loaded, then deletes `other`. `liber ledger merge <file>` prints `Merged N entr(y|ies).`
  - `backfill(vault: Path, cutoff: date) -> int`:
    1. The first time, it writes a header line `{"key": "meta:backfill", "kind": "other", "items": 0, "date": cutoff, "tool": "backfill", "raw": {"backfill_cutoff": cutoff}}`. Later runs reuse the stored cutoff.
    2. Every file under `sources/` (`notes/`, `documents/`, `interviews/`) whose first git-add date (`git log --diff-filter=A --format=%as -- <path>`, last line) is ≤ the cutoff, and whose `file_key` is not in `known_file_keys`, is recorded with `kind_for`, dated by that git date, with `tool="backfill"`.
    3. A `sources/notes/YYYY-MM.md` file is recorded per note line: one `note` entry per note, keyed by `note_key`.
    4. A `.md` sidecar of an archived binary amends the binary's entry with words.
  - `liber ledger backfill [--cutoff YYYY-MM-DD]` prints `Backfilled N item(s).` The default cutoff is today.

- [ ] **Step 1: Write the failing tests:**
  - `test_record_cli_and_duplicate`: `liber ledger record --kind email --key bulk:takeout-2026-10-18 --items 4937 --words 452223 --owner-words 452223 --years 2004-2026 --raw '{"emails_exported": 18000}'` prints `Recorded.`, and running it again prints `Already recorded`.
  - `test_list_prints_labels`: `ledger list` output contains the label.
  - `test_merge_unions_keys`: a vault ledger with keys {a, b} and a conflict copy with {b, c} gives `merge` → `1`, the keys become {a, b, c}, and the copy is deleted.
  - `test_backfill_records_archived_sources_before_cutoff`: in a git-initialized fixture vault, commit `sources/documents/x.md` and `sources/notes/2026-09.md` (2 note lines). `backfill(cutoff=today)` gives `3`. A second run gives `0`.
  - `test_backfill_skips_items_already_recorded_under_another_path`: record `inbox/x.md` by content, then archive it to `sources/documents/x-2.md` with the same bytes. Backfill adds 0.
  - `test_backfill_respects_cutoff`: a source whose git add date is after the cutoff is not recorded.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_ledger.py tests/test_cli_shell.py -q`
Expected: the new tests fail.

- [ ] **Step 3: Implement it** per the Interfaces block. Run git with `subprocess.run(["git", "-C", vault, …], capture_output=True, text=True)`. Outside a git repo, use the file's mtime date.

- [ ] **Step 4: Run them and confirm they pass**

Run: the same command as Step 2, then `uv run pytest -q`.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/liber tests
git commit -m "feat(ledger): record, list, merge and backfill commands"
```

---

### Task 5: Output counts and growth

**Files:**
- Create: `src/liber/stats.py`
- Test: `tests/test_stats.py`

**Interfaces:**
- Consumes: `docs.iter_content_files`, `docs.read_vault_file`, `vaultconfig.load_vault_config`, and `ledger.load`.
- Produces:
  - `FACT_LINE = re.compile(r"^\s*[-*] .*\(src: ", re.M)`. A fact is a list item in a content file (`iter_content_files`, which already excludes `inbox/` and `sources/`) with at least one `(src: …)`.
  - `@dataclass class Output`, with the fields `facts: int`, `private_facts: int`, `people: int`, `interests: int`, `projects: int`, `files: int`, `words: int`, `sources: dict[str, int]` (count per `sources/` subfolder) and `earliest_year: int | None` (the minimum 4-digit year 1900–2099 found in fact lines).
  - `output_from_files(files: list[tuple[str, str]], cfg) -> Output` is pure and takes `(rel_path, text)` pairs. A file is private if its frontmatter `sensitivity == "private"`. People are files whose folder type is `person`, interests `interest`, projects `project`. Words use `ledger.word_count` on the bodies.
  - `output_now(vault: Path) -> Output` uses the working tree.
  - `growth(vault: Path, today: date) -> list[MonthRow]`, where `MonthRow(month: str, words_in: int, owner_words_in: int, facts: int, facts_added: int)`.
    - The input side groups the ledger by `date[:7]`.
    - The output side, for each month from the first commit's month to today's, uses the last commit of the month (`git rev-list -1 --before=<first day of next month> HEAD`). It reads the content files at that commit (`git ls-tree -r --name-only <sha>`, then `git show <sha>:<path>` for `.md` files in content folders) and calls `output_from_files`.
    - Without git, it returns a single row for the current month from `output_now`.

- [ ] **Step 1: Write the failing tests:**
  - `test_fact_detection`: a file containing `- I like X. (2026-10) (src: [[sources/a.md]])`, `- no source here`, and `Prose (src: [[b]])` not in a list gives `facts == 1`.
  - `test_private_facts_counted`: a fact in a `sensitivity: private` file gives `private_facts == 1`.
  - `test_people_interests_projects_counts`: fixture files in `people/`, `interests/` and `career/projects/` are each counted 1.
  - `test_earliest_year`: facts with `[1995–1999]` and `(2026-10)` give `1995`.
  - `test_sources_by_subfolder`: two files in `sources/documents` and one in `sources/notes` give `{"documents": 2, "notes": 1}`.
  - `test_growth_by_month`: a git fixture with one commit dated 2026-09-15 (1 fact) and one dated 2026-10-10 (3 facts), using `GIT_COMMITTER_DATE`/`GIT_AUTHOR_DATE`, plus ledger entries in both months. The rows are `2026-09` (facts 1, added 1) and `2026-10` (facts 3, added 2), with `words_in` per month.
  - `test_growth_without_git`: one row for the current month.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_stats.py -q`
Expected: collection error, `No module named 'liber.stats'`.

- [ ] **Step 3: Implement `src/liber/stats.py`** per the Interfaces block.

- [ ] **Step 4: Run them and confirm they pass**

Run: `uv run pytest tests/test_stats.py -q`, then `uv run pytest -q`.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/liber/stats.py tests/test_stats.py
git commit -m "feat(stats): count stored facts, people, words and sources, with monthly growth from git"
```

---

### Task 6: `liber stats`

**Files:**
- Modify: `src/liber/stats.py` (add `Report`, `build_report`, `render_text`, `render_markdown`)
- Modify: `src/liber/cli.py` (the `stats` command)
- Test: `tests/test_stats.py`, `tests/test_cli_shell.py`

**Interfaces:**
- Consumes: Tasks 1 and 5.
- Produces:
  - `build_report(vault: Path, today: date) -> dict` is the single source for every renderer, so `--json` prints exactly this. It has these keys:
    - `input`: a list per kind, each with `kind`, `items`, `words`, `owner_words`, `audio_hours` and `voice_hours`. `derived` and the `meta:` entries are excluded from the totals.
    - `read_to_get_there`: the merged `raw` dicts, with values summed by key.
    - `recorded_cost_usd`: the sum, or `None`.
    - `years`: `[min, max]` over the entries' `years`.
    - `output`: the `Output` as a dict.
    - `growth`: the `MonthRow`s as dicts.
    - `has_ledger`: bool.
  - `render_text(report) -> str` and `render_markdown(report) -> str`. There are four sections in this order: the headline, Input, Output, Growth. The growth bar is `█` scaled to the largest month's `facts`, 30 characters wide, with `▏` for any month that has a value above 0.
  - **Headline copy** (fixed). Omit any clause whose number is missing or zero:
    - `liber has taken in {owner_words_total:,} words I've written or said{, plus {audio_hours:.1f} hours of talks}{, spanning {y1}–{y2}}.`
    - `It stores {facts:,} facts about me ({private_facts:,} private){, going back to {earliest_year}}, drawn from {sources_total:,} archived sources.`
  - Without a ledger, the text ends with `No input ledger yet. Run: liber ledger backfill`.
  - `liber stats [--md | --json]`.

- [ ] **Step 1: Write the failing tests:**
  - `test_headline_wording`: with a ledger of 2 entries (owner words 1,000 and 234; `audio_seconds` 7,200; years 2004–2026) and a vault with 3 facts (1 private) back to 1995, the first line of `render_text` is exactly `liber has taken in 1,234 words I've written or said, plus 2.0 hours of talks, spanning 2004–2026.`
  - `test_derived_excluded_from_totals`: a `derived` entry with `words` 999 doesn't change the totals.
  - `test_no_labels_or_paths_anywhere`: a ledger entry with `label="bloodwork.pdf"` and `path="inbox/bloodwork.pdf"`. Neither string appears in `render_text`, `render_markdown` or `json.dumps(build_report(...))`.
  - `test_markdown_has_sections`: `render_markdown` contains `## Input`, `## Output` and `## Growth`, and uses Markdown tables.
  - `test_stats_without_ledger` (CLI): a fresh vault with `ledger.jsonl` deleted gives exit 0 and output containing `liber ledger backfill`.
  - `test_read_to_get_there_sums_raw`: two entries with `raw={"emails_exported": 18000}` and `{"emails_exported": 2}` give `18002`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/test_stats.py tests/test_cli_shell.py -q`
Expected: the new tests fail.

- [ ] **Step 3: Implement it** per the Interfaces block. Keep `cli.py` thin: build the report, choose a renderer, echo.

- [ ] **Step 4: Run them and confirm they pass**

Run: the same command as Step 2, then `uv run pytest -q`.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/liber tests
git commit -m "feat(stats): liber stats with quotable headline, breakdowns and growth (--md, --json)"
```

---

### Task 7: `/ingest`, README and docs

**Files:**
- Modify: `src/liber/skills/ingest/SKILL.md`
- Modify: `README.md`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: the commands from Tasks 3, 4 and 6.
- Produces:
  - **`/ingest` changes:**
    - Preflight step 3 becomes `Run liber ledger sweep --notes, then liber extract, then liber status.`
    - In preflight step 2, if the sync-conflict copy is of `ledger.jsonl`, run `liber ledger merge <copy>` (no owner merge needed).
    - In wrap-up, a new step before the push: `If ledger.jsonl changed, commit it: git add ledger.jsonl && git commit -m "ingest: ledger".`
  - **README changes:**
    - A `## Tallies` section: what counts as input (at inbox arrival) and as output (facts are list items with `(src: …)`), what the ledger is, `liber stats` / `--md` / `--json`, `liber ledger backfill` for vaults that existed before, and `[owner] name` in `~/.config/liber/config.toml`.
    - Add `ledger.jsonl` to the vault layout table.
    - Add command rows for `liber stats` and `liber ledger record/list/merge/backfill/sweep`.

- [ ] **Step 1: Write the failing test** `test_ingest_skill_sweeps_and_commits_ledger`: the ingest `SKILL.md` text contains `liber ledger sweep --notes`, `liber ledger merge` and `ingest: ledger`.

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest tests/test_skills.py -q`
Expected: FAIL.

- [ ] **Step 3: Edit the skill and README** per the Interfaces block, matching each file's existing voice and structure.

- [ ] **Step 4: Run it and confirm it passes**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/liber/skills/ingest/SKILL.md README.md tests/test_skills.py
git commit -m "docs: README and /ingest cover the tallies ledger and liber stats"
```

---

### Task 8: Backfill the owner's vault (done by the controller after merge, with the owner's go-ahead)

This task writes to the real vault and config, so it is not delegated.

- [ ] **Step 1:** Add `[owner]\nname = "Rick Boone"` to `~/.config/liber/config.toml`.
- [ ] **Step 2:** Reinstall: `uv tool install --editable '.[voice,transcribe]' --force`.
- [ ] **Step 3:** Record today's explicit entries with `liber ledger record`, using the exact values in spec §8.3:
  - **Email:** `bulk:takeout-sent-2026-10-18`, `email`, items 4937, words and owner words 452223, years 2004-2026, raw `{"emails_exported": 18000, "emails_from_owner": 8600}`, cost 13.82 with basis "computed from usage" (the whole analysis run, recorded once here).
  - **Work documents:** `bulk:drive-work-2026-10-18`, `document`, items 19, words 30807.
  - **Journals:** `bulk:drive-journals-2026-10-18`, `journal`, items 5, words 51229.
  - **The 4 audio transcripts:** `talk-audio`, keyed `url:` plus their source URLs, with `--files` set to each inbox file's `file_key` (add a repeatable `--file PATH` option to `liber ledger record` in Task 4 that stores `file_key(PATH)` in `files`), `audio_seconds` 7600, 3906, 1253 and 1385, and owner words from `owner_words()` on each inbox file.
  - **The 5 written ones:** `interview-written`, keyed by `file_key`.
  - **The ;login: PDF:** `derived`.
- [ ] **Step 4:** Run `liber ledger backfill`, then `liber ledger sweep --notes`. Confirm in `liber ledger list` that the inbox's analysis outputs are `derived` and that nothing is counted twice.
- [ ] **Step 5:** Run `liber stats`, and check the headline against the known totals (about 534,000 words in, 3.9 hours). Show it to the owner.
