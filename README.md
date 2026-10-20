# liber

**liber** is a personal knowledge base about you: your work history, life, likes and dislikes, passions, expertise, and the people in your life. It's kept as plain Markdown in a private git repo, so any LLM, chatbot, or agent can use it as "context about me", and it grows over time as you add to it.

## Why "liber"

The name comes from Carl Jung's *Liber Novus* ("The New Book"), better known as *The Red Book*. Jung spent about sixteen years writing it: a private, ever-growing record of his inner life that was never meant as a public face. That is the spirit of this project, an honest, accumulating record of a whole person.

## How it fits together

```
 capture                     process                      use
 ───────                     ───────                      ───
 inbox.md  (quick notes)  ┐
 inbox/    (documents)    │
 voice interviews         │
 talks + podcasts         ├─► /ingest in Claude Code ─► vault files ─► liber bundle → any chatbot
 AI sessions (proposals)  ┘   (you approve each change)   (git history)   liber serve (MCP) → AI tools
```

- **The vault** (for example `~/liber-vault`) is your content: Markdown files with small frontmatter headers, a private git repo, and an Obsidian vault synced to your phone.
- **This repo** is the tool: the `liber` command and two Claude Code skills, `/ingest` and `/review`.

Every AI tool can reach the vault through liber's MCP server: Claude Code and Claude Desktop locally, and claude.ai (web and phone), ChatGPT and other cloud tools through `https://<your domain>/mcp`. See [Connect your AI tools](#connect-your-ai-tools). Voice interviews (below) let you fill the vault by talking, [`liber transcribe`](#transcribing-talks-and-podcasts) turns recordings of you speaking into transcripts, and [passive capture](#passive-capture-from-ai-sessions) lets your everyday Claude sessions notice things worth keeping.

## Install

Requires [uv](https://docs.astral.sh/uv/), git (with `user.name` and `user.email` set, since ingest makes commits), and [Claude Code](https://claude.com/claude-code). Optional: `wl-copy` or `xclip` for `liber bundle --copy`.

```bash
git clone <this repo> ~/code/python/liber
cd ~/code/python/liber
uv tool install --editable .
```

The `--editable` install matters. Your vault's skills are symlinks into this repo, so updating the repo updates the skills everywhere.

Editable installs pick up code changes but not new dependencies. After pulling changes that add dependencies, re-run the install with `--force`, adding any extras you use (for example `uv tool install --editable '.[voice]' --force`). A `ModuleNotFoundError` from `liber` usually means this step is due.

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
5. **Settings → Daily notes,** for journaling: set the new file location to `inbox`, the date format to `[journal-]YYYY-MM-DD` and the template to `_templates/journal`. "Open today's daily note" then creates `inbox/journal-<date>.md`. In **Sync settings**, turn on **Core plugin settings** so the phone uses the same setup.
6. On your Android phone: open Obsidian, connect to the same remote vault, and add thoughts to `inbox.md` whenever you like.

git runs on the desktop only. Obsidian Sync doesn't sync hidden folders like `.git/`, so there's nothing to exclude.

### Off-site backup (optional)

Obsidian Sync isn't a backup of your git history. To keep one, push the vault to a **private** remote, for example `gh repo create <you>/liber-vault --private --source ~/liber-vault --remote origin --push`, and check the result with `gh repo view <you>/liber-vault --json visibility`. Never make it public. The repo holds everything, including `private` files and `sources/`, so whoever can read it can read everything. Once the vault has an upstream, `/ingest` pushes at the end of each run, but only after confirming the GitHub repo is private. It never force-pushes. Other commits, such as manual edits or `/review`, stay local until you run `git push`.

To seed liber from notes you already have in another Obsidian vault, copy them into `inbox/` and run `/ingest`.

- **Storage:** original documents in `sources/documents/` count against your Sync storage. If that becomes a problem, add `sources/documents` to Sync's excluded folders. The originals stay safe in git on the desktop.
- **Conflicts:** Obsidian Sync usually merges edits automatically. If it ever leaves a conflict copy, `liber check` finds it, and `/ingest` helps you merge it. The file-name patterns are in `liber.toml` under `[sync] conflict_patterns`.

## Everyday use

1. **Capture:**
   - Add a line to `inbox.md` (from your phone or desktop), or run `liber note "..."`.
   - Drop documents into `inbox/`, or run `liber add file.pdf`.
   - Journal in Obsidian's daily note (see [Obsidian and phone sync](#obsidian-and-phone-sync-obsidian-sync)). Any inbox file with "journal" in its name counts as a journal in `liber stats`, and `/ingest` files what it learns from it in `private` files unless you say otherwise.
   - Let your AI sessions propose additions as you work (see [Passive capture](#passive-capture-from-ai-sessions)).
2. **Ingest:** open Claude Code in your vault and run `/ingest`.
   - Claude proposes dated, sourced edits, flags contradictions, and suggests structural changes.
   - You approve or correct them in plain language. Each item becomes one git commit.
   - If the vault has a private backup remote, the commits are pushed at the end.
3. **Use:**
   - `liber bundle --topics career,finance,goals,core --copy` puts a single document on your clipboard, ready to paste into any chatbot.
   - For example: "Given everything about me, what new revenue sources should I pursue?"
   - Bundles stop at `personal` by default. Add `--max-sensitivity private` only when pasting into a chat you trust with your private files.
   - To have an AI write in your voice, point it at `core/writing-style.md` (how you write, by register) and `core/writing-examples.md` (real examples, private). A short global Claude Code skill in `~/.claude/skills/` that reads both through the MCP server works well; tell it to match the examples' density and never to add private facts to a draft.
4. **Review:** run `/review` every month or so.
   - It finds uncaptured parts of your life, stale facts, and structural improvements.
   - It adds questions to `open-questions.md`. Questions that would reveal private information go in the relevant private file instead (see [Health and finance](#health-and-finance)).

## Vault layout

| Path | Contents |
|---|---|
| `AGENTS.md` | One-page summary of you, a map of the vault, and rules for AI readers. The first thing any tool reads |
| `CLAUDE.md` | Conventions Claude Code follows in the vault |
| `liber.toml` | The vault's structure (folders and their `type`), size limits, and sync settings |
| `inbox.md`, `inbox/` | Quick notes and documents waiting for `/ingest` |
| `open-questions.md` | Gaps to fill later |
| `ledger.jsonl` | The input ledger: one line per captured item, written by the capture commands and committed by `/ingest` (see [Tallies](#tallies)) |
| `core/`, `career/`, `people/`, `interests/`, `health/`, `finance/`, `goals/`, `log/` | Your knowledge base |
| `sources/` | The originals everything came from: notes and documents, including interview and talk transcripts |
| `_templates/` | Obsidian templates, for example `person.md` |

The structure is meant to grow. When Claude suggests a new folder and you approve it, the folder is added to `liber.toml`, and `liber check` accepts it from then on.

### Health and finance

These two areas hold the most sensitive material, so each one splits by sensitivity:

| Folder | `personal` (cloud tools and the voice interviewer can read it) | `private` (local tools only) |
|---|---|---|
| `health/` | Training and diet, for example `fitness.md` and `nutrition.md` | Medications, lab results, conditions and open investigations. Start with `overview.md` |
| `finance/` | How you think about money and investing, in `overview.md` | Figures: holdings, balances, income, debt and taxes, for example `portfolio.md` and `situation.md` |

Two rules keep private details from leaking:

- **Open questions.** `open-questions.md` is readable by cloud tools. So a question that would reveal something private, such as "what dose of X do you take?", goes in an `## Open questions` section of the relevant private file instead.
- **No account identifiers.** Account numbers, logins and passwords are never written into vault notes, even private ones. `/ingest` summarizes financial documents instead. The original document is still archived as-is in `sources/`, which cloud tools never see but which is committed to the vault's git. Redact account numbers before dropping in a statement if you don't want them kept at all.

The passion side of investing (why it energizes you, the books and people that shaped you) can live in `interests/`, linked to `finance/`. Financial goals stay in `goals/`.

Every content file starts with:

```yaml
---
type: core            # must match the folder's type in liber.toml
updated: 2026-09-30
sensitivity: personal # public | personal | private
tags: []
---
```

## Tallies

liber keeps count of what goes in and what comes out, so you can quote real numbers about your own vault.

- **Input** is counted the moment an item lands in the inbox. `liber note`, `liber add`, `liber transcribe`, `liber interview` and MCP proposals each record it. Anything that arrives without a liber command, such as a note typed on your phone, is recorded by a sweep (`liber ledger sweep --notes`) at the start of `/ingest`. `liber status` sweeps documents too.
- **Output** is computed from the vault when you ask, so it cannot drift. A fact is a list item in a knowledge folder that carries at least one `(src: …)` link, which is how `/ingest` writes every fact. Private facts are counted, never shown.
- **Owner words** are the words you wrote or said. Notes, documents, journals and emails count all their words. In a transcript, only the turns labeled with your name count. Total words include everyone else, such as a podcast host. Material written by an AI, such as MCP proposals and interview notes (including regenerated ones), counts as 0 owner words, though a proposal still counts as an item. A document with no extractable text has no word count until you paste its text into its `.md` sidecar; the next sweep counts it. An inbox file you edit before `/ingest` stays one item, and the sweep updates its word count.
- **Your name** comes from `[owner] name` in `~/.config/liber/config.toml`:

  ```toml
  [owner]
  name = "Your Name"
  ```

  If it is missing, liber falls back to `[interview] name`. If neither is set, or a transcript has no labeled speaker turns, its owner words are left out of the total, and `liber stats` says so in a note below the headline. Once you set the name, the next sweep fills in owner words for transcripts still in the inbox.
- **The ledger** is `ledger.jsonl` at the vault root: one JSON line per captured item, append-only. Capture commands write to it but don't commit it. `/ingest` commits it as `ingest: ledger` along with its other commits. If Obsidian Sync leaves a conflict copy, `/ingest` merges it with `liber ledger merge`.

`liber stats` prints the numbers. `--md` prints Markdown for pasting, and `--json` prints the raw report for other tools. For example:

```
liber has taken in 3 words I've written or said.
It stores 0 facts about me (0 private).

Input
  note                     1 items            3 words            3 mine

Output
  facts                            0
  ...
```

The report lists input by kind, what the vault stores (`words stored` is the size of the vault's own knowledge files, not input), and month-by-month growth. The "going back to" year comes from fact dates such as `(2020)` or `[1995–1999]`, never from other numbers in a fact. It never prints item names, labels, keys or paths, so `--md` output is safe to paste publicly. Labels stay in the ledger, and `liber ledger list` is the only command that shows them.

If your vault existed before this feature, run `liber ledger backfill` once; `liber stats` reminds you until you do. It records everything already archived in `sources/` as input, up to a cutoff date (`--cutoff YYYY-MM-DD`, default today) that is fixed by the first run. Items archived after the cutoff are recognized by content, so nothing is counted twice. The count it prints leaves out derived files, which are recorded for traceability only. For bulk imports that happen outside the CLI, use `liber ledger record`.

## Voice interviews

Talk instead of typing. `liber interview` opens a page in your browser where a voice interviewer asks you questions, follows up, and lets you think. Afterwards a transcript and structured session notes land in `inbox/` for `/ingest`.

**How it works**
- **The voice** is OpenAI's GPT-Live-1, in full duplex: it listens while it talks and handles pauses and interruptions. Audio runs in your browser, which cancels echo, so speakers are fine.
- **The brain** is Claude, running on your machine. It reads your vault, but only up to `personal`, so `private` files never reach either company. It quietly steers the interviewer's follow-ups, answers when the interviewer hands off, and writes the session notes at the end.
- **What leaves your machine:** your voice and the conversation go to OpenAI (GPT-Live-1). Claude (Anthropic) receives the transcript and `personal`-level excerpts from your vault. `private` files and `sources/` are never sent to either. Transcripts and notes are saved only on your machine. Exception: `--continue` sends your previous interview's notes, and `--notes <file>` sends that transcript — even after `/ingest` has archived them into `sources/` — because they came from your own earlier interview.
- **Cost:** about $0.05 per minute of voice, plus Claude. A 45-minute interview costs roughly $2–4.

**Set up once**
```bash
cd ~/code/python/liber && uv tool install --editable '.[voice]'
liber interview --setup     # after liber init: your first name, OpenAI key (GPT-Live-1 access), Anthropic key
```
The keys are stored in `~/.config/liber/secrets.toml` (mode 600). `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` override them.

**Interview**
```bash
liber interview "my career"     # or just `liber interview` and it picks a gap and tells you why
liber interview --continue      # pick up the last interview's topic; can't combine with a topic
liber interview --minutes 20 --model claude-opus-5-5
```
- **Start, Hold (mutes you; click again to resume talking), Add a note, Resume (reconnects after a dropped connection; after a dropped connection both Resume and End are available), End.** Add a note puts typed text into the transcript without interrupting.
- **Going back.** Say something like "going back to Acme…" at any time. The notes merge the correction into the original fact.
- **When it ends.** You can click End, say you want to stop, or reach the time limit (45 minutes by default, with a warning 5 minutes before). You can also press Ctrl-C in the terminal to finish gracefully (transcript and notes are still written); pressing Ctrl-C again just says it's still finishing. If you close the tab, the interview is finished for you after a minute.
- **The files.** You get `inbox/interview-<date>-<topic>.md` (the transcript) and `…-notes.md` (new facts, corrections, people, preferences and follow-up questions, each cited with a `[mm:ss]` timestamp). Then run `/ingest`.

**If something goes wrong:**
- `liber interview --notes inbox/interview-….md` regenerates the notes (needs only the Anthropic key, but requires the voice extra installed and setup done).
- `liber interview --recover` finishes an interview that was cut off, for example by a crash (needs only the Anthropic key, but requires the voice extra installed and setup done).

Settings live in `~/.config/liber/config.toml` under `[interview]`: `name`, `model`, `notes_model`, `voice`, `max_minutes`, `warn_minutes`.

## Transcribing talks and podcasts

Podcasts, talks and workshops you've given are good material twice over: they capture how you speak, and they're full of facts about your life and work. `liber transcribe` downloads a recording, transcribes it with each speaker named, and saves the transcript to `inbox/` for `/ingest`.

**Set up once**
```bash
cd ~/code/python/liber && uv tool install --editable '.[transcribe]' --force   # add voice too if you use it: '.[voice,transcribe]'
```
- Sign up at [AssemblyAI](https://www.assemblyai.com) and add your key to `~/.config/liber/secrets.toml` (mode 600) as `assemblyai_api_key = "…"`. `ASSEMBLYAI_API_KEY` overrides it.
- YouTube downloads need a JavaScript runtime: [Node.js](https://nodejs.org) or [Deno](https://deno.com).

**Transcribe**
```bash
liber transcribe "https://www.youtube.com/watch?v=…" --speakers "Jake O'Shea, Rick Boone"
liber transcribe "<shared Google Drive link>" --speakers "Rick Boone" --note "Mine this for my career history too."
```
- **`--speakers`** lists who talks, comma-separated. AssemblyAI matches each voice to a name and uses the names to spell them right. With one name, other voices are labeled "Audience"; with several, unmatched voices are "Unidentified speaker". If a name isn't matched to any voice, liber warns you, so check the labels.
- **`--note`** puts a note for `/ingest` at the top of the transcript, for example to use a long interview for every part of the vault, not only your voice.
- **The file** is `inbox/transcript-<date>-<title>.md`: the title, publish date and link, then one line per turn with the speaker, a timestamp and their words, verbatim. Long monologues are split into timestamped paragraphs.
- **Sources:** anything [yt-dlp](https://github.com/yt-dlp/yt-dlp) can download: YouTube, Google Drive files shared with "anyone with the link", Vimeo and most public sites. Spotify's audio is copy-protected, so for a Spotify podcast use the same episode's link from Apple Podcasts or the show's website.
- **Cost and privacy:** AssemblyAI charges a small amount per hour of audio; check their pricing page. The audio goes to AssemblyAI; the transcript is saved only on your machine. The downloaded audio is deleted afterward.

## Passive capture from AI sessions

Much of what's worth knowing about you comes out while you're doing something else: rewriting a draft into your own voice, rejecting a phrase because it sounds wrong, explaining why you prefer one approach. Passive capture asks Claude Code to notice those moments in any project and offer to send them to liber.

**How it works**
- A standing instruction in your global `~/.claude/CLAUDE.md` (loaded by every Claude Code session) tells Claude what to watch for:
  - **Writing voice and tone:** your edits to its drafts (the diff is the evidence), words you reject and why, stated style preferences
  - **Opinions:** your views on technology, leadership, work, products and ideas
  - **Preferences:** likes, dislikes, how you like to work
  - **Values, priorities and goals** that come through in your decisions
  - **Facts about your life and work** that come up naturally
- At a natural pause, Claude summarizes what it noticed and **asks before sending anything**.
- On approval it calls the MCP `propose_update` tool, which lands a `proposal-*.md` in `inbox/`. If the tool isn't available, it writes a Markdown file to `inbox/` instead.
- Each capture includes what you were working on, concrete evidence (before → after examples, your own words and reasons), the limits of that evidence, and a suggestion for where it might belong.
- Claude never writes into vault folders directly. `/ingest` decides placement, and you approve every change, as with any other capture.

**Set up once**
1. Register the local MCP server so every Claude Code session has `propose_update`:
   ```bash
   claude mcp add --scope user liber -- liber serve
   claude mcp list    # liber should show ✔ Connected
   ```
2. Add a section like this to `~/.claude/CLAUDE.md`, with your own vault path:
   ```markdown
   # Personal context capture (liber)
   - My personal knowledge base lives at `~/liber-vault`. It should become a full-fledged store of context about me.
   - During any work, watch for evidence about me worth keeping: writing voice and tone (my edits to your drafts, words I reject and why), opinions, preferences, values and goals, and facts about my life and work.
   - At a natural pause, if the evidence is meaningful and not a one-off, summarize it in a few lines and ask before sending.
   - On approval, send it with the liber MCP `propose_update` tool; fallback: write `~/liber-vault/inbox/<topic>-<YYYY-MM-DD>.md`.
   - Include what we were working on, concrete evidence, its limits, and what /ingest should do. Never write directly into vault content folders.
   - Never send account numbers, credentials or other secrets. Flag sensitive material (health, finance, private matters about others) for /ingest.
   - High bar: skip trivia and passing remarks. Capture patterns, rejections with reasons, and opinions or preferences I state clearly or repeat.
   ```
3. Restart running Claude Code sessions. They load `CLAUDE.md` and MCP servers only at startup.

For claude.ai, add liber as a connector (see [Cloud apps](#cloud-apps-one-time-setup)) and put a short version of the instruction in your profile preferences.

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

   Use the same hostname everywhere — the DNS route, the OAuth app URLs, and the public URL you give `liber server init` (its default is `https://liber.kineticrick.com`).
4. **Configure liber:** run `liber server init`. It asks for the public URL, the GitHub login allowed to connect, and the OAuth app's ID and secret. It then writes:
   - `~/.config/liber/secrets.toml` (mode 600)
   - the `[server]` config
   - two systemd user services
   - `~/.cloudflared/liber.yml`

   `liber server init` needs `liber` and `cloudflared` on your PATH and expects exactly one tunnel credentials file in `~/.cloudflared/` (otherwise pass `--tunnel-credentials <file>`). Re-running it is refused once `secrets.toml` exists; `--force` replaces the secrets and signs out every connected app.
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

GitHub sign-ins last up to a year. They end when you run `liber logout-all`, remove the login from `allowed_github_logins`, or revoke the liber OAuth app in GitHub (picked up within about five minutes).

## Commands

| Command | What it does |
|---|---|
| `liber init <path>` | Create a new vault and make it the default |
| `liber note "text"` | Add a dated note to `inbox.md` |
| `liber add <file>…` | Copy documents into `inbox/`, never overwriting |
| `liber status` | Show waiting notes, documents (and whether they've been converted to text), and sync conflicts |
| `liber ledger sweep [--notes]` | Record inbox items that arrived without a liber command (for example phone capture) in `ledger.jsonl`. `--notes` also records pending `inbox.md` notes. `liber status` sweeps files automatically |
| `liber ledger record --kind K --key KEY […]` | Record an input explicitly, for example a bulk import (`--items`, `--words`, `--owner-words`, `--audio-seconds`, `--years`, `--cost-usd` with `--cost-basis`, `--raw`, `--label`) |
| `liber ledger list` | List ledger entries, oldest first. The only place labels are printed |
| `liber ledger merge <copy>` | Fold a sync-conflict copy of `ledger.jsonl` into the ledger, then delete it |
| `liber ledger backfill [--cutoff YYYY-MM-DD]` | Record everything already archived in `sources/` before the ledger began |
| `liber stats [--md \| --json]` | Quotable numbers: what has gone into the vault, what it stores, and how it grew |
| `liber extract` | Convert waiting PDF, Word, and HTML documents to Markdown text |
| `liber archive <name>` | Move a processed document (and its text) to `sources/documents/`, printing the final name |
| `liber archive --notes [--count N]` | Move processed notes (optionally only the first N) to `sources/notes/<YYYY-MM>.md` |
| `liber check` | Validate frontmatter, folder structure, `[[links]]`, sync conflicts, and `AGENTS.md` size. Exits 1 on problems |
| `liber bundle [--topics a,b] [--max-sensitivity personal] [--copy]` | Combine `AGENTS.md` and the selected folders into one Markdown document. Files above the ceiling, or with missing or invalid sensitivity, are left out |
| `liber serve` | Run the MCP server over stdio for local apps (sees everything) |
| `liber serve --http` | Run the MCP server over HTTP with GitHub login, for the tunnel (normally run by systemd) |
| `liber server init [--force] [--tunnel-credentials <file>]` | Set up the HTTP server: secrets, config, systemd services and tunnel config |
| `liber server doctor` | Check the HTTP server end to end, as claude.ai and ChatGPT will see it |
| `liber token create/list/revoke <name>` | Manage service tokens for tools that can't sign in |
| `liber logout-all` | Sign out every connected cloud app |
| `liber transcribe <url> --speakers "A, B" [--note text]` | Transcribe a podcast or talk with named speakers into `inbox/` |
| `liber interview [TOPIC] [--continue] [--minutes N] [--model ID] [--no-browser]` | Voice interview; transcript and notes land in `inbox/` (`--setup`, `--notes <file>`, `--recover` for maintenance) |

## Development

```bash
uv sync
uv run pytest
```

The skills live in `src/liber/skills/`. After changing them, walk through `docs/manual-test/CHECKLIST.md`. After changing the server, walk through `docs/manual-test/SERVER-CHECKLIST.md`. After changing the interviewer, walk through `docs/manual-test/INTERVIEW-CHECKLIST.md`; `uv run pytest -m live` checks your real keys (a few cents). The designs and plans are in `docs/superpowers/`.
