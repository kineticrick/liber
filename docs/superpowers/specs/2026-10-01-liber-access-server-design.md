# liber — Access Server (Sub-project 2) Design

**Date:** 2026-10-01
**Status:** Draft for review
**Builds on:** `docs/superpowers/specs/2026-09-30-liber-foundation-design.md` (sub-project 1)

## 1. Purpose

This sub-project gives every AI tool the user works with direct, permission-aware access to the
liber vault through MCP (Model Context Protocol). Tools can read the knowledge base and propose
additions to it. No tool can edit vault files directly: every proposal goes into `inbox/` and is
reviewed through the existing `/ingest` skill.

### 1.1 Clients

| Client | Transport | Auth |
|---|---|---|
| Claude Code, Claude Desktop (this machine) | stdio (local process) | none: the OS user is the boundary |
| claude.ai on web and mobile | Streamable HTTP via `https://liber.kineticrick.com/mcp` | OAuth (GitHub login) |
| ChatGPT (developer mode connectors) | same | OAuth (GitHub login) |
| Voice interviewer backend (sub-project 4) and smoke tests | same | static service token |

### 1.2 Success criteria

- Claude Code and Claude Desktop can use liber through `liber serve` with no setup beyond one config entry.
- claude.ai (web, then mobile) and ChatGPT developer mode can add liber as a connector at
  `https://liber.kineticrick.com/mcp`. Logging in with GitHub as `kineticrick` succeeds; any other
  GitHub account is refused before it receives a token.
- Cloud clients never receive `private` content or anything from `sources/`. Local clients see everything.
- `propose_update` from any client results in a file in `inbox/` that `/ingest` processes unchanged.
- `liber server doctor` detects every misconfiguration listed in §7 that would stop a strict client
  from connecting.

### 1.3 Non-goals

Semantic search or embeddings. ChatGPT Deep Research's `search`/`fetch` tool pair, which is easy to add
later. Direct edit tools. Multi-user support. Cloud hosting. Per-app ceilings: all OAuth clients share
one ceiling.

## 2. Decisions and their reasons

| Decision | Reason |
|---|---|
| Run on the user's desktop, exposed via **Cloudflare Tunnel** at `liber.kineticrick.com` | The vault and git live on the desktop, which is usually on. Data never sits on a third-party host, and proposals land in the real `inbox/`. `kineticrick.com` moves fully to Cloudflare DNS (free plan). Partial or CNAME setup for a subdomain of `rickbo.one` would need the Business plan, and `rickbo.one` stays on Vercel untouched. |
| **`fastmcp` v4** (pinned, currently 4.0.10) | One server definition runs over both stdio and Streamable HTTP. Its `GitHubProvider` OAuth proxy already implements CIMD and DCR client registration, PKCE, issuing liber's own tokens, and encrypted storage of upstream tokens. GitHub has no DCR, so a proxy is required. Writing this by hand on the official `mcp` SDK (2.2.0) would mean about 500 lines of security-critical code. |
| **GitHub login**, allow-list `["kineticrick"]` | GitHub provides 2FA, and liber stores no password. |
| **Static service token** as a second auth path | OpenAI's Realtime/GPT-Live MCP tool sends a bearer token you supply and runs no OAuth flow. |
| **Ceilings:** local `private`, OAuth `personal`, service token `personal` | Cloud chat providers retain conversations, so `private` content stays on the machine. |
| **`sources/` is local-only** | Archived originals have no sensitivity header and may contain anything. |
| **JSON responses** (no SSE streaming) on HTTP | This avoids reported Cloudflare buffering of `text/event-stream`, and the tools are all quick request/response calls. |

## 3. Tools

The names say "user" so that models don't read them as a cue to role-play. Each tool's description
states that the vault is knowledge *about* the person the model is talking to.

| Tool | Parameters | Returns |
|---|---|---|
| `get_user_profile` | none | `AGENTS.md` text, today's date (ISO), the connection's ceiling, and the visible top-level folders. The description says to call it first. |
| `list_user_knowledge` | `folder: str \| None` | Visible files: `path`, `title` (the first `# ` heading, or the file stem), `type`, `updated`, `sensitivity`, `tags`. |
| `read_user_knowledge` | `path: str` (vault-relative) | Full file text. |
| `search_user_knowledge` | `query: str`, `folders: list[str] \| None`, `limit: int = 10` (max 50) | Ranked results: `path`, `title`, `score`, and up to 3 snippets of about 200 characters each. |
| `propose_update` | `text: str`, `context: str \| None` | The created inbox file name and a note that the owner will review it. |

### 3.1 Visibility rules

These apply to every tool. A file is **visible** to a connection only if all of the following hold:

- **Location.** It is one of:
  - a Markdown file in a content folder (as returned by `iter_content_files`);
  - `AGENTS.md`;
  - `open-questions.md`;
  - when the ceiling is `private` only, a file under `sources/`.
- **Sensitivity.** Its `sensitivity` is valid and at or below the ceiling. `sources/` files count as
  `private`, and `open-questions.md` counts as `personal`.
- **Not a conflict copy.** It does not match a sync-conflict pattern.
- **Inside the vault.** Its resolved real path is inside the vault, so symlinks that escape are invisible.

Never visible: `inbox.md`, `inbox/`, `liber.toml`, `CLAUDE.md`, `README.md`, `_templates/`, and hidden paths.

Reading or listing anything invisible behaves exactly as if it did not exist, with the same
`not found` error, so a connection cannot learn that a file above its ceiling exists.

### 3.2 Search

- **Scoring.** Matching is case-insensitive on the query's terms (split on whitespace). A file's score
  is its total term hits, with title hits counting double. Files missing any term rank after files
  containing all terms.
- **Order.** Ties break by newest `updated`, then by path.
- **Snippets.** Each snippet is centred on a hit, with whitespace collapsed.
- **Errors.** A query that is empty after trimming returns an error.

### 3.3 Proposals

- **File.** Each proposal is written to `inbox/proposal-<YYYY-MM-DDTHH-MM-SS>-<client>.md`. `client` is a
  sanitised `[a-z0-9.-]` label: `local`, the OAuth client's name, or the service token's name. A
  collision gets the usual `-2` suffix via `free_name`.
- **Content.**

  ```markdown
  <!-- liber proposal -->
  # Proposed update from <client> — <YYYY-MM-DD HH:MM>

  **Context:** <context or "none given">

  <text>
  ```

- **Limits.**
  - `text` must be non-empty after trimming and at most 20,000 characters. `context` may be at most
    2,000 characters.
  - At most 50 waiting proposal files (`inbox/proposal-*.md`) may exist; beyond that the tool returns an
    error asking the user to run `/ingest`.
- **Effect.** The text is written as Markdown, never executed or interpreted. The `.md` suffix means
  `liber extract` treats it as ready text, so `/ingest` needs no change.

## 4. Architecture

```
src/liber/server/
  __init__.py
  knowledge.py   VaultView(vault, ceiling): profile, list, read, search, propose. No fastmcp imports.
  app.py         build_server(settings, mode) -> FastMCP; 5 tools; resolves the caller's ceiling and client label
  auth.py        GitHub provider config, allow-list enforcement, service-token verifier, combined verifier
  settings.py    ServerSettings / Secrets loading and validation
  tokens.py      service-token store (create / list / revoke; sha256 hashes; constant-time compare)
  setup.py       `server init` file generation (config, secrets, systemd units, cloudflared config)
  doctor.py      `server doctor` checks
```

- `knowledge.py` reuses `liber.docs` (`iter_content_files`, `read_vault_file`), `liber.vaultconfig`
  (`sensitivity_rank`, `load_vault_config`), `liber.check.find_conflicts`, and `liber.paths.free_name`.
- `app.py` picks the ceiling from the authenticated identity:
  - stdio mode → `ceilings.local`;
  - OAuth token → `ceilings.oauth`;
  - service token → `ceilings.service`.

  The vault path is resolved per request via `liber.config.resolve_vault()`, so vault errors become
  tool errors and the server stays up.
- **One server, two kinds of token.** It must accept both GitHub-proxy tokens and service tokens.
  - **Spike first:** determine whether fastmcp v4 supports multiple token verifiers natively.
  - **Fallback:** a combined verifier that checks the service-token store first (constant-time
    comparison against hashes), then delegates to the provider's verifier.
- **Allow-list enforcement** happens in the OAuth callback, after GitHub returns the user. If the login
  is not in `allowed_github_logins`, the callback renders "This liber server is private.", issues no
  liber token, and logs the attempt.
  - **Spike:** find fastmcp's hook point for this.
  - **Fallback:** wrap the provider's callback handler.

## 5. Configuration

`~/.config/liber/config.toml` (existing file, new section):

```toml
vault = "/home/kineticrick/liber-vault"

[server]
base_url = "https://liber.kineticrick.com"
host = "127.0.0.1"
port = 8765
allowed_github_logins = ["kineticrick"]

[server.ceilings]
local = "private"
oauth = "personal"
service = "personal"
```

`~/.config/liber/secrets.toml` must be mode `0600`. `liber serve --http` refuses to start otherwise,
and prints the `chmod` fix.

```toml
github_client_id = "..."
github_client_secret = "..."
jwt_signing_key = "..."          # generated by `liber server init`
storage_encryption_key = "..."   # Fernet key, generated
```

- **Token and client storage:** `~/.local/share/liber/oauth/`, encrypted with
  `storage_encryption_key`, so restarts keep sessions.
- **Service tokens:** `~/.local/share/liber/service-tokens.json` holds `{name, sha256, created}` and is
  mode `0600`.

## 6. Commands

| Command | Behavior |
|---|---|
| `liber serve` | stdio MCP server, no auth, ceiling `ceilings.local` (`private` if unset). Needs only the vault. |
| `liber serve --http` | Streamable HTTP on `host:port`, mounted at `/mcp`, with JSON responses, GitHub OAuth, and the service-token path. Requires a complete `[server]` config and secrets. |
| `liber server init` | Interactive. Prompts for the GitHub client ID and secret and generates the keys. Writes `secrets.toml` (`0600`), the `[server]` section (defaults above, `base_url` prompted with that default), `~/.config/systemd/user/liber-mcp.service`, `~/.config/systemd/user/cloudflared-liber.service`, and `~/.cloudflared/liber.yml` (ingress `liber.kineticrick.com` → `http://127.0.0.1:8765`, catch-all 404). Prints the `systemctl --user` and `loginctl enable-linger` commands without running them. Refuses to overwrite existing secrets unless given `--force`. |
| `liber server doctor` | Runs the checks in §7 and prints ✓ or ✗ with a fix for each. Exits 1 on any ✗. |
| `liber token create <name>` | Prints a new random 32-byte urlsafe token once and stores only its hash. Names are unique. |
| `liber token list` | Lists names and created dates. |
| `liber token revoke <name>` | Removes the token. |
| `liber logout-all` | Regenerates `jwt_signing_key` and clears the OAuth token storage. Every OAuth client must log in again. Service tokens are unaffected. |

## 7. `liber server doctor` checks

1. The config has `[server]` with `base_url`, `port` and `allowed_github_logins`, and all ceilings are
   valid levels.
2. `secrets.toml` exists, is mode `0600`, and has all four keys.
3. The vault resolves and `liber.toml` loads.
4. The local server answers on `http://127.0.0.1:<port>/mcp`.
5. **Public reachability.** These checks run against `base_url`:
   - The protected-resource metadata is served, its `resource` exactly equals `<base_url>/mcp`, and
     `authorization_servers` has exactly one entry.
   - The authorization-server metadata advertises `S256`, includes `"none"` in
     `token_endpoint_auth_methods_supported`, has `client_id_metadata_document_supported: true` and a
     registration endpoint, and includes an `iss` / issuer equal to the server.
   - An unauthenticated `POST /mcp` gets `401` with a `WWW-Authenticate` header containing `resource_metadata=`.
6. The `cloudflared-liber` and `liber-mcp` systemd user units are active. Reported as a warning only,
   since `--http` may be run by hand.

## 8. Error handling

| Situation | Behavior |
|---|---|
| Vault missing or `liber.toml` broken | The tool returns `liber vault unavailable: <reason>`; the server stays up. |
| Invisible or missing file on read | `not found: <path>`, identical in both cases. |
| Path escape (`..`, absolute path, symlink out) | `not found: <path>`; logged at warning level. |
| Empty search query; `limit` outside 1–50 | Validation error with the allowed range. |
| Proposal too long, empty, or the inbox cap is reached | An error stating the limit. Nothing is written. |
| Non-allowed GitHub login | Callback refuses with a "private server" page; no token is issued; logged. |
| Expired, revoked, or unknown token | `401` (the client re-authenticates). |
| Secrets missing or permissions too open | `liber serve --http` exits 1 with the fix. |

Logs go to stderr, and therefore to the journal under systemd. They cover logins, rejections,
proposals (with name and size only), and path-escape attempts. **Vault content is never logged.**

## 9. Testing

- **`VaultView`** (temporary vaults):
  - For each tool and each ceiling, both directions: nothing above the ceiling appears in list, read
    or search, and everything at or below it does.
  - Missing or invalid sensitivity is hidden.
  - Conflict copies are hidden.
  - `sources/` is visible at `private` and invisible at `personal`.
  - `..`, absolute and symlink escapes are refused.
  - Search ranking, tie-breaks and snippets.
  - Proposal naming, size limits, the 50 cap, and collision suffixes.
  - `open-questions.md` counts as `personal`.
- **Tools** via fastmcp's in-memory client: a local-mode server sees `private` and `sources/`; an
  OAuth-ceiling server does not. `propose_update` creates the inbox file.
- **Auth** (in-process HTTP app):
  - Unauthenticated → `401` with `resource_metadata`.
  - The protected-resource metadata's `resource` is exact.
  - A service token is accepted; a revoked or wrong one is rejected.
  - The allow-list rejects a simulated non-allowed login at the callback (GitHub is mocked).
  - Secrets permission refusal.
- **Setup and doctor:** `server init` writes the expected files with the right modes. Each doctor check
  is tested against a fake HTTP responder for both pass and fail.
- **Spike** (first task): the fastmcp v4 hook points for (a) multiple verifiers and (b) callback
  allow-list enforcement. Findings are recorded in the plan before dependent tasks run.
- **Manual live checklist**, `docs/manual-test/SERVER-CHECKLIST.md`:
  - Claude Code via stdio.
  - claude.ai web connector, then the same on mobile.
  - ChatGPT developer mode.
  - A login from a second GitHub account is refused.
  - A proposal is ingested through `/ingest`.
  - `liber logout-all` forces re-login.

## 10. User-performed setup

These steps change external accounts or system services, so the README documents them and the user
carries them out:

1. Add `kineticrick.com` to Cloudflare (free plan) and switch its nameservers at the registrar.
2. Install `cloudflared`, then run `cloudflared tunnel login`, `cloudflared tunnel create liber`, and
   `cloudflared tunnel route dns liber liber.kineticrick.com`.
3. Create a GitHub OAuth App:
   - homepage: `https://liber.kineticrick.com`
   - callback: `https://liber.kineticrick.com/auth/callback`
4. Run `liber server init`, then the printed `systemctl --user enable --now …` and `loginctl enable-linger` commands.
5. Run `liber server doctor` until it shows all ✓.
6. Connect the clients (README "Connect your AI tools").

Do not put Cloudflare Access, WAF challenges, or bot-fight rules on `liber.kineticrick.com`, because
Anthropic's and OpenAI's servers must reach `/.well-known/*`, `/authorize`, `/token`, `/register` and `/mcp`.

## 11. Documentation

The README gains:

- a "Connect your AI tools" section, with the Claude Code command, the Claude Desktop JSON,
  claude.ai/mobile, ChatGPT developer mode, and a service-token example;
- the §10 setup steps;
- the access-level table;
- the new commands in the command reference.

The architecture diagram drops "(later)" from the MCP server. `docs/hardening-backlog.md` is unchanged.
