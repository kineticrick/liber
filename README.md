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
 voice transcripts (later)┘   (you approve each change)   (git history)   liber serve (MCP) → AI tools
```

- **The vault** (for example `~/liber-vault`) is your content: Markdown files with small frontmatter headers, a private git repo, and an Obsidian vault synced to your phone.
- **This repo** is the tool: the `liber` command and two Claude Code skills, `/ingest` and `/review`.

Every AI tool can reach the vault through liber's MCP server: Claude Code and Claude Desktop locally, and claude.ai (web and phone), ChatGPT and other cloud tools through `https://<your domain>/mcp`. See [Connect your AI tools](#connect-your-ai-tools). Planned next: a voice interviewer (OpenAI GPT-Live-1 for the conversation, Claude as the interviewer's brain) that drops transcripts into `inbox/`.

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

## Connect your AI tools

liber's MCP server gives tools five capabilities: `get_user_profile`, `list_user_knowledge`, `read_user_knowledge`, `search_user_knowledge` and `propose_update`. No tool can edit your vault. Proposals land in `inbox/` as files named `proposal-*.md`, and you review them with `/ingest`.

What each connection can see:

| Connection | Sees up to | `sources/` |
|---|---|---|
| Local (`liber serve`: Claude Code, Claude Desktop) | `private` | yes |
| Cloud apps signed in with GitHub (claude.ai, ChatGPT) | `personal` | no |
| Service tokens (voice backend, scripts) | `personal` | no |

Files above a connection's level don't exist for it. They are absent from lists and search, and reading one says "not found". You can change the levels in `~/.config/liber/config.toml` under `[server.ceilings]`.

### Local apps

- **Claude Code:** `claude mcp add --scope user liber -- liber serve`
- **Claude Desktop:** add this to `claude_desktop_config.json`, using the full path that `which liber` prints:
  ```json
  { "mcpServers": { "liber": { "command": "/home/you/.local/bin/liber", "args": ["serve"] } } }
  ```

### Cloud apps (one-time setup)

The HTTP server runs on your desktop and is reached through a Cloudflare Tunnel, so the desktop has to be on for cloud apps to reach it.

1. **Domain:** add a domain you control (for example `kineticrick.com`) to a free Cloudflare account, and switch its nameservers at your registrar. Cloudflare's free plan needs the whole domain; it can't take just one subdomain.
2. **Tunnel:** [install `cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/), then:
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create liber
   cloudflared tunnel route dns liber liber.example.com
   ```
3. **GitHub OAuth app:** in GitHub → Settings → Developer settings → OAuth Apps → New OAuth App, set:
   - Homepage URL: `https://liber.example.com`
   - Authorization callback URL: `https://liber.example.com/auth/callback`

   Keep the client ID, and generate a client secret.
4. **Configure liber:** run `liber server init`. It asks for the public URL, the GitHub login allowed to connect, and the OAuth app's ID and secret. It then writes:
   - `~/.config/liber/secrets.toml` (mode 600)
   - the `[server]` config
   - two systemd user services
   - `~/.cloudflared/liber.yml`
5. **Start the services:**
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now liber-mcp.service cloudflared-liber.service
   loginctl enable-linger $USER    # keep running when you're logged out
   ```
6. **Check it:** run `liber server doctor` until every line shows ✓.
7. **Connect:**
   - **claude.ai:** go to Settings → Connectors → Add custom connector, and enter `https://liber.example.com/mcp`. Sign in with GitHub when asked. The connector then works in the Claude mobile app too.
   - **ChatGPT:** turn on developer mode (Settings → Apps & Connectors → Advanced), then create a connector with the same URL.

Don't put Cloudflare Access, WAF challenges or bot protection on this hostname. Anthropic's and OpenAI's servers must be able to reach the OAuth endpoints. Your protection is the GitHub login: anyone else who tries is refused before they get a token.

### Service tokens

These are for tools that can't run a login flow, such as OpenAI's Realtime/GPT-Live MCP tool:

```bash
liber token create voice-backend   # printed once; send it as "Authorization: Bearer <token>"
liber token list
liber token revoke voice-backend   # takes effect immediately
```

`liber logout-all` signs out every cloud app; restart the server afterwards with `systemctl --user restart liber-mcp`.

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
| `liber serve` | Run the MCP server over stdio for local apps (sees everything) |
| `liber serve --http` | Run the MCP server over HTTP with GitHub login, for the tunnel (normally run by systemd) |
| `liber server init` | Set up the HTTP server: secrets, config, systemd services and tunnel config |
| `liber server doctor` | Check the HTTP server end to end, as claude.ai and ChatGPT will see it |
| `liber token create/list/revoke <name>` | Manage service tokens for tools that can't sign in |
| `liber logout-all` | Sign out every connected cloud app |

## Development

```bash
uv sync
uv run pytest
```

The skills live in `src/liber/skills/`. After changing them, walk through `docs/manual-test/CHECKLIST.md`. After changing the server, walk through `docs/manual-test/SERVER-CHECKLIST.md`. The designs and plans are in `docs/superpowers/`.
