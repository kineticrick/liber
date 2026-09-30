---
name: review
description: Whole-vault review of the liber knowledge base, covering uncaptured parts of the owner's life, stale facts, thin areas, structural fit, and AGENTS.md accuracy, and proposing changes and open questions for approval. Use when the user runs /review or asks for a check-up of their liber vault.
---

# Review the liber vault

You are reviewing **liber**, a knowledge base about its owner, who is the person you are talking to. First read `CLAUDE.md` and `AGENTS.md`. **The owner approves every change.**

## 1. Preflight

1. Run `git status --porcelain`. Offer to commit any uncommitted changes first.
2. Run `liber check` and report the results.

## 2. Survey

Read `liber.toml`, `AGENTS.md`, `open-questions.md`, and every content folder. For large vaults, read file headings and frontmatter first, then go deeper where needed. Look for:

- **Uncaptured life areas.** Common parts of a life with no home yet, for example health and fitness, places lived, education, finances, family history, creative work, beliefs, or routines. Only raise the ones that seem relevant to this owner.
- **Thin areas.** Folders or files with little content compared with their importance to the owner.
- **Stale facts.** Old `updated` dates, `last_contact` long past, and present-tense facts that are probably no longer true.
- **Structure.** Files that have grown too large, folders that no longer fit, and repeated patterns that deserve a template or a frontmatter field.
- **AGENTS.md.** Whether the Summary still reflects the vault, and whether it is close to the size limit.
- **Open questions** that the vault now answers and can be removed.

## 3. Propose

Present one numbered list, grouped as: **Structure**, **Content fixes**, **Stale items to confirm**, **AGENTS.md**, and **New open questions**. Wait for the owner's answers.

## 4. Apply

1. Apply only the approved changes, following `CLAUDE.md`:
   - Update the `updated:` dates.
   - Turn superseded facts into *Previously* history.
   - For approved structural changes, update `liber.toml` `[folders]`, `CLAUDE.md`, and the map in `AGENTS.md` together.
2. Add the approved questions to `open-questions.md` as `- [YYYY-MM-DD] <question> (from review)`.
3. Run `liber check` and fix everything it reports.
4. Commit: `git add -A && git commit -m "review: <short summary>"`.
5. Recap what changed and what's still open.

## Rules

- **Suggest boldly, change nothing without approval.**
- **Never delete facts silently.**
- **Never edit anything in `sources/`.**
- **Keep notes about other people factual and kind.**
