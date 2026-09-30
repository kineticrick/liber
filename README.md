# liber

**liber** is a personal knowledge base about you: your work history, life, likes and dislikes, passions, expertise, and the people in your life. It's kept as plain Markdown in a private git repo, so any LLM, chatbot, or agent can use it as "context about me", and it grows over time as you add to it.

## Why "liber"

The name comes from Carl Jung's *Liber Novus* ("The New Book"), better known as *The Red Book*. Jung spent about sixteen years writing it: a private, ever-growing record of his inner life that was never meant as a public face. That is the spirit of this project, an honest, accumulating record of a whole person.

Two other Jungian names were considered and set aside:

- **persona** is Jung's term for the *mask* we show the world, the opposite of what liber holds. LLMs also tend to read "persona" as an instruction to role-play.
- **anima** is, strictly, one part of the psyche (the contrasexual archetype), not the whole Self.

## How it fits together

```
 capture                     process                      use
 ───────                     ───────                      ───
 inbox.md  (quick notes)  ┐
 inbox/    (documents)    ├─► /ingest in Claude Code ─► vault files ─► liber bundle → any chatbot
 voice transcripts (later)┘   (you approve each change)   (git history)   MCP server (later) → agents
```

- **The vault** (for example `~/liber-vault`) is your content: Markdown files with small frontmatter headers, a private git repo, and an Obsidian vault synced to your phone.
- **This repo** is the tool: the `liber` command and two Claude Code skills, `/ingest` and `/review`.

Planned later: an MCP access server so agents can query the vault directly, and a voice interviewer (OpenAI GPT-Live-1 for the conversation, Claude as the interviewer's brain) that drops transcripts into `inbox/`.

## Install

Requires [uv](https://docs.astral.sh/uv/), git (with `user.name` and `user.email` set, since ingest makes commits), and [Claude Code](https://claude.com/claude-code). Optional: `wl-copy` or `xclip` for `liber bundle --copy`.

```bash
git clone <this repo> ~/code/python/liber
cd ~/code/python/liber
uv tool install --editable .
```

The `--editable` install matters. Your vault's skills are symlinks into this repo, so updating the repo updates the skills everywhere.

## Create your vault

```bash
liber init ~/liber-vault
```

This creates the folders and starter files, links the skills into `.claude/skills/`, makes the first git commit, and sets the vault as your default in `~/.config/liber/config.toml`. Set `LIBER_VAULT` to point a single command at a different vault.

### Obsidian and phone sync (Obsidian Sync)

1. In Obsidian on the desktop: **Open folder as vault** and choose `~/liber-vault`.
2. **Settings → Sync:** create a new remote vault for it. Keep it separate from any other vault.
3. **Sync settings:** turn on syncing for **PDFs** and **other file types**, so documents saved into `inbox/` on the phone reach the desktop.
4. **Settings → Templates:** set the template folder to `_templates`. This lets you create people with the `person` template.
5. On your Android phone: open Obsidian, connect to the same remote vault, and add thoughts to `inbox.md` whenever you like.

git runs on the desktop only. Obsidian Sync doesn't sync hidden folders like `.git/`, so there's nothing to exclude.

To seed liber from notes you already have in another Obsidian vault, copy them into `inbox/` and run `/ingest`.

- **Storage:** original documents in `sources/documents/` count against your Sync storage. If that becomes a problem, add `sources/documents` to Sync's excluded folders. The originals stay safe in git on the desktop.
- **Conflicts:** Obsidian Sync usually merges edits automatically. If it ever leaves a conflict copy, `liber check` finds it, and `/ingest` helps you merge it. The file-name patterns are in `liber.toml` under `[sync] conflict_patterns`.

## Everyday use

1. **Capture:**
   - Add a line to `inbox.md` (from your phone or desktop), or run `liber note "..."`.
   - Drop documents into `inbox/`, or run `liber add file.pdf`.
2. **Ingest:** open Claude Code in your vault and run `/ingest`.
   - Claude proposes dated, sourced edits, flags contradictions, and suggests structural changes.
   - You approve or correct them in plain language. Each item becomes one git commit.
3. **Use:**
   - `liber bundle --topics career,goals,core --copy` puts a single document on your clipboard, ready to paste into any chatbot.
   - For example: "Given everything about me, what new revenue sources should I pursue?"
4. **Review:** run `/review` every month or so.
   - It finds uncaptured parts of your life, stale facts, and structural improvements.
   - It adds questions to `open-questions.md`.

## Vault layout

| Path | Contents |
|---|---|
| `AGENTS.md` | One-page summary of you, a map of the vault, and rules for AI readers. The first thing any tool reads |
| `CLAUDE.md` | Conventions Claude Code follows in the vault |
| `liber.toml` | The vault's structure (folders and their `type`), size limits, and sync settings |
| `inbox.md`, `inbox/` | Quick notes and documents waiting for `/ingest` |
| `open-questions.md` | Gaps to fill later |
| `core/`, `career/`, `people/`, `interests/`, `goals/`, `log/` | Your knowledge base |
| `sources/` | The originals everything came from: notes, documents, and (later) interviews |
| `_templates/` | Obsidian templates, for example `person.md` |

The structure is meant to grow. When Claude suggests a new folder and you approve it, the folder is added to `liber.toml`, and `liber check` accepts it from then on.

Every content file starts with:

```yaml
---
type: core            # must match the folder's type in liber.toml
updated: 2026-09-30
sensitivity: personal # public | personal | private
tags: []
---
```

## Commands

| Command | What it does |
|---|---|
| `liber init <path>` | Create a new vault and make it the default |
| `liber note "text"` | Add a dated note to `inbox.md` |
| `liber add <file>…` | Copy documents into `inbox/`, never overwriting |
| `liber status` | Show waiting notes, documents (and whether they've been converted to text), and sync conflicts |
| `liber extract` | Convert waiting PDF, Word, and HTML documents to Markdown text |
| `liber archive <name>` | Move a processed document (and its text) to `sources/documents/`, printing the final name |
| `liber archive --notes [--count N]` | Move processed notes (optionally only the first N) to `sources/notes/<YYYY-MM>.md` |
| `liber check` | Validate frontmatter, folder structure, `[[links]]`, sync conflicts, and `AGENTS.md` size. Exits 1 on problems |
| `liber bundle [--topics a,b] [--max-sensitivity personal] [--copy]` | Combine `AGENTS.md` and the selected folders into one Markdown document. Files above the ceiling, or with missing or invalid sensitivity, are left out |

## Development

```bash
uv sync
uv run pytest
```

The skills live in `src/liber/skills/`. After changing them, walk through `docs/manual-test/CHECKLIST.md`. The design and plan are in `docs/superpowers/`.
