# Hardening backlog

Known minor issues deferred from the sub-project 1 build reviews (2026-09-30). None lose data outside git history; pick these up alongside sub-project 2.

- **Task 1:** config.py non-string/empty `vault` value → TypeError traceback / cwd; no test for invalid-TOML branch or ~/.config fallback
- **Task 1:** test_cli_shell handle_errors test passes vacuously if no exception (use pytest.raises); help assertion weak
- **Task 2:** liber.toml non-numeric limits / non-table folders / string conflict_patterns raise non-LiberError (traceback)
- **Task 2:** BOM frontmatter handling unverified; non-UTF-8 .md raises UnicodeDecodeError uncaught
- **Task 3:** init failure mid-way leaves half-built vault that retry refuses; _git_init no OSError handling; duplicated skill check
- **Task 4:** root sync-conflict file reported twice (unknown-file + sync-conflict); missing AGENTS.md kind 'unknown-file'; unclosed fence; per-file unknown-folder noise; non-UTF-8 AGENTS.md traceback
- **Task 5:** _is_sidecar hides real doc `report.md` when file `report` exists (silently absent from status); dangling symlink in inbox/ crashes status via stat(); no CLI error-path tests for note/add
- **Task 6:** extract exits 0 when some docs failed; sidecar write outside try (OSError aborts batch; truncated sidecar looks extracted); multi-line exception text in output
- **Task 7:** archive_notes read→rewrite window can drop a note appended in the milliseconds between; partial move with no rollback if sidecar rename fails; `.md` sidecar check case-sensitive
- **Task 8:** `--topics ","` yields AGENTS-only bundle; copy_to_clipboard untested
- **Task 9:** "nothing worth keeping" path unclear whether check/commit still run; test doesn't pin `--notes --count` mention
- **Final review:** `archive --notes` drops headings and flattens indentation in inbox notes; lines inside multi-line HTML comments in inbox.md count as notes; `check` matches wikilinks case-sensitively (Obsidian doesn't).
