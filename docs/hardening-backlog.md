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

## Access server (sub-project 2)

Deferred from the final whole-branch review of the access server.

- **settings:** OSError/UnicodeDecodeError reading config or secrets gives a traceback; relative XDG_DATA_HOME accepted; `base_url = "https://"` passes validation
- **knowledge:** raw newlines in proposal context; snippet offsets can drift for characters whose lowercase changes length; no cap on query terms
- **app:** no test that unexpected exceptions are masked
- **tokens:** hand-edited non-ASCII hash raises TypeError (fails closed); malformed entries list as "None"; no lock on concurrent create/revoke; data dir default mode
- **auth:** callback takes the first `code` param (fails closed); a GitHub outage shows the "private" page and logs login None; no try/finally around the code delete; consent cookie not cleared on refusal; logout-all hides rmtree errors; OAuth dir mode follows umask; GitHub scope is `user` (consider `read:user` after live testing)
- **setup:** unquoted paths in unit ExecStart / tunnel YAML; json.dumps surrogate escapes are invalid TOML for non-BMP characters; config append not atomic; `_tunnel_id` TypeError for non-object JSON
- **doctor:** systemctl call has no timeout; local-server failure drops the exception detail; doctor's vault check uses the shell's LIBER_VAULT, which the systemd service may not have

## Voice interviewer (sub-project 4)

Deferred from the final whole-branch review and task-by-task sign-offs.

- **Task 1:** `read_secret_values` drops non-string values and replaces an unparseable secrets file on rewrite; `load_voice_keys` doesn't check secrets mode when keys come from environment; `save_voice_setup` config write is not atomic and precedes secrets write
- **Task 2:** no test for exact 2,500 ms turn boundary; multi-line typed notes get flattened; topic and notes not escaped in Markdown (e.g. `[[…]]` becomes a wikilink); `dialogue(limit=0)` returns all turns instead of respecting the limit; `Fragment(**f)` brittlely assumes keys
- **Task 3:** JSON prompts lack "no code fences" documentation (parser tolerates them); topic string used raw as search query (no escaping)
- **Task 5:** `sends(commentary/close)` raise `ConnectionClosed` if peer dropped — callers should catch them (only `session._safe` does currently); non-dict JSON in messages drops silently
- **Task 6:** banker's rounding for elapsed minutes not exact; `_save_draft` blocks the event loop with sync I/O
- **Task 7:** background tasks ignoring cancellation for >5 s may overlap new sideband; elapsed time includes interrupted periods (document expected behavior)
- **Task 8:** auth token visible in `?t=` query string (uvicorn access log is off in runner); failed `setRemoteDescription` re-enables Start button; stale in-flight poll during resume could tear down fresh connection (narrow race); `testserver` is in the production host allowlist (token still required)
- **Task 9:** bare `suppress(BaseException)` on `serve()` await also swallows intended `CancelledError`; startup-failure path untested; `--notes` path accepted outside the vault; `topic`/`--continue` silently ignored when used with `--recover`/`--notes`
- **Deferred (final review):** background-tab throttling may let the 60 s heartbeat lapse while interrupted; typed-note/seed char limits assume English-like text; the live smoke test checks model access and Claude only (a real WebRTC session needs a browser)
