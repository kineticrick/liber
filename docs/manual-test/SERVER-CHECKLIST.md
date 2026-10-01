# Manual test: the liber MCP server

Some of this can only be tested live. Run through it after changing anything in `src/liber/server/`, and after the first setup.

## Local (stdio)

- [ ] `claude mcp add --scope user liber -- liber serve`, then in Claude Code ask: "Use liber: what's in my profile?" Claude calls `get_user_profile` and quotes your `AGENTS.md`.
- [ ] Ask about something only in a `private` file. Claude finds it, because local connections see everything.
- [ ] Ask Claude to "propose to liber that I started learning Rust this week". A `proposal-*-local.md` file appears in `inbox/`.

## Remote (HTTP)

- [ ] `liber server doctor` shows ✓ on every line except possibly the `!` service warnings.
- [ ] **claude.ai on the web:** add the custom connector `https://<your domain>/mcp` and sign in with GitHub as the allowed login. The connector shows as connected, and asking about your profile works.
- [ ] Ask about something in a `private` file. Claude can't find it.
- [ ] **Claude mobile app:** the same connector works without re-adding it.
- [ ] **ChatGPT developer mode:** add a connector with the same URL, sign in, and ask about your profile.
- [ ] **A wrong GitHub account:** in a private browser window, start a connector sign-in and log in to GitHub as a different account. You see "This liber server is private.", and `journalctl --user -u liber-mcp` logs the refusal.
- [ ] **Service token:** run `liber token create smoke`, then
  `curl -s https://<your domain>/mcp -H "authorization: Bearer <token>" -H "content-type: application/json" -H "accept: application/json, text/event-stream" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`
  lists 5 tools. After `liber token revoke smoke`, the same curl returns 401.
- [ ] **Logout:** run `liber logout-all` and `systemctl --user restart liber-mcp`. claude.ai asks you to sign in again.

## Proposals end to end

- [ ] Ask claude.ai to propose a fact. A `proposal-*-claude*.md` file appears in `inbox/`. Running `/ingest` in the vault offers it like any other document, and approving it updates the vault with one commit.
