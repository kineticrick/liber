---
name: ingest
description: Process the liber vault's inbox (quick notes in inbox.md and documents in inbox/) into the knowledge base, proposing dated, sourced edits for the owner to approve, one git commit per item. Use when the user runs /ingest or asks to process their liber inbox.
---

# Ingest the liber inbox

You are updating **liber**, a knowledge base about its owner, who is the person you are talking to. First read `CLAUDE.md` and `AGENTS.md` in the vault root. They define the conventions you must follow. **The owner approves every change. Never edit content they haven't approved.**

## 1. Preflight

1. Run `git status --porcelain`. If anything is uncommitted, show it and offer to commit it first (`git add -A && git commit -m "manual edits"`), so each ingested item gets a clean commit of its own. Continue only once the tree is clean or the owner says to go ahead anyway.
2. Run `liber check`.
   - For each `sync-conflict` file, compare it with the original, agree the merged text with the owner, write it into the original, and delete the conflict copy.
   - Report other problems and offer to fix them.
3. Run `liber extract`, then `liber status`.
4. Tell the owner what's waiting and the order you'll take: inbox notes first as one batch, then documents oldest first.
   - `no text found`: ask the owner to paste the text or skip the item. If they paste it, replace `inbox/<name>.md` with the pasted text.
   - `failed`: report the error and skip the item unless the owner can supply the text.
   - `folder`: ask the owner to move the files inside it directly into `inbox/`.

## 2. For each item

### a. Read

- The item itself: the notes listed by `liber status`, or the document's text (`inbox/<name>.md` for converted documents, or the file itself for `.md` and `.txt`). Read long documents in chunks.
- `AGENTS.md`, and every existing file the item touches, including `people/` files for anyone mentioned, so you know what is already recorded.

### b. Propose

Present one numbered list of proposed changes, grouped like this:

- **Content edits.** For each one: the target file, whether it adds, updates, or creates a new file, and the exact text.
- **⚠️ Contradictions.** Questions where the item disagrees with the vault, for example "career/timeline.md says grad school ended 2015, but this is dated 2014. Which is right?"
- **New people.** A new `people/` file for each person.
- **Log entries.** Dated events for `log/<YYYY>.md`, newest first, e.g. `- 2014-05 — Finished my master's thesis. (src: [[thesis-summary.md]])`.
- **Open questions.** Gaps this item reveals (something mentioned but never explained), phrased as questions for `open-questions.md`.
- **Structural suggestions,** kept separate from content: a new top-level folder when something has no good home, splitting a sprawling file, a new frontmatter field, a new template.

Example:

> **From `thesis.pdf`:**
> 1. `career/skills.md`: add *Bayesian modeling: deep; basis of thesis work (2014)*
> 2. `interests/epistemology.md`: **new file** (sensitivity: personal?)
> 3. `people/Ana Ruiz.md`: **new**, thesis advisor
> 4. ⚠️ `career/timeline.md` says grad school ended 2015, but the thesis is dated 2014. Which is right?
>
> **Structure:** nothing to suggest.

Then stop and wait. The owner answers in plain language, for example "all but 2", "3 was my co-advisor", or "skip this one".

- **Skip:** leave the item in the inbox untouched and move on.
- **Nothing worth keeping:** archive it with no content edits, as in step c.

### c. Archive, then apply

1. **Archive first**, so you know the final source name for links:
   - Notes: `liber archive --notes --count <N>`, where N is the number of notes you reviewed. Notes that arrived while you worked stay in the inbox. Link to the printed notes file by name, for example `[[2026-09]]`.
   - Document: `liber archive "<name>"`. Link to the archived name it prints, which may carry a `-2` suffix, for example `[[thesis.pdf]]`.
2. **Apply the approved edits, with the owner's corrections:**
   - End every added fact with `(src: [[<source name>]])`, and give it a date or date range whenever one is known.
   - Set `updated:` to today on every file you change.
   - New files get full frontmatter per `CLAUDE.md`. Confirm their sensitivity, defaulting to `personal`. Create people from `_templates/person.md`, replacing `{{title}}` and `{{date}}`.
   - A superseded fact becomes history (`- *Previously* …`). It is never deleted.
   - For approved structural changes, update `liber.toml` `[folders]`, `CLAUDE.md`, and the map in `AGENTS.md` together, and create the folder.
3. Run `liber check` and fix everything it reports.
4. Commit: `git add -A && git commit -m "ingest: <item name>"`.

## 3. Wrap up

1. Add the open questions the owner approved to `open-questions.md`, one line each: `- [YYYY-MM-DD] <question> (from [[<source name>]])`. Commit with `ingest: open questions`.
2. If anything significant changed, propose a refresh of the **Summary** in `AGENTS.md`.
   - Show the full new text and get approval separately.
   - Keep it within the size limit; `liber check` enforces this.
   - Commit with `ingest: refresh AGENTS.md`.
3. Give a short recap: items processed, files changed, questions added, anything skipped.

## Rules

- **Record only what the source supports.** Mark inferences, for example "suggests I enjoy teaching (inferred)", or ask instead.
- **Never delete facts silently.** Contradictions go to the owner. Outdated facts become *Previously* history.
- **Distill long documents instead of copying them.** The full text stays in `sources/`.
- **Keep notes about other people factual and kind.**
- **Actively suggest structure.** When an item has no good home, a file is sprawling, or a new field would help, propose the change.
- **Never edit anything in `sources/`.**
- **The owner's word wins.** Record their correction, not your first reading.
