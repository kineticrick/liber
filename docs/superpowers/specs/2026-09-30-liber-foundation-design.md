# liber — Foundation, Inbox & Ingest (Sub-project 1) Design

**Date:** 2026-09-30
**Status:** Draft for review

## 1. Purpose

liber is a personal knowledge base about one person (the user): work history, life
details, likes and dislikes, passions, expertise, friends, and professional network.
Any LLM, agent, or agentic tool should be able to use it as "context about me," so
the user never has to re-explain their life. The motivating first use is asking an
LLM for ideas on new revenue sources, which only works well with deep personal
context.

It must be easy to add to and edit, and it must grow over time.

### 1.1 The name

**liber** comes from Carl Jung's *Liber Novus* ("The New Book"), known as *The Red
Book*. Jung spent about 16 years writing it as a private, ever-growing record of his
inner life. It was not a public face and was not meant for publication. That is the
spirit of this project: an honest, accumulating record of a whole person. The user's
interest in Jungian psychology prompted the name. Two alternatives were considered and
rejected:

- *persona*: in Jung's usage, the mask shown to the world, which is the opposite of
  what is being built. LLMs also read "persona" as a cue to role-play.
- *anima*: in Jung's system, strictly the contrasexual archetype, one part of the psyche
  rather than the whole Self.

**The README must include this derivation.**

### 1.2 Overall system and where this spec fits

liber is built as four sub-projects, each with its own spec, plan, and build:

1. **Foundation, inbox & ingest (this spec).** Vault structure, conventions, the
   `liber` CLI, and the Claude Code `/ingest` and `/review` skills.
2. **Access server.** An MCP server (`get_profile`, `search`, `read`, `propose_update`)
   with sensitivity filtering. Future spec.
3. **Automatic ingest (API mode).** Optional, for when the voice interviewer lands. Future.
4. **Voice interviewer.** OpenAI GPT-Live-1 for full-duplex conversation, with Claude as
   the delegated backend "brain" calling the access server. Future spec. Its only
   contract with this sub-project is that **finished transcripts are dropped into
   `inbox/`**, where ingest handles them like any other document.

### 1.3 Success criteria for this sub-project

- The user can create a vault with one command and open it in Obsidian on desktop and on an
  Android phone, synced.
- Quick notes (typed on phone or desktop) and whole documents (md, txt, pdf, docx,
  html) can be captured in seconds.
- `/ingest` in Claude Code turns captured items into approved, sourced, dated edits in
  the right files, with one git commit per item.
- `liber bundle` produces a single Markdown document that can be pasted into any chatbot
  (for example, for the revenue-ideas question) and that respects a sensitivity ceiling.
- The structure can evolve: Claude proposes structural changes, the user approves them,
  and the tooling adapts without code changes.

### 1.4 Non-goals (this sub-project)

MCP server, search beyond Obsidian and grep, embeddings, voice, OCR, web UI,
multi-user use, automatic or unattended ingest.

## 2. Repositories

| Repo | Path | Contents |
|---|---|---|
| Code | `~/code/python/liber` (currently `persona/`, to be renamed) | Python CLI, skills, tests, README |
| Vault | `~/liber-vault` (user-chosen) | The user's content: private git repo and Obsidian vault |

Keeping the two apart keeps personal data out of the code's history, keeps the vault
clean for Obsidian and phone sync, and lets backup and encryption apply to the vault
alone.

### 2.1 Sync (Android)

- **Syncthing** syncs the vault folder between phone and desktop.
- **git runs only on the desktop.** The phone never commits.
- `.stignore` excludes `.git/` and `.claude/`.
- Syncthing `*.sync-conflict-*` files are detected by `liber check` and resolved during
  ingest.

## 3. Vault layout and conventions

### 3.1 Layout (initial; evolvable, see §3.5)

```
liber-vault/
  AGENTS.md            one-page summary of the user + vault map + rules for AI readers
  CLAUDE.md            conventions for Claude Code sessions; points to AGENTS.md
  README.md            short human-facing guide
  liber.toml           vault config: schema version, folders, file types, size limits
  inbox.md             quick notes; cleared after ingest
  open-questions.md    gaps noticed by Claude; material for interviews and writing
  inbox/               drop zone for documents
  core/                identity.md, personality.md, values.md, preferences.md
  career/              timeline.md, skills.md, projects/<name>.md
  people/              "<Full Name>.md", one per person
  interests/           one file per passion or hobby
  goals/               current goals (including financial and creative)
  log/                 <YYYY>.md: dated life events, newest first
  sources/
    notes/             <YYYY-MM>.md: archived inbox notes
    documents/         originals plus extracted .md next to each
    interviews/        voice transcripts (future)
  .claude/skills/      symlinks to the code repo's skills
```

### 3.2 Frontmatter (required on every content file outside `sources/` and `inbox/`)

```yaml
---
type: core | career | project | person | interest | goal | log   # allowed values come from liber.toml
updated: 2026-09-30
sensitivity: public | personal | private
tags: []
---
```

- Sensitivity is set **per file**. Content more sensitive than the rest of its file goes
  in a separate file.
- `AGENTS.md`, `CLAUDE.md`, `README.md`, `inbox.md` and `open-questions.md` are system
  files. They are exempt from the `type` requirement. `AGENTS.md` carries
  `sensitivity: public` so it can always be bundled.

### 3.3 Writing conventions

- **First person**, in the user's voice. `AGENTS.md` states who "I" refers to.
- **Facts carry dates and sources**, for example:
  `- Led the payments migration at Acme (2019–2021). (src: [[2026-10-02-interview]])`
- **Superseded facts become history** and are never silently deleted:
  `- *Previously* lived in Denver (until 2024).`
- **People are referenced with wikilinks**: `[[Sam Chen]]`.
- **Person template:** frontmatter adds `relationship`, `met` (how and when), and
  `last_contact`. The body is free-form notes. Notes about others stay factual and kind.

### 3.4 AGENTS.md

It is the file every tool reads first, and it should stay within a target size set in
`liber.toml` (default about 2,000 tokens, estimated at 4 characters per token). It
contains:

1. A summary of the user: who they are, what they do, what drives them, and key likes
   and dislikes.
2. A map of the vault: what each folder holds.
3. Rules for AI readers:
   - This is knowledge *about* the user, not a character to play.
   - Facts are dated; treat old ones as possibly stale.
   - Follow `src:` links for full detail.
   - Respect `sensitivity`.

### 3.5 Evolving structure

- `liber.toml` lists the allowed folders and `type` values, plus limits. The CLI reads
  them from there and never hard-codes the layout.
- Claude is **encouraged** to propose structural changes: new top-level folders (for
  example `health/`, `places/`), file splits, new fields, and new templates.
  - These are always presented separately from content edits and require approval.
  - When a change is approved, Claude updates `liber.toml`, `CLAUDE.md`, and the map in
    `AGENTS.md` together.

Example `liber.toml`:

```toml
schema_version = 1

[limits]
agents_md_max_tokens = 2000

[folders]
core      = { type = "core" }
career    = { type = "career" }
"career/projects" = { type = "project" }
people    = { type = "person" }
interests = { type = "interest" }
goals     = { type = "goal" }
log       = { type = "log" }
```

## 4. The `liber` CLI

- **Stack:** Python 3.12+, managed with `uv`, installed via `uv tool install`.
- **Dependencies:** kept minimal. Typer for the CLI, `python-frontmatter`,
  and `markitdown` for conversion.
- **Vault location:** `~/.config/liber/config.toml` (`vault = "..."`). The `LIBER_VAULT`
  environment variable overrides it.

| Command | Behavior |
|---|---|
| `liber init <path>` | Creates the vault (layout, templates, starter `AGENTS.md`, `CLAUDE.md`, `README.md`, `liber.toml`, `.gitignore`, `.stignore`), links the skills into `.claude/skills/`, runs `git init`, makes the initial commit, and writes the user config. Refuses to run on a non-empty directory. |
| `liber note "text"` | Appends `- YYYY-MM-DD HH:MM — text` to `inbox.md`. |
| `liber add <file>…` | Copies files into `inbox/`, adding a suffix on name collision. |
| `liber status` | Lists waiting inbox notes and documents, each document's extraction state (pending, done, or no text found), and any sync conflicts. |
| `liber extract` | For each document in `inbox/` without a sibling `.md` file, converts it with `markitdown` to `<name>.<ext>.md`. If the result has no meaningful text, the document is marked "no text found". Plain `.md` and `.txt` files need no conversion. |
| `liber archive <item>` | Moves a document and its extracted text to `sources/documents/`, never overwriting (it adds a suffix). `liber archive --notes` moves the processed contents of `inbox.md` into `sources/notes/<YYYY-MM>.md` and leaves `inbox.md` empty. |
| `liber check` | Reports missing or invalid frontmatter, `type` or folder values not in `liber.toml`, broken wikilinks, sync-conflict files, and an `AGENTS.md` over its size limit. Exits non-zero if it finds problems. |
| `liber bundle [--topics a,b] [--max-sensitivity personal] [--copy]` | Writes one Markdown document to stdout (or the clipboard with `--copy`): `AGENTS.md` first, then the selected folders' files, each with a header giving its path. Files above the sensitivity ceiling are excluded. The default ceiling is `personal`. `--copy` uses `wl-copy` or `xclip`, and falls back to stdout with a warning if neither is present. |

All commands give clear errors when no vault is configured, pointing to `liber init`.

## 5. Claude Code skills

The skill sources live in the code repo under `skills/<name>/SKILL.md`. `liber init`
symlinks them into the vault's `.claude/skills/`, so updates to the skills apply
immediately.

### 5.1 `/ingest`

1. **Preflight.**
   - If the working tree is dirty, offer to commit first.
   - Run `liber check`, and resolve any sync conflicts with the user.
   - Run `liber status` and `liber extract`.
2. **Process each item, oldest first.** The items are `inbox.md` notes as one batch, then
   each document.
   1. Read the item.
   2. Read `AGENTS.md` and the relevant existing files.
   3. Propose a numbered change set:
      - content edits (add, update, or new file), each with its target file;
      - contradictions, flagged as questions;
      - new person files;
      - log entries;
      - structural proposals, in a separate section.
   4. The user replies in natural language (approve all, approve some, correct, skip).
   5. Apply the approved edits: update `updated:`, add `src:` links, and confirm the
      sensitivity of each new file (default `personal`).
   6. Run `liber check`.
   7. Run `liber archive`.
   8. Make one git commit per item: `ingest: <item>`.
3. **Gaps.** Add questions about gaps noticed during ingest to `open-questions.md`.
4. **Wrap-up.** If anything significant changed, propose an `AGENTS.md` summary refresh
   as a separate approval.

The rules below are also part of the skill:

- Record only what the source supports. Mark inferences as inferences, or ask.
- Never delete facts silently.
- Distill long documents instead of copying them; the full text remains in `sources/`.
- Keep notes about others factual and kind.
- Actively suggest structural improvements (§3.5).

### 5.2 `/review`

`/review` is a whole-vault pass, run whenever the user wants (for example, monthly):

- aspects of life that aren't captured yet, and thin areas;
- stale facts (old `updated` dates, or `last_contact` long past);
- structural fit;
- `AGENTS.md` accuracy and size.

It proposes changes (with the same approval rules as ingest), adds questions to
`open-questions.md`, and commits the approved changes as `review: <summary>`.

## 6. Error handling

| Situation | Behavior |
|---|---|
| Scanned PDF or document with no text layer | Marked "no text found" in `status`; the skill asks the user to paste the text or skip the document |
| Dirty working tree at ingest start | Skill offers to commit first |
| Sync-conflict files | `check` flags them; the skill resolves them with the user before editing the affected files |
| No config or missing vault | Clear error pointing to `liber init` |
| Name collisions in `add` and `archive` | Suffix added; nothing is ever overwritten |
| `init` on a non-empty directory | Refused |

## 7. Testing

- **CLI:** `pytest` against temporary vaults, covering every command.
- **Required both-direction test for `bundle`:** at `--max-sensitivity personal`, no
  `private` file appears, and every `public` and `personal` file in the selected topics
  does appear. Repeat for each ceiling level.
- **`extract`:** small fixture PDF, DOCX, and HTML files, plus a textless PDF fixture
  that must come out as "no text found".
- **`check`:** fixtures for each problem class, plus a clean vault that must pass.
- **Skills:** a sample inbox fixture (a quick note, plus a document that contradicts an
  existing fact) and a written manual walkthrough checklist in `docs/`.

## 8. Documentation

- **Code repo `README.md`:**
  - the name derivation (§1.1)
  - what liber is and how the pieces fit, including the future sub-projects
  - install
  - `liber init`
  - Obsidian setup
  - Syncthing setup on Android (including the `.stignore` rules)
  - the everyday workflow (capture, then `/ingest`, then `bundle` or a connected tool)
  - `/review`
  - command reference
- **Vault `README.md`:** a short guide for the user on where things go, how to capture,
  and how to ingest.
- **Vault `AGENTS.md` and `CLAUDE.md`:** the AI-facing documentation.
