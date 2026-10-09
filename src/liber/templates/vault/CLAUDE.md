# liber vault — instructions for Claude Code

This is a **liber** vault: a knowledge base about its owner ("I" and "me" in every file). Read `AGENTS.md` first.

## Skills

- `/ingest` processes quick notes in `inbox.md` and documents in `inbox/` into the vault.
- `/review` reviews the whole vault for gaps, stale facts, and structure.

## Conventions

- **Structure** is defined in `liber.toml` `[folders]`: every Markdown file in a folder must have that folder's `type`. Change structure only with the owner's approval. When you do, update `liber.toml`, this file, and the map in `AGENTS.md` together, then run `liber check`.
- **Frontmatter** on every file in a content folder:
  ```yaml
  ---
  type: <the folder's type from liber.toml>
  updated: YYYY-MM-DD
  sensitivity: public | personal | private
  tags: []
  ---
  ```
  Sensitivity is per file. Put more sensitive material in its own file. New files default to `personal`; confirm with the owner.
- **Health** lives in `health/`. Medical details (medications, lab results, conditions) go in `private` files. Training and diet can be `personal`.
- **Open questions** that would reveal `private` information go in an `## Open questions` section of the relevant private file, not in `open-questions.md`, which cloud tools can read.
- **First person**, in the owner's voice.
- **Facts carry dates and sources**, for example `- Led the payments migration at Acme (2019–2021). (src: [[sources/documents/thesis.pdf]])`.
- **Never delete facts silently.** Superseded facts become history, for example `- *Previously* lived in Denver (until 2024).`
- **People** are linked by name, for example `[[Sam Chen]]`, and each has `people/Sam Chen.md` based on `_templates/person.md`. Keep notes about others factual and kind.
- **Log** entries go in `log/<YYYY>.md`, newest first, for example `- 2026-09-30 — Started liber.`
- **Root files** are only `AGENTS.md`, `CLAUDE.md`, `README.md`, `inbox.md`, `open-questions.md`, and `liber.toml`. Don't add other notes at the root.
- **`sources/`** holds original material. Never edit it.
- Run `liber check` after editing. It must pass before you commit.

## Commands

`liber status`, `liber extract`, `liber archive <name>`, `liber archive --notes --count <N>`, `liber check`, `liber bundle`.
