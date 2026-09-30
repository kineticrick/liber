# Manual test: /ingest and /review

The skills can't be unit-tested, so run through this checklist after changing either `SKILL.md`. Use a throwaway vault; this never touches your real one.

## Setup

```bash
export LIBER_VAULT=/tmp/liber-manual-test
rm -rf "$LIBER_VAULT"
uv run liber init "$LIBER_VAULT"
```

`liber init` also points `~/.config/liber/config.toml` at the test vault. See Cleanup to point it back.

Add a fact that the sample document contradicts:

```bash
cat >> "$LIBER_VAULT/career/timeline.md" <<'EOF'

- Graduate school, M.S. at State University (2012–2015).
EOF
git -C "$LIBER_VAULT" commit -qam "seed timeline"
```

Load the sample inbox:

```bash
while IFS= read -r line; do uv run liber note "$line"; done < docs/manual-test/sample-inbox/notes.txt
uv run liber add docs/manual-test/sample-inbox/thesis-summary.md
uv run liber status
```

## Run /ingest

Open Claude Code in `$LIBER_VAULT` and run `/ingest`. Check each item:

- [ ] Preflight runs `liber check`, `liber extract`, and `liber status`, and states the processing order (notes first, then the document).
- [ ] Notes: proposes `core/preferences.md` (likes mentoring, dislikes managing), a new `people/Priya Nair.md`, and an open question or goal about teaching or freelancing. Waits for approval.
- [ ] Notes archived with `--count 3`; links look like `(src: [[2026-MM]])`.
- [ ] Document: flags ⚠️ the 2014 thesis versus grad school ending in 2015. Doesn't silently change either date.
- [ ] Proposes a new `people/Ana Ruiz.md` and a skills entry. Marks "enjoys explaining" as inferred or cites it directly.
- [ ] Asks for the sensitivity of each new file.
- [ ] One commit per item (`git -C "$LIBER_VAULT" log --oneline`).
- [ ] `liber check` passes at the end, and `open-questions.md` has at least one entry.
- [ ] Offers an `AGENTS.md` Summary refresh as a separate approval.

Late-arrival check: during the notes proposal, run `uv run liber note "arrived late"` in another terminal. After archiving, it must still be in `inbox.md`.

## Run /review

- [ ] Proposes at least one uncaptured life area and at least one structural or content suggestion, and waits for approval.
- [ ] Approving a new top-level folder updates `liber.toml`, `CLAUDE.md`, and the `AGENTS.md` map together, and `liber check` passes.

## Cleanup

```bash
rm -rf /tmp/liber-manual-test
unset LIBER_VAULT
```

If you already have a real vault, set `vault = "/path/to/your/vault"` in `~/.config/liber/config.toml` again.
