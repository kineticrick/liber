# My liber vault

This vault is a knowledge base about me, kept in plain Markdown so any AI tool can use it.

## Adding things

- **Quick thought:** add a line to `inbox.md`, from Obsidian on any device or with `liber note "..."` on the desktop.
- **Whole document** (a paper, a resume, an export): drop it into `inbox/`.

## Processing the inbox

On the desktop, open Claude Code in this folder and run `/ingest`. Claude proposes changes, I approve them, and each processed item becomes one git commit. Run `/review` every so often for a whole-vault check-up.

## Using it

- `liber bundle` prints everything up to `personal` sensitivity as one document to paste into any chatbot.
- `AGENTS.md` is the entry point for AI tools.

## Where things go

See the map in `AGENTS.md`. The structure is defined in `liber.toml` and can grow over time.
