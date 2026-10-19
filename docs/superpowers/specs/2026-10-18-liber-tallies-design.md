# liber — Tallies (`liber stats`) Design

**Date:** 2026-10-18
**Status:** Draft for review
**Builds on:**
- `docs/superpowers/specs/2026-09-30-liber-foundation-design.md` (inbox, `/ingest`, archive)
- `docs/superpowers/specs/2026-10-01-liber-voice-interviewer-design.md` (interview transcripts)
- `liber transcribe` (`src/liber/transcribe.py`)

## 1. Purpose

The owner wants exact, always-current numbers for how much of "me" liber has taken in and holds. They want
them for two reasons:

- **To quote:** in the README, a blog or Twitter post, a talk, or conversation. For example: "liber has read
  534,000 words I've written over 22 years and stores 862 dated facts about me."
- **For curiosity:** seeing the breakdown, and watching it grow month by month.

There are two sides, and both are wanted:

- **Input:** everything processed into liber. An item counts **the moment it lands in the inbox**
  (owner's decision), whether or not it is ingested later.
- **Output:** what is stored as "me" in the vault after `/ingest`.

### 1.1 Success criteria

- `liber stats` prints a one- or two-sentence headline that can be quoted as-is, then an input breakdown, an
  output breakdown and a month-by-month growth table.
- `liber stats --md` prints the same content as Markdown, ready to paste into a README or post.
- Every capture path records itself, so the owner never maintains numbers by hand. That includes capture that
  bypasses the CLI: notes typed into `inbox.md` in Obsidian on the phone, and files dropped into `inbox/`.
- Numbers are exact counts, never estimates. When a number is unknown, it is left out rather than guessed.
- Everything captured before this feature ships is backfilled, so the totals are complete from day one.
- Re-running a command, regenerating interview notes, or running `/ingest` twice never double-counts.

### 1.2 Non-goals

- Billing accuracy. Cost is recorded only when a tool knows it; there is no price table to keep current.
- Charts or a web page. The output is terminal text and Markdown.
- An MCP tool for stats. It could come later.
- Counting anything outside the vault and its capture tools.

### 1.3 Anti-goals

- **A number the owner quotes publicly that turns out wrong.** Exactness matters more than coverage.
- **Leaking private content through stats.** Private facts may be *counted*, but `liber stats` never prints
  their content, file names, or the names of private sources.
- **Bookkeeping chores.** If keeping the tallies right ever needs the owner's attention, the design failed.

## 2. Decisions and their reasons

| Decision | Reason |
|---|---|
| **Input is recorded in a ledger at capture time.** Output is **computed from the vault** on demand | Much of the input never lands in the vault: the 18,000 emails exported and the 4,937 kept, hours of audio, cost. It has to be written down when it happens. Output is all in the vault, so computing it can never drift. |
| **The ledger is `ledger.jsonl` at the vault root**, one JSON object per line, append-only | It is backed up and synced with the vault, and simple to append to from any tool. `liber check` only inspects root `*.md` files, and Obsidian doesn't show `.jsonl`, so the vault stays clean. It isn't exposed through MCP. |
| **One entry per captured inbox item, keyed by its inbox file name** (or by note for `inbox.md` lines) | Makes re-runs and regenerated notes idempotent. Recording an existing key is a no-op. |
| **A catch-up sweep records anything in the inbox without an entry**, dated by the note's own date or the file's mtime. It runs at the start of `/ingest` and in `liber status` | Phone capture through Obsidian never runs a liber command. The sweep makes the ledger complete without the owner doing anything. |
| **Capture commands write the ledger. `/ingest` commits it** with its other commits | Capture commands don't commit today, and shouldn't start. `/ingest` already commits and pushes. |
| **A fact is a list item in a knowledge folder (the `[folders]` in `liber.toml`) that carries at least one `(src: …)` link** | That is how `/ingest` writes every fact (see `core/values.md`). It is countable without parsing prose. |
| **Output growth comes from git history:** for each month, count at the month's last commit | The vault's history is already the record of what was stored when. It needs no extra bookkeeping. |
| **Bulk runs outside the CLI record themselves with `liber ledger record`** | Today's writing analysis was a one-off pipeline. Future runs (or a later `liber style`) need a way in. |
| **Owner words are counted separately from total words** where speakers are labeled | "Words I've written or said" is the quotable number. A podcast transcript's total includes the host. |

## 3. The ledger

Each line is one capture event:

```json
{"date": "2026-10-18", "kind": "talk-audio", "tool": "liber transcribe",
 "key": "inbox/transcript-2016-03-03-a-history-of-site-reliability-engineerin.md",
 "items": 1, "words": 4498, "owner_words": 4157, "audio_seconds": 1385,
 "years": [2016, 2016], "cost_usd": null, "raw": {}, "label": "A History of SRE at Uber"}
```

- **`kind`** is a fixed vocabulary that is extended deliberately: `note`, `document`, `journal`, `email`,
  `talk-audio`, `interview-written`, `voice-interview`, `proposal`, `other`.
- **`items`** is usually 1. A bulk entry carries the count (e.g. 4,937 emails).
- **`raw`** holds what was read to produce the item but isn't stored in it (e.g. `{"emails_exported": 18000,
  "emails_from_owner": 8600}`).
- **`years`** is the span the content covers, when known.
- **`label`** is shown only in local, non-public views (see §5). The `key` is never printed by `--md`.

| Capture path | Records |
|---|---|
| `liber note` | `note`, 1 item, words |
| `liber add` | `document`, per file. Words are filled in once `liber extract` has produced text, by the sweep |
| `liber transcribe` | `talk-audio`, words, owner words (speakers matching the owner's name), audio seconds |
| `liber interview` | `voice-interview`, transcript words, owner words, voice seconds. The notes file shares the transcript's entry, so it isn't counted again |
| MCP `propose_update` | `proposal`, 1 item, words |
| `liber ledger record …` | anything else, with explicit fields (used for bulk runs and the backfill) |
| Sweep (start of `/ingest`, `liber status`) | any unrecorded inbox file or `inbox.md` note, with its kind inferred from name and type |

## 4. Output measures (computed)

From the working tree, using `liber.toml` folders and frontmatter:

- **facts:** total and private (by the file's `sensitivity`)
- **people** (files in the person folder), **interests**, **projects**, and **files** per area
- **words of knowledge:** the body text of knowledge files, excluding frontmatter
- **sources archived:** files in `sources/`, by subfolder
- **earliest dated fact:** the earliest year in a fact's date

Growth repeats the fact, file and word counts at each month's last commit.

## 5. `liber stats`

1. **Headline:** quotable sentences, for example "liber has taken in N words of my writing and speech (H hours
   of talks), spanning Y1–Y2, and stores F facts about me, back to Y0."
2. **Input breakdown:** by kind, with items, words and owner words. "Read to get there" lines come from
   `raw`. Cost totals appear only where recorded.
3. **Output breakdown:** facts (and private count), people, interests, projects, files, words, sources.
4. **Growth:** month by month, words in and facts stored (cumulative and added), with a simple bar.

`--md` prints the same content as Markdown, minus labels and keys. `--json` exists for other tools. Private
facts are counted, never shown.

## 6. Backfill

A one-time `liber ledger backfill` builds entries for everything captured before this feature:

- **Archived sources** (`sources/notes/*`, `sources/documents/*`, `sources/interviews/*`), dated by git add date
- **Items now in the inbox,** through the sweep
- **The 2026-10-18 writing analysis,** whose numbers aren't derivable from the vault. They go in as explicit
  `liber ledger record` entries, from `inbox/proposal-2026-10-18T20-59-44-local.md` and the counts in this
  conversation: 4,937 emails / 452,223 words (18,000 exported, 8,600 from owner), 19 work documents /
  30,807 words, 5 journals / 51,229 words, 2004–2026, $13.82 (see §8.3).

Backfill is idempotent (keyed like everything else), so it can be re-run safely.

## 7. Changes elsewhere

- **`/ingest` skill:** run the sweep first. At the end, commit `ledger.jsonl` if it changed (`ingest: ledger`).
- **`liber init`:** create an empty `ledger.jsonl`.
- **README:** document `liber stats`, the ledger, and what counts as input and output.

## 8. Rules settled after review

These rules close the gaps a builder review found. Each was decided by the design's author ("my call"), not
by the owner. They are listed for the owner to check.

### 8.1 Identity: what makes two captures the same item (my call)

File names are not identity. `free_name` adds `-2` suffixes, and names are reused after archiving.

- **Files:** the key is `sha256:` of the file's bytes. The same file added twice, or the same text arriving
  under a new name, is one item. A different file that reuses an old name is a new item.
- **`liber transcribe`:** the key is `url:` plus the source URL. Re-running it on the same recording is one
  item, even though the transcript text differs slightly between runs.
- **Voice interviews:** the key is the session id. The notes file and regenerated notes share it.
- **`inbox.md` notes:** the key is a hash of the note's normalized text. The sweep records notes only at the
  start of `/ingest`, never in `liber status`. This keeps half-typed phone notes from being counted, since
  `/ingest` archives notes right after reviewing them. `liber note` records its note immediately.
- Entries record both `key` (identity) and `path` (where the item was when recorded). Only `key` is used for
  de-duplication.
- **Backfill covers only items captured before the launch date,** stored as `backfill_cutoff` in the first
  ledger line. It looks items up by content hash, so an item archived after launch (and possibly renamed) is
  still recognized and never counted twice.

### 8.2 What counts as input, and whose words (my call)

- **Owner words** count toward the headline "words of my writing and speech":
  - **Notes, documents, journals and emails** count all their words. The owner wrote them.
  - **Transcripts** (audio and written) count only turns labeled with the owner's name.
  - **AI-written material counts as 0 owner words:** MCP proposals, interview notes, and analysis outputs.
    Proposals still count as items.
- **Derived files are not input.** Files produced *from* material already recorded are recorded as
  `kind: "derived"`, with 0 items and 0 words, for traceability only. Examples are today's
  `facts-from-writing-*`, `writing-style-analysis-*` and `writing-examples-*`, and any file whose first line is
  `<!-- liber proposal: …`. Future bulk tools must write that marker line.
- **The same work captured twice counts once.** The ;login: interview is in both
  `sources/documents/login-fall-2018-boone-interview.pdf` and `inbox/transcript-login-magazine-interview.md`.
  The transcript is the item. The PDF is recorded as `derived` (it is the transcript's original).
- **The owner's name** comes from a new `[owner] name` setting in `~/.config/liber/config.toml`, set to
  "Rick Boone". If it is missing, liber falls back to `[interview] name` with prefix matching. If neither is
  set, owner words for transcripts are left out of the total, and `liber stats` says so.

### 8.3 Backfill values (answered from the analysis run)

The 2026-10-18 numbers are exact counts from the run's working files, not the rounded figures in the
proposal:

- Takeout: 18,000 messages exactly; 8,600 exactly from the owner's address; 4,937 kept, with 452,223 words.
- Documents: 19 work documents with 30,807 words, and 5 journals with 51,229 words.
- Cost: $13.82 (batch reading $10.08, retry $0.36, merging $3.38). This was computed from the API's
  reported token usage at published prices, and is recorded as `cost_basis: "computed from usage"`.

Exact audio durations for the 4 recordings are known from the downloaded files (ffprobe) and go into their
backfill entries (`audio_seconds`): aitx-podcast 7,600; power-of-narrative 3,906; capacity-prediction 1,253;
sre-at-uber 1,385. Total 14,144 s (3.93 h). Today's 9 inbox transcripts are explicit backfill entries, not
sweep entries, so the 5 written interviews get `interview-written` and the 4 recordings get `talk-audio`.
Going forward, `liber transcribe` records `audio_seconds` itself, from yt-dlp's `duration`.

### 8.4 Privacy of labels (my call)

`liber stats` never prints labels, keys or paths, in any mode. Every view shows kinds and counts only, so
there is nothing private to leak. The `label` field stays in the ledger for the owner's own inspection with
`liber ledger list`, which is local-only and never part of stats output.

### 8.5 One writer (answered by the project)

The `liber` CLI and MCP server run only on the desktop. The README says "git runs on the desktop only", and
the cloud tunnel ends at the same machine. The phone only edits files through Obsidian Sync, and the sweep
picks those up. So `ledger.jsonl` has a single writer. Obsidian Sync does copy it, so `liber check` reports a
sync-conflict copy of `ledger.jsonl`, using the existing `conflict_patterns`, and `/ingest` merges it by
union of keys.

### 8.6 Cost (my call, following §1.2)

Tools don't compute prices. `liber transcribe` records `audio_seconds`, and `liber interview` records
`voice_seconds`. Dollar costs appear only in explicit `liber ledger record` entries, such as bulk runs, and
the stats output labels them "recorded cost".

### 8.7 Amendments (my call)

The ledger stays append-only. To change an item later, for example to add words to a `liber add` entry once
`liber extract` has produced text, liber appends a new line with the same key and `"amends": true`. The last
line for a key wins.

## 9. Left to the builder

- Module layout and function names, following the existing style (`inbox.py`, `archive.py`, thin `cli.py`)
- How the sweep infers `kind` from an unrecorded file (filename prefixes like `transcript-`, `interview-`,
  `proposal-`, and suffixes)
- The exact headline wording and table layout, within §5
- Performance of the git-history walk. The vault is small; a month-end checkout of file contents through
  `git show` is fine.
