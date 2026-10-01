# liber Access Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Claude Code/Desktop (stdio), claude.ai, ChatGPT, and a future voice backend (Streamable HTTP via `https://liber.kineticrick.com/mcp`) permission-aware read access to the liber vault plus a propose-into-inbox tool, behind GitHub OAuth (allow-listed) and revocable service tokens.

**Architecture:** A new `liber.server` package. `knowledge.py` is a pure access layer: `VaultView(vault, ceiling)` handles visibility, list, read, search and propose, and does not import fastmcp. `app.py` defines five thin fastmcp tools that resolve the caller's ceiling and call `VaultView`. `auth.py` wraps fastmcp's `GitHubProvider` OAuth proxy with an allow-list enforced at the OAuth callback, and combines it with a service-token verifier through `MultiAuth`. `settings.py`, `tokens.py`, `setup.py` and `doctor.py` cover configuration, service tokens, `liber server init` and `liber server doctor`.

**Tech Stack:** Python 3.12, uv, Typer, `fastmcp==4.0.10` (pins `mcp==2.2.0`, brings `httpx2`, `py-key-value-aio`, `cryptography`, `anyio`), `httpx`, pytest with anyio's bundled plugin.

**Spec:** `docs/superpowers/specs/2026-10-01-liber-access-server-design.md`

**Spike findings (2026-10-01, fastmcp 4.0.10, all verified by running code).** The spec asked for a spike before the build. It was done during planning, and its answers are built into the tasks below:

- **Two kinds of token.** `MultiAuth(server=provider, verifiers=[...])` is native, so no hand-written combined verifier is needed. Service tokens must return `scopes=list(provider.required_scopes)` (that is, `["user"]`), or they get 403.
- **Allow-list.** The allow-list is enforced by overriding `OAuthProxy._handle_idp_callback`. The override calls `super()`, reads the upstream GitHub token from the one-time code store, and checks the login. On refusal it deletes the code and returns 403 with "This liber server is private." `load_access_token` re-checks the login on every request.
- **Client name.** For GitHub tokens, `AccessToken.client_id` is the GitHub user id. The MCP client's id and name are restored by overriding `load_access_token`, which feeds the proposal label.
- **Metadata URLs.** The authorization-server metadata `issuer` and the protected-resource `authorization_servers[0]` carry a trailing slash (`https://host/`).
- **HTTP mode.** `stateless_http=True` with `json_response=True` serves `tools/list` and `tools/call` without `initialize`.
- **Test mocking.** fastmcp uses `httpx2`, so tests mock GitHub with `httpx2.MockTransport` and an override of `_create_upstream_oauth_client`. `ASGITransport` does not run lifespan, so tests wrap the app in `app.router.lifespan_context(app)`.
- **The in-memory `Client(mcp)` bypasses HTTP auth.** `get_access_token()` is `None` both there and under stdio.
- **Key handling.**
  - Pass `jwt_signing_key` as bytes; a `str` triggers 1M PBKDF2 rounds.
  - Storage is `FernetEncryptionWrapper(FileTreeStore(...))`. `DiskStore` needs the missing `diskcache` package.
  - Set `cache_ttl_seconds`; otherwise every request makes 2 GitHub calls.
- **Error and banner settings.**
  - Use `mask_error_details=True` and raise `ToolError` for every intended error.
  - Use `show_banner=False`, which also skips a PyPI version check.

## Global Constraints

- **Runtime dependencies:** the existing ones plus exactly `fastmcp==4.0.10` (pinned exactly, because auth.py overrides private fastmcp internals) and `httpx>=0.28`. Add no others.
- **HTTP options:** always `path="/mcp"`, `json_response=True`, `stateless_http=True`, `show_banner=False`.
- **Ceilings:** levels are `public` < `personal` < `private`. Defaults are local `private`, oauth `personal`, service `personal`.
- **Visibility:** a file is visible only if all of these hold:
  - it is a Markdown file in a content folder (as returned by `content_paths`), `AGENTS.md`, `open-questions.md` (treated as `personal`), or, at the `private` ceiling only, a `.md` or `.txt` file under `sources/` (treated as `private`);
  - its sensitivity is valid and at or below the ceiling;
  - it is not a sync-conflict copy;
  - its resolved real path is inside the vault;
  - it is readable as UTF-8.
- **Error strings:**
  - Invisible and missing files both read `not found: <path>`.
  - Vault problems read `liber vault unavailable: <reason>`.
- **Proposals:**
  - File name: `inbox/proposal-<YYYY-MM-DDTHH-MM-SS>-<label>.md`.
  - Limits: text 1–20000 characters, context ≤ 2000 characters, at most 50 pending `inbox/proposal-*.md` files.
  - Label: characters `[a-z0-9.-]`, at most 40, falling back to `unknown`. Stdio uses `local`, service tokens use the token name, and OAuth uses the MCP client name.
- **Search:**
  - `limit` defaults to 10, range 1–50.
  - Score is body hits plus title hits; the body is the text without frontmatter.
  - Results with all terms rank first, then by score, then by newest `updated`, then by path.
  - Up to 3 snippets of about 200 characters each.
- **Files and modes:**
  - `~/.config/liber/secrets.toml`: mode `0600`, required, with keys `github_client_id`, `github_client_secret`, `jwt_signing_key`, `storage_encryption_key`.
  - Data directory: `$XDG_DATA_HOME/liber` (default `~/.local/share/liber`).
  - OAuth storage: `<data>/oauth/`.
  - Service tokens: `<data>/service-tokens.json`, mode `0600`, sha256 hashes only.
- **Logging:** logs go to stderr and must never contain vault content or proposal text, only names and sizes.
- **CLI errors:** every failure prints `error: <message>` and exits 1, via the existing `handle_errors()`.
- **Tests:**
  - Async tests use `pytestmark = pytest.mark.anyio` plus an `anyio_backend` fixture in `tests/conftest.py`.
  - Tests never touch the real home directory, config, data or network. `XDG_DATA_HOME` is isolated, and `FASTMCP_CHECK_FOR_UPDATES=off` is set.
  - Run with `uv run pytest` from the repo root.
- **Commit messages** end with these two lines, where the first names the model that actually made the commit:
  ```
  Co-Authored-By: <your model name> <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV
  ```

## Review Focus

1. **A symlink in a content folder pointing outside the vault**, either to a file or to a directory, must be invisible to `list`, `read` and `search` at every ceiling. Test in Task 2.
2. **A file under `sources/` whose frontmatter claims `sensitivity: public`** must still be hidden from `personal` and `public` connections. Test in Task 2.
3. **A search query that matches only text in an invisible file** (above the ceiling, a conflict copy, or a source at `personal`) must return no result and no snippet. Test in Task 3.
4. **A hostile or odd client name** such as `../../etc/passwd`, unicode, or an empty name must still produce a proposal file directly inside `inbox/` with a sanitised name. Test in Task 3.
5. **A service token revoked while the HTTP server keeps running** must be rejected on the very next request, with no restart. Test in Task 6.

## File Structure

```
pyproject.toml                  + fastmcp==4.0.10, httpx>=0.28
src/liber/docs.py               + content_paths() (refactor out of iter_content_files)
src/liber/cli.py                + serve, logout-all, `token` sub-app, `server` sub-app
src/liber/server/__init__.py    package marker
src/liber/server/settings.py    Ceilings, ServerSettings, Secrets; load/write; data_dir; key generators
src/liber/server/knowledge.py   VaultView, Doc, NotFound, proposal_label
src/liber/server/app.py         Identity, identity_for, build_server (5 tools)
src/liber/server/tokens.py      TokenStore, TokenInfo
src/liber/server/auth.py        AllowListGitHubProvider, ServiceTokenVerifier, build_http_server, run_http, logout_all
src/liber/server/setup.py       init_server, find_tunnel_credentials, require_command
src/liber/server/doctor.py      Check, run_doctor, default_http_client, systemctl_active
tests/conftest.py               + XDG_DATA_HOME, FASTMCP_CHECK_FOR_UPDATES, anyio_backend
tests/test_server_*.py          one file per module
docs/manual-test/SERVER-CHECKLIST.md
README.md                       + "Connect your AI tools", access server setup, commands
```

---

### Task 1: Dependencies, server settings and secrets

**Files:**
- Modify: `pyproject.toml` (dependencies)
- Modify: `tests/conftest.py` (isolation and the anyio backend)
- Create: `src/liber/server/__init__.py`, `src/liber/server/settings.py`
- Test: `tests/test_server_settings.py`

**Interfaces:**
- Consumes: `liber.config.user_config_path() -> Path`, `liber.errors.LiberError`, `liber.vaultconfig.SENSITIVITY_LEVELS`
- Produces:
  - `@dataclass(frozen=True) Ceilings(local: str = "private", oauth: str = "personal", service: str = "personal")`
  - `@dataclass(frozen=True) ServerSettings(base_url: str, host: str, port: int, allowed_github_logins: tuple[str, ...], ceilings: Ceilings)`, where `base_url` has no trailing slash
  - `@dataclass(frozen=True) Secrets(github_client_id: str, github_client_secret: str, jwt_signing_key: str, storage_encryption_key: str)`
  - `DEFAULT_PORT = 8765`, `SECRET_KEYS: tuple[str, ...]`
  - `secrets_path() -> Path`, `data_dir() -> Path`
  - `load_ceilings() -> Ceilings`, `load_server_settings() -> ServerSettings`, `load_secrets() -> Secrets`, `write_secrets(secrets: Secrets) -> Path`
  - `new_jwt_signing_key() -> str` (urlsafe base64 of 32 random bytes), `new_storage_key() -> str` (Fernet key)
  - conftest fixture `anyio_backend` returning `"asyncio"`

- [ ] **Step 1: Add dependencies**

In `pyproject.toml`, change the `dependencies` list to:
```toml
dependencies = [
    "typer>=0.12",
    "python-frontmatter>=1.1",
    "markitdown[pdf,docx]>=0.1.2",
    "fastmcp==4.0.10",
    "httpx>=0.28",
]
```
Run: `uv sync`
Expected: resolves and installs `fastmcp 4.0.10` and `mcp 2.2.0` without errors. Then run `uv run python -c "import fastmcp, httpx2, key_value; print(fastmcp.__version__)"`, which should print `4.0.10`.

- [ ] **Step 2: Extend the test isolation**

In `tests/conftest.py`, add these lines inside `isolated_env`, just before `return home`:
```python
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("FASTMCP_CHECK_FOR_UPDATES", "off")
```
Append this fixture to the end of `tests/conftest.py`:
```python
@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 3: Write failing tests**

`tests/test_server_settings.py`:
```python
import os
import stat

import pytest

from helpers import write
from liber.config import user_config_path
from liber.errors import LiberError
from liber.server.settings import (
    Ceilings, Secrets, data_dir, load_ceilings, load_secrets, load_server_settings,
    new_jwt_signing_key, new_storage_key, secrets_path, write_secrets,
)

SERVER_TOML = """
vault = "/tmp/v"

[server]
base_url = "https://liber.example.com/"
port = 9000
allowed_github_logins = ["rick"]

[server.ceilings]
oauth = "public"
"""


def test_data_dir_uses_xdg(isolated_env):
    assert data_dir() == isolated_env / ".local" / "share" / "liber"


def test_secrets_path_is_next_to_config():
    assert secrets_path() == user_config_path().parent / "secrets.toml"


def test_ceiling_defaults_without_config():
    assert load_ceilings() == Ceilings("private", "personal", "personal")


def test_load_server_settings():
    write(user_config_path(), SERVER_TOML)
    s = load_server_settings()
    assert s.base_url == "https://liber.example.com"
    assert (s.host, s.port) == ("127.0.0.1", 9000)
    assert s.allowed_github_logins == ("rick",)
    assert s.ceilings == Ceilings("private", "public", "personal")


def test_missing_server_section_points_to_server_init():
    write(user_config_path(), 'vault = "/tmp/v"\n')
    with pytest.raises(LiberError, match="liber server init"):
        load_server_settings()


@pytest.mark.parametrize("body, match", [
    ('base_url = "liber.example.com"\nallowed_github_logins = ["rick"]', "base_url"),
    ('base_url = "https://x"\nallowed_github_logins = []', "allowed_github_logins"),
    ('base_url = "https://x"\nallowed_github_logins = ["rick"]\nport = 0', "port"),
    ('base_url = "https://x"\nallowed_github_logins = ["rick"]\nport = "80"', "port"),
])
def test_invalid_server_settings(body, match):
    write(user_config_path(), f"[server]\n{body}\n")
    with pytest.raises(LiberError, match=match):
        load_server_settings()


def test_invalid_ceiling():
    write(user_config_path(), '[server.ceilings]\noauth = "secret"\n')
    with pytest.raises(LiberError, match="oauth"):
        load_ceilings()


def test_secrets_roundtrip_and_mode():
    s = Secrets("cid", "csecret", new_jwt_signing_key(), new_storage_key())
    path = write_secrets(s)
    assert path == secrets_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_secrets() == s


def test_secrets_missing_points_to_server_init():
    with pytest.raises(LiberError, match="liber server init"):
        load_secrets()


def test_secrets_with_open_permissions_refused():
    path = write_secrets(Secrets("a", "b", "c", "d"))
    os.chmod(path, 0o644)
    with pytest.raises(LiberError, match="chmod 600"):
        load_secrets()


def test_secrets_missing_key():
    path = write_secrets(Secrets("a", "b", "c", "d"))
    path.write_text('github_client_id = "a"\n', encoding="utf-8")
    os.chmod(path, 0o600)
    with pytest.raises(LiberError, match="github_client_secret"):
        load_secrets()


def test_generated_keys_are_distinct_and_valid():
    from cryptography.fernet import Fernet

    assert new_jwt_signing_key() != new_jwt_signing_key()
    assert len(new_jwt_signing_key()) >= 43
    Fernet(new_storage_key())
```

Run: `uv run pytest tests/test_server_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'liber.server'`.

- [ ] **Step 4: Implement settings**

`src/liber/server/__init__.py`:
```python
"""liber's MCP access server."""
```

`src/liber/server/settings.py`:
```python
"""Server settings ([server] in config.toml) and secrets (secrets.toml)."""

import base64
import json
import os
import secrets as pysecrets
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

from liber.config import user_config_path
from liber.errors import LiberError
from liber.vaultconfig import SENSITIVITY_LEVELS

DEFAULT_PORT = 8765
SECRET_KEYS: tuple[str, ...] = ("github_client_id", "github_client_secret", "jwt_signing_key", "storage_encryption_key")
_INIT_HINT = "Run `liber server init` to set up the HTTP server."


@dataclass(frozen=True)
class Ceilings:
    local: str = "private"
    oauth: str = "personal"
    service: str = "personal"


@dataclass(frozen=True)
class ServerSettings:
    base_url: str
    host: str
    port: int
    allowed_github_logins: tuple[str, ...]
    ceilings: Ceilings


@dataclass(frozen=True)
class Secrets:
    github_client_id: str
    github_client_secret: str
    jwt_signing_key: str
    storage_encryption_key: str


def secrets_path() -> Path:
    return user_config_path().parent / "secrets.toml"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "liber"


def new_jwt_signing_key() -> str:
    return base64.urlsafe_b64encode(pysecrets.token_bytes(32)).decode("ascii")


def new_storage_key() -> str:
    return Fernet.generate_key().decode("ascii")


def _read_config() -> dict:
    path = user_config_path()
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise LiberError(f"{path} is not valid TOML: {exc}") from exc


def load_ceilings() -> Ceilings:
    server = _read_config().get("server", {})
    raw = server.get("ceilings", {}) if isinstance(server, dict) else {}
    if not isinstance(raw, dict):
        raise LiberError("[server.ceilings] must be a table")
    values = {}
    for key in ("local", "oauth", "service"):
        if key in raw:
            if raw[key] not in SENSITIVITY_LEVELS:
                raise LiberError(
                    f"[server.ceilings] {key} must be one of {', '.join(SENSITIVITY_LEVELS)}, got {raw[key]!r}"
                )
            values[key] = raw[key]
    return Ceilings(**values)


def load_server_settings() -> ServerSettings:
    server = _read_config().get("server")
    if not isinstance(server, dict):
        raise LiberError(f"no [server] section in {user_config_path()}. {_INIT_HINT}")
    base_url = server.get("base_url")
    if not isinstance(base_url, str) or not base_url.startswith(("https://", "http://")):
        raise LiberError(f"[server] base_url must be a URL like https://liber.example.com. {_INIT_HINT}")
    logins = server.get("allowed_github_logins")
    if not isinstance(logins, list) or not logins or not all(isinstance(x, str) and x for x in logins):
        raise LiberError(f"[server] allowed_github_logins must list at least one GitHub login. {_INIT_HINT}")
    port = server.get("port", DEFAULT_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise LiberError("[server] port must be a number between 1 and 65535")
    host = server.get("host", "127.0.0.1")
    if not isinstance(host, str) or not host:
        raise LiberError("[server] host must be a string such as 127.0.0.1")
    return ServerSettings(base_url.rstrip("/"), host, port, tuple(logins), load_ceilings())


def load_secrets() -> Secrets:
    path = secrets_path()
    if not path.is_file():
        raise LiberError(f"{path} not found. {_INIT_HINT}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise LiberError(f"{path} can be read by other users (mode {mode:o}); fix it with: chmod 600 {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise LiberError(f"{path} is not valid TOML: {exc}") from exc
    missing = [key for key in SECRET_KEYS if not isinstance(data.get(key), str) or not data[key]]
    if missing:
        raise LiberError(f"{path} is missing {', '.join(missing)}. {_INIT_HINT}")
    return Secrets(**{key: data[key] for key in SECRET_KEYS})


def write_secrets(secrets: Secrets) -> Path:
    """Write secrets.toml with mode 0600 (JSON string escaping is valid TOML)."""
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{key} = {json.dumps(getattr(secrets, key))}\n" for key in SECRET_KEYS)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(body)
    os.chmod(path, 0o600)
    return path
```

Run: `uv run pytest -v`
Expected: all tests pass, including the 101 existing ones.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock tests/conftest.py src/liber/server tests/test_server_settings.py
git commit -m "feat(server): dependencies, server settings and secrets

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 2: VaultView visibility, profile, list and read

**Files:**
- Modify: `src/liber/docs.py` (extract `content_paths`)
- Create: `src/liber/server/knowledge.py`
- Test: `tests/test_server_knowledge.py`

**Interfaces:**
- Consumes:
  - `liber.docs.read_vault_file(vault, path) -> VaultFile` (`.text`, `.meta`, `.parse_error`)
  - `liber.check.find_conflicts(vault, patterns) -> list[str]`
  - `liber.vaultconfig.load_vault_config(vault).conflict_patterns`, `sensitivity_rank(level) -> int | None`
  - fixture `vault` (a fresh vault created by `liber init`)
- Produces:
  - `liber.docs.content_paths(vault: Path) -> list[Path]`, sorted by vault-relative posix path; `iter_content_files` now reads these
  - `class NotFound(LiberError)`
  - `@dataclass(frozen=True) Doc(rel: str, text: str, body: str, meta: dict, sensitivity: str, title: str)`
  - `VaultView(vault: Path, ceiling: str)`, which raises `LiberError` for an invalid ceiling, with:
    - `.docs() -> dict[str, Doc]` (visible documents, cached per instance)
    - `.profile(today: date) -> dict` with keys `profile`, `today`, `access_level`, `folders`
    - `.list(folder: str | None = None) -> list[dict]`, each item `{path, title, type, updated, sensitivity, tags}`
    - `.read(path: str) -> str` (full text including frontmatter); raises `NotFound("not found: <path>")`

- [ ] **Step 1: Write failing tests**

`tests/test_server_knowledge.py`:
```python
import os
import re
from datetime import date

import pytest

from helpers import write
from liber.errors import LiberError
from liber.server.knowledge import NotFound, VaultView
from liber.vaultconfig import SENSITIVITY_LEVELS, sensitivity_rank


def fm(type, sens, body, updated="2026-09-30"):
    return f"---\ntype: {type}\nupdated: {updated}\nsensitivity: {sens}\ntags: [t]\n---\n\n{body}\n"


@pytest.fixture
def mixed(vault):
    write(vault / "core" / "pub.md", fm("core", "public", "# Public me\nPUB-MARK"))
    write(vault / "core" / "pers.md", fm("core", "personal", "# Personal me\nPERS-MARK"))
    write(vault / "core" / "priv.md", fm("core", "private", "# Private me\nPRIV-MARK"))
    write(vault / "core" / "nosens.md", "---\ntype: core\nupdated: 2026-09-30\n---\n\nNOSENS-MARK\n")
    write(vault / "core" / "pers (Conflicted copy phone).md", fm("core", "personal", "CONFLICT-MARK"))
    write(vault / "sources" / "documents" / "thesis.pdf.md", "SOURCE-MARK\n")
    write(vault / "sources" / "documents" / "claims-public.md", fm("core", "public", "SOURCE-PUBLIC-MARK"))
    write(vault / "inbox" / "doc.md", "INBOX-MARK\n")
    write(vault / "inbox.md", "# Inbox\n- INBOXNOTE-MARK\n")
    return vault


# rel -> lowest ceiling at which it is visible
EXPECTED = {
    "AGENTS.md": "public",
    "core/pub.md": "public",
    "core/pers.md": "personal",
    "open-questions.md": "personal",
    "core/priv.md": "private",
    "sources/documents/thesis.pdf.md": "private",
    "sources/documents/claims-public.md": "private",  # Review Focus 2: sources are always private
}
NEVER = [
    "core/nosens.md", "core/pers (Conflicted copy phone).md", "inbox/doc.md", "inbox.md",
    "CLAUDE.md", "README.md", "liber.toml", "_templates/person.md",
]


@pytest.mark.parametrize("ceiling", SENSITIVITY_LEVELS)
def test_list_respects_ceiling_both_directions(mixed, ceiling):
    entries = VaultView(mixed, ceiling).list()
    listed = {e["path"] for e in entries}
    for rel, minimum in EXPECTED.items():
        assert (rel in listed) == (sensitivity_rank(ceiling) >= sensitivity_rank(minimum)), rel
    for rel in NEVER:
        assert rel not in listed
    for e in entries:
        assert sensitivity_rank(e["sensitivity"]) <= sensitivity_rank(ceiling)


@pytest.mark.parametrize("ceiling", SENSITIVITY_LEVELS)
def test_read_respects_ceiling_both_directions(mixed, ceiling):
    view = VaultView(mixed, ceiling)
    for rel, minimum in EXPECTED.items():
        if sensitivity_rank(ceiling) >= sensitivity_rank(minimum):
            assert view.read(rel) == (mixed / rel).read_text(encoding="utf-8")
        else:
            with pytest.raises(NotFound, match=re.escape(f"not found: {rel}")):
                view.read(rel)
    for rel in NEVER:
        with pytest.raises(NotFound):
            view.read(rel)


def test_hidden_and_missing_give_the_same_error(mixed):
    view = VaultView(mixed, "personal")
    with pytest.raises(NotFound) as hidden:
        view.read("core/priv.md")
    with pytest.raises(NotFound) as missing:
        view.read("core/nope.md")
    assert str(hidden.value) == "not found: core/priv.md"
    assert str(missing.value) == "not found: core/nope.md"


@pytest.mark.parametrize("path", ["../AGENTS.md", "/etc/passwd", "core/../core/pub.md", "core\\pub.md", "", "core/./pub.md"])
def test_escape_attempts_are_not_found(mixed, path):
    with pytest.raises(NotFound):
        VaultView(mixed, "private").read(path)


def test_symlinks_escaping_the_vault_are_invisible(mixed, tmp_path):
    # Review Focus 1
    outside = tmp_path / "outside"
    secret = write(outside / "secret.md", fm("core", "public", "OUTSIDE-MARK"))
    os.symlink(secret, mixed / "core" / "linked.md")
    os.symlink(outside, mixed / "interests" / "linkdir")
    view = VaultView(mixed, "private")
    paths = {e["path"] for e in view.list()}
    assert not any("linked" in p or "linkdir" in p for p in paths)
    for rel in ("core/linked.md", "interests/linkdir/secret.md"):
        with pytest.raises(NotFound):
            view.read(rel)


def test_unreadable_files_are_skipped(mixed):
    (mixed / "core" / "binary.md").write_bytes(b"\xff\xfe\x00bad")
    assert "core/binary.md" not in {e["path"] for e in VaultView(mixed, "private").list()}


def test_summary_fields(mixed):
    entry = next(e for e in VaultView(mixed, "public").list() if e["path"] == "core/pub.md")
    assert entry == {
        "path": "core/pub.md", "title": "Public me", "type": "core",
        "updated": "2026-09-30", "sensitivity": "public", "tags": ["t"],
    }


def test_title_falls_back_to_file_stem(mixed):
    write(mixed / "interests" / "chess.md", fm("interest", "personal", "no heading here"))
    entry = next(e for e in VaultView(mixed, "personal").list() if e["path"] == "interests/chess.md")
    assert entry["title"] == "chess"


def test_list_folder_filter(mixed):
    assert {e["path"] for e in VaultView(mixed, "private").list("sources/")} == {
        "sources/documents/thesis.pdf.md", "sources/documents/claims-public.md",
    }
    assert VaultView(mixed, "personal").list("sources") == []
    assert {e["path"] for e in VaultView(mixed, "public").list("core")} == {"core/pub.md"}


def test_profile(mixed):
    p = VaultView(mixed, "personal").profile(date(2026, 10, 1))
    assert p["profile"] == (mixed / "AGENTS.md").read_text(encoding="utf-8")
    assert p["today"] == "2026-10-01"
    assert p["access_level"] == "personal"
    assert "core" in p["folders"] and "sources" not in p["folders"]
    assert "sources" in VaultView(mixed, "private").profile(date(2026, 10, 1))["folders"]


def test_invalid_ceiling(vault):
    with pytest.raises(LiberError, match="ceiling"):
        VaultView(vault, "secret")
```

Run: `uv run pytest tests/test_server_knowledge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'liber.server.knowledge'`.

- [ ] **Step 2: Extract `content_paths` in docs.py**

In `src/liber/docs.py`, replace the whole `iter_content_files` function with:
```python
def content_paths(vault: Path) -> list[Path]:
    """Markdown files in content folders: not at the root, not exempt, not hidden. Sorted by relative path."""
    paths = []
    for path in vault.rglob("*.md"):
        parts = path.relative_to(vault).parts
        if len(parts) == 1 or parts[0] in EXEMPT_DIRS:
            continue
        if any(is_hidden_part(p) for p in parts[:-1]):
            continue
        paths.append(path)
    return sorted(paths, key=lambda p: p.relative_to(vault).as_posix())


def iter_content_files(vault: Path) -> list[VaultFile]:
    """Markdown files in content folders: not at the root, not exempt, not hidden."""
    return [read_vault_file(vault, path) for path in content_paths(vault)]
```

- [ ] **Step 3: Implement the visibility part of knowledge.py**

`src/liber/server/knowledge.py`:
```python
"""What an MCP connection may see and do in the vault, at a given sensitivity ceiling."""

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from liber.check import find_conflicts
from liber.docs import content_paths, read_vault_file
from liber.errors import LiberError
from liber.vaultconfig import load_vault_config, sensitivity_rank

log = logging.getLogger("liber.server")

_SOURCE_SUFFIXES = {".md", ".txt"}
_FRONTMATTER = re.compile(r"\A﻿?---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


class NotFound(LiberError):
    """A file that doesn't exist, or that this connection may not see. Deliberately indistinguishable."""


@dataclass(frozen=True)
class Doc:
    rel: str
    text: str
    body: str
    meta: dict
    sensitivity: str
    title: str


def _body(text: str) -> str:
    return _FRONTMATTER.sub("", text, count=1)


def _title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


class VaultView:
    def __init__(self, vault: Path, ceiling: str):
        limit = sensitivity_rank(ceiling)
        if limit is None:
            raise LiberError(f"invalid ceiling {ceiling!r}")
        self.vault = vault
        self.ceiling = ceiling
        self._limit = limit
        self._docs: dict[str, Doc] | None = None

    def _candidates(self) -> list[tuple[Path, str | None]]:
        """(path, forced sensitivity), where None means: use the file's frontmatter."""
        items: list[tuple[Path, str | None]] = [(p, None) for p in content_paths(self.vault)]
        items.append((self.vault / "AGENTS.md", None))
        items.append((self.vault / "open-questions.md", "personal"))
        if self._limit >= sensitivity_rank("private"):
            sources = self.vault / "sources"
            if sources.is_dir():
                items += [
                    (p, "private") for p in sorted(sources.rglob("*")) if p.suffix.lower() in _SOURCE_SUFFIXES
                ]
        return items

    def docs(self) -> dict[str, Doc]:
        if self._docs is not None:
            return self._docs
        root = self.vault.resolve()
        conflicts = set(find_conflicts(self.vault, load_vault_config(self.vault).conflict_patterns))
        docs: dict[str, Doc] = {}
        for path, forced in self._candidates():
            rel = path.relative_to(self.vault).as_posix()
            if rel in conflicts:
                continue
            try:
                if not path.is_file() or not path.resolve().is_relative_to(root):
                    continue
                vf = read_vault_file(self.vault, path)
            except (OSError, UnicodeDecodeError):
                continue
            sensitivity = forced or (None if vf.parse_error else vf.meta.get("sensitivity"))
            rank = sensitivity_rank(sensitivity)
            if rank is None or rank > self._limit:
                continue
            body = _body(vf.text)
            docs[rel] = Doc(rel, vf.text, body, vf.meta, sensitivity, _title(body, path.stem))
        self._docs = docs
        return docs

    def profile(self, today: date) -> dict:
        docs = self.docs()
        agents = docs.get("AGENTS.md")
        return {
            "profile": agents.text if agents else "",
            "today": today.isoformat(),
            "access_level": self.ceiling,
            "folders": sorted({rel.split("/")[0] for rel in docs if "/" in rel}),
        }

    def list(self, folder: str | None = None) -> list[dict]:
        prefix = folder.strip().strip("/") + "/" if folder and folder.strip().strip("/") else None
        return [
            self._summary(doc)
            for rel, doc in sorted(self.docs().items())
            if prefix is None or rel.startswith(prefix)
        ]

    def read(self, path: str) -> str:
        rel = self._normalize(path)
        doc = self.docs().get(rel) if rel else None
        if doc is None:
            raise NotFound(f"not found: {path}")
        return doc.text

    @staticmethod
    def _normalize(path: str) -> str | None:
        candidate = path.strip()
        parts = candidate.split("/")
        if not candidate or "\\" in candidate or candidate.startswith("/") or any(p in ("", ".", "..") for p in parts):
            if ".." in candidate or candidate.startswith("/"):
                log.warning("refused path outside the vault: %r", path)
            return None
        return candidate

    @staticmethod
    def _summary(doc: Doc) -> dict:
        updated = doc.meta.get("updated")
        tags = doc.meta.get("tags")
        return {
            "path": doc.rel,
            "title": doc.title,
            "type": doc.meta.get("type"),
            "updated": str(updated) if updated is not None else None,
            "sensitivity": doc.sensitivity,
            "tags": tags if isinstance(tags, list) else [],
        }
```

Run: `uv run pytest -v`
Expected: all pass, including `tests/test_docs.py` and `tests/test_check.py`, which still go through `iter_content_files`.

- [ ] **Step 4: Commit**

```bash
git add src/liber/docs.py src/liber/server/knowledge.py tests/test_server_knowledge.py
git commit -m "feat(server): VaultView visibility, profile, list and read

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 3: VaultView search and proposals

**Files:**
- Modify: `src/liber/server/knowledge.py`
- Test: `tests/test_server_search_propose.py`

**Interfaces:**
- Consumes: `VaultView`, `Doc` (`.body`, `.title`, `.meta`) from Task 2; `liber.paths.free_name(directory, name) -> str`; `liber.inbox.inbox_documents` (in tests only)
- Produces:
  - Constants `MAX_PROPOSAL_CHARS = 20_000`, `MAX_CONTEXT_CHARS = 2_000`, `MAX_PENDING_PROPOSALS = 50`, `MAX_SEARCH_LIMIT = 50`, `SNIPPET_CHARS = 200`
  - `proposal_label(raw: str | None) -> str`
  - `VaultView.search(query: str, folders: list[str] | None = None, limit: int = 10) -> list[dict]`, each item `{path, title, score, snippets}`
  - `VaultView.propose(text: str, context: str | None, client: str | None, now: datetime) -> dict` returning `{"file": "inbox/<name>", "message": str}`

- [ ] **Step 1: Write failing tests**

`tests/test_server_search_propose.py`:
```python
from datetime import datetime
from pathlib import Path

import pytest

from helpers import write
from liber.errors import LiberError
from liber.inbox import inbox_documents
from liber.server.knowledge import VaultView, proposal_label
from liber.vaultconfig import SENSITIVITY_LEVELS, sensitivity_rank

NOW = datetime(2026, 10, 1, 14, 5, 9)


def fm(type, sens, body, updated="2026-09-30"):
    return f"---\ntype: {type}\nupdated: {updated}\nsensitivity: {sens}\ntags: []\n---\n\n{body}\n"


def paths(results):
    return [r["path"] for r in results]


def test_results_with_all_terms_rank_first(vault):
    write(vault / "interests" / "a.md", fm("interest", "personal", "# Wheel\nwheel wheel wheel"))
    write(vault / "interests" / "b.md", fm("interest", "personal", "# Notes\npottery wheel"))
    assert paths(VaultView(vault, "personal").search("pottery wheel"))[:2] == ["interests/b.md", "interests/a.md"]


def test_title_hits_count_double(vault):
    write(vault / "interests" / "t.md", fm("interest", "personal", "# Sailing\nnothing"))
    write(vault / "interests" / "u.md", fm("interest", "personal", "# Other\nsailing"))
    results = VaultView(vault, "personal").search("sailing")
    assert [(r["path"], r["score"]) for r in results] == [("interests/t.md", 2), ("interests/u.md", 1)]


def test_ties_break_by_newest_updated_then_path(vault):
    write(vault / "interests" / "old.md", fm("interest", "personal", "kayak", updated="2025-01-01"))
    write(vault / "interests" / "new.md", fm("interest", "personal", "kayak", updated="2026-09-01"))
    write(vault / "interests" / "new2.md", fm("interest", "personal", "kayak", updated="2026-09-01"))
    assert paths(VaultView(vault, "personal").search("Kayak")) == [
        "interests/new.md", "interests/new2.md", "interests/old.md",
    ]


def test_frontmatter_is_not_searched(vault):
    write(vault / "interests" / "z.md",
          "---\ntype: interest\nupdated: 2026-09-30\nsensitivity: personal\ntags: [zebraquux]\n---\n\n# Z\nbody\n")
    assert VaultView(vault, "personal").search("zebraquux") == []


def test_snippets_are_centered_and_bounded(vault):
    body = "filler " * 100 + "MARKER one " + "filler " * 100 + "MARKER two " + "filler " * 100
    write(vault / "interests" / "s.md", fm("interest", "personal", body))
    [result] = VaultView(vault, "personal").search("marker")
    assert result["title"] == "s"
    assert len(result["snippets"]) == 2
    for snippet in result["snippets"]:
        assert "MARKER" in snippet
        assert len(snippet) <= 202
    assert result["snippets"][0].startswith("…") and result["snippets"][0].endswith("…")


def test_at_most_three_snippets(vault):
    write(vault / "interests" / "m.md", fm("interest", "personal", ("kiwi " + "pad " * 80) * 6))
    [result] = VaultView(vault, "personal").search("kiwi")
    assert len(result["snippets"]) == 3


@pytest.mark.parametrize("ceiling", SENSITIVITY_LEVELS)
def test_search_respects_ceiling_both_directions(vault, ceiling):
    # Review Focus 3: invisible files never match, so they never leak snippets
    for sens in SENSITIVITY_LEVELS:
        write(vault / "core" / f"{sens}.md", fm("core", sens, f"UNIQUETERM {sens}"))
    write(vault / "core" / "public (Conflicted copy x).md", fm("core", "public", "UNIQUETERM conflict"))
    write(vault / "core" / "nosens.md", "---\ntype: core\nupdated: 2026-09-30\n---\n\nUNIQUETERM nosens\n")
    write(vault / "sources" / "documents" / "d.md", "UNIQUETERM source\n")
    results = VaultView(vault, ceiling).search("uniqueterm")
    expected = {f"core/{s}.md" for s in SENSITIVITY_LEVELS if sensitivity_rank(s) <= sensitivity_rank(ceiling)}
    if ceiling == "private":
        expected.add("sources/documents/d.md")
    assert set(paths(results)) == expected
    joined = " ".join(s for r in results for s in r["snippets"])
    assert "conflict" not in joined and "nosens" not in joined


def test_folder_filter_and_limit(vault):
    for i in range(12):
        write(vault / "interests" / f"k{i:02}.md", fm("interest", "personal", "kayak"))
    write(vault / "goals" / "kayak-goal.md", fm("goal", "personal", "kayak"))
    view = VaultView(vault, "personal")
    assert len(view.search("kayak", limit=5)) == 5
    assert paths(view.search("kayak", folders=["goals/"])) == ["goals/kayak-goal.md"]


def test_empty_query_and_bad_limit(vault):
    view = VaultView(vault, "personal")
    with pytest.raises(LiberError, match="empty"):
        view.search("   ")
    for bad in (0, 51):
        with pytest.raises(LiberError, match="between 1 and 50"):
            view.search("x", limit=bad)


def test_propose_writes_inbox_file(vault):
    out = VaultView(vault, "personal").propose("I left Acme in September.", "chat about jobs", "Claude", NOW)
    assert out["file"] == "inbox/proposal-2026-10-01T14-05-09-claude.md"
    assert "/ingest" in out["message"]
    assert (vault / out["file"]).read_text(encoding="utf-8") == (
        "<!-- liber proposal -->\n"
        "# Proposed update from claude — 2026-10-01 14:05\n\n"
        "**Context:** chat about jobs\n\n"
        "I left Acme in September.\n"
    )


def test_propose_without_context(vault):
    out = VaultView(vault, "personal").propose("fact", None, "x", NOW)
    assert "**Context:** none given" in (vault / out["file"]).read_text(encoding="utf-8")


def test_same_second_collision_gets_suffix(vault):
    view = VaultView(vault, "personal")
    assert view.propose("one", None, "x", NOW)["file"] == "inbox/proposal-2026-10-01T14-05-09-x.md"
    assert view.propose("two", None, "x", NOW)["file"] == "inbox/proposal-2026-10-01T14-05-09-x-2.md"


@pytest.mark.parametrize("raw, label", [
    ("Claude", "claude"),
    ("claude.ai", "claude.ai"),
    ("../../etc/passwd", "etc-passwd"),
    ("Ünïcode Client!", "n-code-client"),
    ("x..y", "x.y"),
    ("a" * 60, "a" * 40),
    ("...", "unknown"),
    ("", "unknown"),
    (None, "unknown"),
])
def test_proposal_label(raw, label):
    assert proposal_label(raw) == label


@pytest.mark.parametrize("client", ["../../etc/passwd", "a/b\\c", "Ünïcode", "", None])
def test_hostile_client_names_stay_in_inbox(vault, client):
    # Review Focus 4
    out = VaultView(vault, "personal").propose("text", None, client, NOW)
    created = vault / out["file"]
    assert created.parent == vault / "inbox" and created.is_file()


def test_propose_limits(vault):
    view = VaultView(vault, "personal")
    with pytest.raises(LiberError, match="empty"):
        view.propose(" \n ", None, "x", NOW)
    with pytest.raises(LiberError, match="20000"):
        view.propose("a" * 20001, None, "x", NOW)
    with pytest.raises(LiberError, match="2000"):
        view.propose("ok", "c" * 2001, "x", NOW)
    assert list((vault / "inbox").glob("proposal-*.md")) == []
    view.propose("a" * 20000, "c" * 2000, "x", NOW)
    assert len(list((vault / "inbox").glob("proposal-*.md"))) == 1


def test_pending_cap(vault):
    for i in range(50):
        write(vault / "inbox" / f"proposal-{i}.md", "x")
    with pytest.raises(LiberError, match="/ingest"):
        VaultView(vault, "personal").propose("more", None, "x", NOW)


def test_proposals_are_invisible_to_tools(vault):
    view = VaultView(vault, "private")
    view.propose("SECRETPROPOSAL", None, "x", NOW)
    assert VaultView(vault, "private").search("secretproposal") == []


def test_proposals_are_ready_for_ingest(vault):
    out = VaultView(vault, "personal").propose("fact", None, "x", NOW)
    assert [(d.name, d.state) for d in inbox_documents(vault)] == [(Path(out["file"]).name, "ready")]
```

Run: `uv run pytest tests/test_server_search_propose.py -v`
Expected: FAIL with `ImportError: cannot import name 'proposal_label'`.

- [ ] **Step 2: Implement search and propose**

In `src/liber/server/knowledge.py`:

1. Change the imports at the top to:
```python
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from liber.check import find_conflicts
from liber.docs import content_paths, read_vault_file
from liber.errors import LiberError
from liber.paths import free_name
from liber.vaultconfig import load_vault_config, sensitivity_rank
```

2. Below `_FRONTMATTER = ...`, add:
```python
MAX_PROPOSAL_CHARS = 20_000
MAX_CONTEXT_CHARS = 2_000
MAX_PENDING_PROPOSALS = 50
MAX_SEARCH_LIMIT = 50
SNIPPET_CHARS = 200
_MAX_SNIPPETS = 3
_LABEL_BAD = re.compile(r"[^a-z0-9.-]+")
```

3. Below `_title`, add:
```python
def proposal_label(raw: str | None) -> str:
    label = _LABEL_BAD.sub("-", (raw or "").lower()).strip("-.")
    label = re.sub(r"\.{2,}", ".", label)[:40].strip("-.")
    return label or "unknown"


def _updated_ordinal(doc: Doc) -> int:
    value = doc.meta.get("updated")
    if isinstance(value, date):
        return value.toordinal()
    try:
        return date.fromisoformat(str(value)).toordinal()
    except ValueError:
        return 0


def _positions(haystack: str, needle: str) -> list[int]:
    found, start = [], haystack.find(needle)
    while start != -1:
        found.append(start)
        start = haystack.find(needle, start + 1)
    return found


def _snippets(text: str, terms: list[str]) -> list[str]:
    flat = " ".join(text.split())
    lowered = flat.lower()
    out: list[str] = []
    covered_until = -1
    for pos in sorted(p for term in terms for p in _positions(lowered, term)):
        if pos < covered_until:
            continue
        start = max(0, pos - SNIPPET_CHARS // 2)
        end = min(len(flat), start + SNIPPET_CHARS)
        out.append(("…" if start > 0 else "") + flat[start:end] + ("…" if end < len(flat) else ""))
        covered_until = end
        if len(out) == _MAX_SNIPPETS:
            break
    return out
```

4. Add these two methods to `VaultView`, after `read`:
```python
    def search(self, query: str, folders: list[str] | None = None, limit: int = 10) -> list[dict]:
        terms = query.lower().split()
        if not terms:
            raise LiberError("query must not be empty")
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise LiberError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
        prefixes = [f.strip().strip("/") + "/" for f in folders if f.strip().strip("/")] if folders else []
        ranked = []
        for rel, doc in self.docs().items():
            if prefixes and not any(rel.startswith(p) for p in prefixes):
                continue
            body, title = doc.body.lower(), doc.title.lower()
            counts = [body.count(t) + title.count(t) for t in terms]
            score = sum(counts)
            if score == 0:
                continue
            missing_any = not all(counts)
            ranked.append((missing_any, -score, -_updated_ordinal(doc), rel, doc, score))
        ranked.sort(key=lambda item: item[:4])
        return [
            {"path": rel, "title": doc.title, "score": score, "snippets": _snippets(doc.body, terms)}
            for _, _, _, rel, doc, score in ranked[:limit]
        ]

    def propose(self, text: str, context: str | None, client: str | None, now: datetime) -> dict:
        body = text.strip()
        if not body:
            raise LiberError("proposal text is empty")
        if len(text) > MAX_PROPOSAL_CHARS:
            raise LiberError(f"proposal text is {len(text)} characters; the limit is {MAX_PROPOSAL_CHARS}")
        if context is not None and len(context) > MAX_CONTEXT_CHARS:
            raise LiberError(f"context is {len(context)} characters; the limit is {MAX_CONTEXT_CHARS}")
        inbox = self.vault / "inbox"
        inbox.mkdir(exist_ok=True)
        pending = len(list(inbox.glob("proposal-*.md")))
        if pending >= MAX_PENDING_PROPOSALS:
            raise LiberError(
                f"{pending} proposals are already waiting for review; ask the owner to run /ingest before proposing more"
            )
        label = proposal_label(client)
        name = free_name(inbox, f"proposal-{now:%Y-%m-%dT%H-%M-%S}-{label}.md")
        context_line = (context or "").strip() or "none given"
        (inbox / name).write_text(
            f"<!-- liber proposal -->\n# Proposed update from {label} — {now:%Y-%m-%d %H:%M}\n\n"
            f"**Context:** {context_line}\n\n{body}\n",
            encoding="utf-8",
        )
        log.info("proposal saved: %s (%d characters)", name, len(body))
        return {
            "file": f"inbox/{name}",
            "message": "Saved for the owner to review with /ingest. Nothing in the knowledge base has changed yet.",
        }
```

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add src/liber/server/knowledge.py tests/test_server_search_propose.py
git commit -m "feat(server): keyword search and inbox proposals

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 4: MCP tools and `liber serve` (stdio)

**Files:**
- Create: `src/liber/server/app.py`
- Modify: `src/liber/cli.py` (add the `serve` command)
- Test: `tests/test_server_app.py`

**Interfaces:**
- Consumes:
  - `VaultView`, `NotFound` (Tasks 2–3)
  - `Ceilings`, `load_ceilings` (Task 1)
  - `liber.config.resolve_vault`, `liber.vaultconfig.load_vault_config`
  - fixture `configured_vault` (sets `LIBER_VAULT`)
- Produces:
  - `Mode = Literal["stdio", "http"]`
  - `@dataclass(frozen=True) Identity(ceiling: str, label: str)`
  - `identity_for(mode: Mode, token: AccessToken | None, ceilings: Ceilings) -> Identity`, which raises `LiberError("unauthenticated request")` for http mode with no token
  - `build_server(mode: Mode, ceilings: Ceilings, *, auth=None) -> FastMCP`, with five tools:
    - `get_user_profile() -> dict`
    - `list_user_knowledge(folder=None) -> {"files": [...]}`
    - `read_user_knowledge(path) -> {"path", "content"}`
    - `search_user_knowledge(query, folders=None, limit=10) -> {"results": [...]}`
    - `propose_update(text, context=None) -> {"file", "message"}`
  - CLI `liber serve` (stdio)
  - Token claims contract, filled in by Task 6:
    - `claims["liber_kind"]` is `"service"` or `"oauth"`
    - service tokens carry `claims["service_name"]`
    - OAuth tokens carry `claims["mcp_client_name"]`

- [ ] **Step 1: Write failing tests**

`tests/test_server_app.py`:
```python
import sys

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken

from helpers import write
from liber.errors import LiberError
from liber.server.app import Identity, build_server, identity_for
from liber.server.settings import Ceilings

pytestmark = pytest.mark.anyio
CEIL = Ceilings(local="private", oauth="personal", service="public")
TOOLS = {"get_user_profile", "list_user_knowledge", "read_user_knowledge", "search_user_knowledge", "propose_update"}


def fm(type, sens, body):
    return f"---\ntype: {type}\nupdated: 2026-09-30\nsensitivity: {sens}\ntags: []\n---\n\n{body}\n"


async def test_tools_are_registered_with_descriptions(configured_vault):
    async with Client(build_server("stdio", CEIL)) as c:
        tools = {t.name: t for t in await c.list_tools()}
    assert set(tools) == TOOLS
    assert "call this first" in tools["get_user_profile"].description.lower()
    assert "not a character" in tools["get_user_profile"].description.lower()
    for tool in tools.values():
        assert "user" in tool.description.lower()


async def test_stdio_mode_uses_local_ceiling(configured_vault):
    write(configured_vault / "core" / "priv.md", fm("core", "private", "PRIVMARK"))
    async with Client(build_server("stdio", CEIL)) as c:
        profile = (await c.call_tool("get_user_profile", {})).data
        files = (await c.call_tool("list_user_knowledge", {})).data["files"]
        read = (await c.call_tool("read_user_knowledge", {"path": "core/priv.md"})).data
        found = (await c.call_tool("search_user_knowledge", {"query": "privmark"})).data["results"]
    assert profile["access_level"] == "private"
    assert "core/priv.md" in {f["path"] for f in files}
    assert read["path"] == "core/priv.md" and "PRIVMARK" in read["content"]
    assert [r["path"] for r in found] == ["core/priv.md"]


async def test_configured_ceiling_flows_into_tools(configured_vault):
    write(configured_vault / "core" / "priv.md", fm("core", "private", "PRIVMARK"))
    async with Client(build_server("stdio", Ceilings(local="personal"))) as c:
        with pytest.raises(ToolError, match="not found: core/priv.md"):
            await c.call_tool("read_user_knowledge", {"path": "core/priv.md"})
        found = (await c.call_tool("search_user_knowledge", {"query": "privmark"})).data["results"]
    assert found == []


async def test_propose_update_tool(configured_vault):
    async with Client(build_server("stdio", CEIL)) as c:
        out = (await c.call_tool("propose_update", {"text": "New job at Initech.", "context": "chat"})).data
    assert out["file"].startswith("inbox/proposal-") and out["file"].endswith("-local.md")
    assert "Initech" in (configured_vault / out["file"]).read_text(encoding="utf-8")


async def test_validation_errors_become_tool_errors(configured_vault):
    async with Client(build_server("stdio", CEIL)) as c:
        with pytest.raises(ToolError, match="empty"):
            await c.call_tool("search_user_knowledge", {"query": "  "})
        with pytest.raises(ToolError, match="between 1 and 50"):
            await c.call_tool("search_user_knowledge", {"query": "x", "limit": 99})
        with pytest.raises(ToolError, match="empty"):
            await c.call_tool("propose_update", {"text": " "})


async def test_missing_vault_is_a_tool_error(monkeypatch, tmp_path):
    monkeypatch.setenv("LIBER_VAULT", str(tmp_path / "nope"))
    async with Client(build_server("stdio", CEIL)) as c:
        with pytest.raises(ToolError, match="liber vault unavailable"):
            await c.call_tool("get_user_profile", {})


def test_identity_for():
    assert identity_for("stdio", None, CEIL) == Identity("private", "local")
    service = AccessToken(token="t", client_id="service:voice", scopes=["user"],
                          claims={"liber_kind": "service", "service_name": "voice"})
    assert identity_for("http", service, CEIL) == Identity("public", "voice")
    oauth = AccessToken(token="t", client_id="c1", scopes=["user"],
                        claims={"liber_kind": "oauth", "mcp_client_name": "Claude", "login": "rick"})
    assert identity_for("http", oauth, CEIL) == Identity("personal", "Claude")
    unnamed = AccessToken(token="t", client_id="c2", scopes=["user"], claims={"liber_kind": "oauth"})
    assert identity_for("http", unnamed, CEIL) == Identity("personal", "oauth")
    with pytest.raises(LiberError, match="unauthenticated"):
        identity_for("http", None, CEIL)


async def test_serve_command_speaks_mcp_over_stdio(configured_vault):
    script = (
        "import os; "
        f"os.environ['LIBER_VAULT'] = {str(configured_vault)!r}; "
        "from liber.cli import app; app(['serve'])"
    )
    async with Client(StdioTransport(command=sys.executable, args=["-c", script])) as c:
        profile = (await c.call_tool("get_user_profile", {})).data
    assert profile["access_level"] == "private"
    assert "About me" in profile["profile"]
```

Run: `uv run pytest tests/test_server_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'liber.server.app'`.

- [ ] **Step 2: Implement app.py**

`src/liber/server/app.py`:
```python
"""The liber MCP server: five thin tools over VaultView."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token

from liber.config import resolve_vault
from liber.errors import LiberError
from liber.server.knowledge import VaultView
from liber.server.settings import Ceilings
from liber.vaultconfig import load_vault_config

Mode = Literal["stdio", "http"]

INSTRUCTIONS = (
    "liber is a knowledge base about the person you are talking with: their work history, skills, "
    "interests, preferences, goals and the people in their life. It is knowledge about the user so you "
    "can help them well. It is not a character for you to play. Call get_user_profile first. Facts carry "
    "dates; treat old ones as possibly stale. Use propose_update to suggest additions; the user reviews them."
)


@dataclass(frozen=True)
class Identity:
    ceiling: str
    label: str


def identity_for(mode: Mode, token: AccessToken | None, ceilings: Ceilings) -> Identity:
    if mode == "stdio":
        return Identity(ceilings.local, "local")
    if token is None:
        raise LiberError("unauthenticated request")
    claims = token.claims or {}
    if claims.get("liber_kind") == "service":
        return Identity(ceilings.service, str(claims.get("service_name") or "service"))
    return Identity(ceilings.oauth, str(claims.get("mcp_client_name") or "oauth"))


@contextmanager
def _tool_errors() -> Iterator[None]:
    try:
        yield
    except LiberError as exc:
        raise ToolError(str(exc)) from exc


def build_server(mode: Mode, ceilings: Ceilings, *, auth=None) -> FastMCP:
    mcp = FastMCP("liber", instructions=INSTRUCTIONS, auth=auth, mask_error_details=True)

    def session() -> tuple[VaultView, Identity]:
        identity = identity_for(mode, get_access_token(), ceilings)
        try:
            vault = resolve_vault()
            load_vault_config(vault)
        except LiberError as exc:
            raise LiberError(f"liber vault unavailable: {exc}") from exc
        return VaultView(vault, identity.ceiling), identity

    @mcp.tool(description=(
        "Call this first. Returns the profile of the user you are talking with: a one-page summary of who "
        "they are, a map of their knowledge base, and rules for using it. This is knowledge about the user, "
        "not a character for you to play."
    ))
    def get_user_profile() -> dict:
        with _tool_errors():
            view, _ = session()
            return view.profile(date.today())

    @mcp.tool(description=(
        "List files in the user's knowledge base with their title, type, last-updated date, sensitivity and "
        "tags. Optionally limit to one folder, such as 'career' or 'people'."
    ))
    def list_user_knowledge(folder: str | None = None) -> dict:
        with _tool_errors():
            view, _ = session()
            return {"files": view.list(folder)}

    @mcp.tool(description=(
        "Read one file from the user's knowledge base by its path, as returned by list_user_knowledge or "
        "search_user_knowledge."
    ))
    def read_user_knowledge(path: str) -> dict:
        with _tool_errors():
            view, _ = session()
            return {"path": path, "content": view.read(path)}

    @mcp.tool(description=(
        "Keyword search across the user's knowledge base. Returns the best-matching files with short "
        "snippets. Use folders to narrow the search, for example ['people']."
    ))
    def search_user_knowledge(query: str, folders: list[str] | None = None, limit: int = 10) -> dict:
        with _tool_errors():
            view, _ = session()
            return {"results": view.search(query, folders, limit)}

    @mcp.tool(description=(
        "Propose an addition or correction to the user's knowledge base, such as a new fact the user just "
        "told you. It is saved for the user to review; nothing changes until they approve it. Include dates "
        "when known, and use context to say where this came from."
    ))
    def propose_update(text: str, context: str | None = None) -> dict:
        with _tool_errors():
            view, identity = session()
            return view.propose(text, context, identity.label, datetime.now())

    return mcp
```

- [ ] **Step 3: Add `liber serve`**

In `src/liber/cli.py`:
- Add `import logging` and `import sys` at the top, with the other stdlib imports.
- Add `from liber.server.settings import load_ceilings` to the imports.
- Append:
```python
def _configure_server_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )


@app.command("serve")
def serve_cmd() -> None:
    """Run the liber MCP server over stdio, for Claude Code, Claude Desktop and other local apps."""
    _configure_server_logging()
    with handle_errors():
        from liber.server.app import build_server

        build_server("stdio", load_ceilings()).run(transport="stdio", show_banner=False)
```

Run: `uv run pytest -v`
Expected: all pass, including `tests/test_skills.py`. The new `serve` command is registered, and the skills don't mention it, so the skills test is unaffected.

- [ ] **Step 4: Commit**

```bash
git add src/liber/server/app.py src/liber/cli.py tests/test_server_app.py
git commit -m "feat(server): MCP tools and liber serve over stdio

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 5: Service tokens and `liber token`

**Files:**
- Create: `src/liber/server/tokens.py`
- Modify: `src/liber/cli.py` (the `token` sub-app)
- Test: `tests/test_server_tokens.py`

**Interfaces:**
- Consumes: `data_dir()` (Task 1), `LiberError`
- Produces:
  - `@dataclass(frozen=True) TokenInfo(name: str, created: str)`
  - `TokenStore(path: Path)` with:
    - `TokenStore.default() -> TokenStore` (`data_dir()/"service-tokens.json"`)
    - `.create(name: str, today: date) -> str` (returns the plaintext token)
    - `.list() -> list[TokenInfo]`
    - `.revoke(name: str) -> None`
    - `.match(token: str) -> str | None` (re-reads the file on every call, compares in constant time)
  - CLI `liber token create|list|revoke`

- [ ] **Step 1: Write failing tests**

`tests/test_server_tokens.py`:
```python
import stat
from datetime import date

import pytest
from typer.testing import CliRunner

from liber.cli import app
from liber.errors import LiberError
from liber.server.tokens import TokenInfo, TokenStore

TODAY = date(2026, 10, 1)


def test_create_list_match_revoke(tmp_path):
    store = TokenStore(tmp_path / "tokens.json")
    token = store.create("voice-backend", TODAY)
    assert len(token) >= 40
    assert store.list() == [TokenInfo("voice-backend", "2026-10-01")]
    assert store.match(token) == "voice-backend"
    assert store.match(token + "x") is None
    assert token not in (tmp_path / "tokens.json").read_text(encoding="utf-8")
    assert stat.S_IMODE((tmp_path / "tokens.json").stat().st_mode) == 0o600
    store.revoke("voice-backend")
    assert store.match(token) is None
    assert store.list() == []


def test_match_sees_changes_made_by_another_store_instance(tmp_path):
    path = tmp_path / "tokens.json"
    reader = TokenStore(path)
    token = TokenStore(path).create("laptop", TODAY)
    assert reader.match(token) == "laptop"
    TokenStore(path).revoke("laptop")
    assert reader.match(token) is None


@pytest.mark.parametrize("name", ["", "Has Space", "UPPER", "-lead", "a" * 41, "x/y"])
def test_bad_names(tmp_path, name):
    with pytest.raises(LiberError, match="lowercase"):
        TokenStore(tmp_path / "t.json").create(name, TODAY)


def test_duplicate_and_unknown(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    store.create("laptop", TODAY)
    with pytest.raises(LiberError, match="already exists"):
        store.create("laptop", TODAY)
    with pytest.raises(LiberError, match="no token named"):
        store.revoke("nope")


def test_empty_store(tmp_path):
    store = TokenStore(tmp_path / "missing.json")
    assert store.list() == [] and store.match("anything") is None


def test_corrupt_store(tmp_path):
    path = tmp_path / "t.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(LiberError, match="unreadable"):
        TokenStore(path).list()


def test_default_location(isolated_env):
    assert TokenStore.default().path == isolated_env / ".local" / "share" / "liber" / "service-tokens.json"


def test_cli_token_lifecycle():
    runner = CliRunner()
    created = runner.invoke(app, ["token", "create", "laptop"])
    assert created.exit_code == 0, created.output
    token = created.stdout.strip().splitlines()[0]
    assert TokenStore.default().match(token) == "laptop"
    listed = runner.invoke(app, ["token", "list"])
    assert listed.exit_code == 0 and "laptop" in listed.output
    assert runner.invoke(app, ["token", "revoke", "laptop"]).exit_code == 0
    again = runner.invoke(app, ["token", "revoke", "laptop"])
    assert again.exit_code == 1 and "error:" in again.output
    assert "no service tokens" in runner.invoke(app, ["token", "list"]).output
```

Run: `uv run pytest tests/test_server_tokens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'liber.server.tokens'`.

- [ ] **Step 2: Implement tokens.py**

`src/liber/server/tokens.py`:
```python
"""Service tokens for the HTTP server: named, random, stored only as sha256 hashes."""

import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from liber.errors import LiberError
from liber.server.settings import data_dir

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


@dataclass(frozen=True)
class TokenInfo:
    name: str
    created: str


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenStore:
    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def default(cls) -> "TokenStore":
        return cls(data_dir() / "service-tokens.json")

    def _load(self) -> list[dict]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LiberError(f"{self.path} is unreadable: {exc}") from exc
        tokens = data.get("tokens") if isinstance(data, dict) else None
        return [t for t in tokens if isinstance(t, dict)] if isinstance(tokens, list) else []

    def _save(self, tokens: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"tokens": tokens}, handle, indent=2)
        os.chmod(self.path, 0o600)

    def create(self, name: str, today: date) -> str:
        if not _NAME.match(name):
            raise LiberError("token names use lowercase letters, digits and dashes (max 40), e.g. voice-backend")
        tokens = self._load()
        if any(t.get("name") == name for t in tokens):
            raise LiberError(f"a token named {name!r} already exists; revoke it first")
        token = secrets.token_urlsafe(32)
        tokens.append({"name": name, "sha256": _digest(token), "created": today.isoformat()})
        self._save(tokens)
        return token

    def list(self) -> list[TokenInfo]:
        return [TokenInfo(str(t.get("name")), str(t.get("created"))) for t in self._load()]

    def revoke(self, name: str) -> None:
        tokens = self._load()
        kept = [t for t in tokens if t.get("name") != name]
        if len(kept) == len(tokens):
            raise LiberError(f"no token named {name!r}")
        self._save(kept)

    def match(self, token: str) -> str | None:
        digest = _digest(token)
        found = None
        for entry in self._load():  # no early exit: constant work per stored token
            if hmac.compare_digest(digest, str(entry.get("sha256", ""))):
                found = str(entry.get("name"))
        return found
```

- [ ] **Step 3: Add the `token` sub-app**

In `src/liber/cli.py`:
- Change `from datetime import datetime` to `from datetime import date, datetime`.
- Add `from liber.server.tokens import TokenStore` to the imports.
- Append:
```python
token_app = typer.Typer(no_args_is_help=True, help="Manage service tokens for the HTTP server (e.g. the voice backend).")
app.add_typer(token_app, name="token")


@token_app.command("create")
def token_create_cmd(name: Annotated[str, typer.Argument(help="A label such as voice-backend")]) -> None:
    """Create a service token. It is printed once; only its hash is stored."""
    with handle_errors():
        token = TokenStore.default().create(name, date.today())
    typer.echo(token)
    typer.echo(f"Service token '{name}' created. This is the only time it is shown; store it safely.", err=True)


@token_app.command("list")
def token_list_cmd() -> None:
    """List service tokens (names and creation dates only)."""
    with handle_errors():
        tokens = TokenStore.default().list()
    if not tokens:
        typer.echo("no service tokens")
    for info in tokens:
        typer.echo(f"{info.name}  created {info.created}")


@token_app.command("revoke")
def token_revoke_cmd(name: Annotated[str, typer.Argument(help="The token's name")]) -> None:
    """Revoke a service token immediately."""
    with handle_errors():
        TokenStore.default().revoke(name)
    typer.echo(f"Revoked service token '{name}'.")
```

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/liber/server/tokens.py src/liber/cli.py tests/test_server_tokens.py
git commit -m "feat(server): service tokens and liber token commands

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 6: GitHub OAuth, the HTTP server, `liber serve --http` and `liber logout-all`

**Files:**
- Create: `src/liber/server/auth.py`
- Modify: `src/liber/cli.py` (the `--http` flag on `serve`, and `logout-all`)
- Test: `tests/test_server_auth.py`

**Interfaces:**
- Consumes:
  - `build_server` (Task 4), `TokenStore` (Task 5)
  - From Task 1: `ServerSettings`, `Secrets`, `data_dir`, `load_server_settings`, `load_secrets`, `write_secrets`, `new_jwt_signing_key`
- Produces:
  - `HTTP_OPTIONS = {"path": "/mcp", "json_response": True, "stateless_http": True}`
  - `PRIVATE_PAGE: str`
  - `oauth_dir() -> Path`
  - `encrypted_file_storage(directory: Path, fernet_key: str) -> AsyncKeyValue`
  - `class AllowListGitHubProvider(GitHubProvider)` with keyword `allowed_logins`
  - `class ServiceTokenVerifier(TokenVerifier)` taking `(store: TokenStore, *, scopes: list[str])`
  - `build_http_server(settings, secrets, *, token_store=None, client_storage=None, provider_cls=AllowListGitHubProvider, http_client=None, require_consent=True) -> FastMCP`
  - `run_http(settings, secrets) -> None`
  - `logout_all() -> None`
  - CLI `liber serve --http`, `liber logout-all`

- [ ] **Step 1: Write failing tests**

`tests/test_server_auth.py`:
```python
import base64
import hashlib
import secrets
import stat
from contextlib import asynccontextmanager
from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import httpx2
import pytest
from key_value.aio.stores.memory import MemoryStore
from typer.testing import CliRunner

from helpers import write
from liber.cli import app
from liber.config import user_config_path
from liber.server import auth as auth_module
from liber.server.auth import (
    HTTP_OPTIONS, PRIVATE_PAGE, AllowListGitHubProvider, build_http_server, logout_all, oauth_dir,
)
from liber.server.settings import (
    Ceilings, Secrets, ServerSettings, load_secrets, new_jwt_signing_key, new_storage_key, secrets_path, write_secrets,
)
from liber.server.tokens import TokenStore

pytestmark = pytest.mark.anyio
BASE = "https://liber.example.com"
H = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
SETTINGS = ServerSettings(BASE, "127.0.0.1", 8765, ("rick",), Ceilings())
REDIRECT = "http://localhost:33418/callback"
GITHUB_USERS = {"gho_rick": {"id": 1, "login": "Rick"}, "gho_eve": {"id": 2, "login": "eve"}}


def github_api(request: httpx2.Request) -> httpx2.Response:
    token = request.headers["authorization"].removeprefix("Bearer ")
    user = GITHUB_USERS.get(token)
    if user is None:
        return httpx2.Response(401, json={"message": "Bad credentials"})
    if request.url.path == "/user":
        return httpx2.Response(200, json=user)
    return httpx2.Response(200, json=[], headers={"x-oauth-scopes": ""})


class FakeUpstream:
    """Stands in for GitHub's token endpoint: code 'rick' -> token 'gho_rick'."""

    async def fetch_token(self, url, **params):
        return {"access_token": f"gho_{params['code']}", "token_type": "bearer"}

    async def aclose(self):
        pass


class MockedGitHubProvider(AllowListGitHubProvider):
    def _create_upstream_oauth_client(self):
        return FakeUpstream()


def fm(type, sens, body):
    return f"---\ntype: {type}\nupdated: 2026-09-30\nsensitivity: {sens}\ntags: []\n---\n\n{body}\n"


def rpc(method, params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return body


def call(name, args=None):
    return rpc("tools/call", {"name": name, "arguments": args or {}})


@pytest.fixture
def store(tmp_path):
    return TokenStore(tmp_path / "tokens.json")


def make_server(store, jwt_key=None, storage=None):
    keys = Secrets("Ov23test", "x" * 40, jwt_key or new_jwt_signing_key(), new_storage_key())
    return build_http_server(
        SETTINGS, keys, token_store=store, client_storage=storage if storage is not None else MemoryStore(),
        provider_cls=MockedGitHubProvider,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(github_api)),
        require_consent=False,
    )


@asynccontextmanager
async def serve(mcp):
    asgi = mcp.http_app(**HTTP_OPTIONS)
    async with asgi.router.lifespan_context(asgi):  # ASGITransport does not run lifespan itself
        async with httpx.AsyncClient(transport=httpx.ASGITransport(asgi), base_url=BASE) as client:
            yield client


async def oauth_until_callback(http, github_code):
    reg = await http.post("/register", json={
        "redirect_uris": [REDIRECT], "client_name": "Claude Test", "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
    assert reg.status_code == 201, reg.text
    client_id = reg.json()["client_id"]
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    r = await http.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "state": "client-state",
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": f"{BASE}/mcp"})
    assert r.status_code == 302, r.text
    txn = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    callback = await http.get("/auth/callback", params={"code": github_code, "state": txn})
    return callback, client_id, verifier


async def oauth_access_token(http):
    callback, client_id, verifier = await oauth_until_callback(http, "rick")
    assert callback.status_code == 302, callback.text
    code = parse_qs(urlparse(callback.headers["location"]).query)["code"][0]
    token = await http.post("/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": client_id, "redirect_uri": REDIRECT,
        "code_verifier": verifier, "resource": f"{BASE}/mcp"})
    assert token.status_code == 200, token.text
    return token.json()["access_token"]


async def test_metadata_and_unauthenticated_challenge(store):
    async with serve(make_server(store)) as http:
        prm = (await http.get("/.well-known/oauth-protected-resource/mcp")).json()
        meta = (await http.get("/.well-known/oauth-authorization-server")).json()
        unauth = await http.post("/mcp", headers=H, json=rpc("tools/list"))
    assert prm["resource"] == f"{BASE}/mcp" and prm["authorization_servers"] == [f"{BASE}/"]
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert "none" in meta["token_endpoint_auth_methods_supported"]
    assert meta["client_id_metadata_document_supported"] is True
    assert unauth.status_code == 401
    assert f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"' in unauth.headers["www-authenticate"]


async def test_service_token_gets_service_ceiling(configured_vault, store):
    write(configured_vault / "core" / "priv.md", fm("core", "private", "PRIVMARK"))
    token = store.create("voice", date(2026, 10, 1))
    headers = {**H, "authorization": f"Bearer {token}"}
    async with serve(make_server(store)) as http:
        listed = await http.post("/mcp", headers=headers, json=rpc("tools/list"))
        profile = await http.post("/mcp", headers=headers, json=call("get_user_profile"))
        hidden = await http.post("/mcp", headers=headers, json=call("read_user_knowledge", {"path": "core/priv.md"}))
        proposal = await http.post("/mcp", headers=headers, json=call("propose_update", {"text": "fact"}))
    assert listed.status_code == 200 and len(listed.json()["result"]["tools"]) == 5
    assert profile.json()["result"]["structuredContent"]["access_level"] == "personal"
    assert hidden.json()["result"]["isError"] is True
    assert hidden.json()["result"]["content"][0]["text"] == "not found: core/priv.md"
    assert proposal.json()["result"]["structuredContent"]["file"].endswith("-voice.md")


async def test_wrong_service_token_is_401(store):
    async with serve(make_server(store)) as http:
        r = await http.post("/mcp", headers={**H, "authorization": "Bearer nope"}, json=rpc("tools/list"))
    assert r.status_code == 401


async def test_service_token_revoked_while_running(configured_vault, store):
    # Review Focus 5
    token = store.create("voice", date(2026, 10, 1))
    headers = {**H, "authorization": f"Bearer {token}"}
    async with serve(make_server(store)) as http:
        assert (await http.post("/mcp", headers=headers, json=rpc("tools/list"))).status_code == 200
        TokenStore(store.path).revoke("voice")
        assert (await http.post("/mcp", headers=headers, json=rpc("tools/list"))).status_code == 401


async def test_allowed_github_login_full_flow(configured_vault, store):
    async with serve(make_server(store)) as http:
        access = await oauth_access_token(http)
        headers = {**H, "authorization": f"Bearer {access}"}
        profile = await http.post("/mcp", headers=headers, json=call("get_user_profile"))
        proposal = await http.post("/mcp", headers=headers, json=call("propose_update", {"text": "fact"}))
    assert profile.status_code == 200
    assert profile.json()["result"]["structuredContent"]["access_level"] == "personal"
    assert proposal.json()["result"]["structuredContent"]["file"].endswith("-claude-test.md")


async def test_disallowed_github_login_never_gets_a_code(store):
    async with serve(make_server(store)) as http:
        callback, _, _ = await oauth_until_callback(http, "eve")
    assert callback.status_code == 403
    assert "location" not in callback.headers
    assert callback.text == PRIVATE_PAGE


async def test_rotated_signing_key_invalidates_oauth_tokens(store):
    storage = MemoryStore()
    async with serve(make_server(store, jwt_key=new_jwt_signing_key(), storage=storage)) as http:
        access = await oauth_access_token(http)
    async with serve(make_server(store, jwt_key=new_jwt_signing_key(), storage=storage)) as http:
        r = await http.post("/mcp", headers={**H, "authorization": f"Bearer {access}"}, json=rpc("tools/list"))
    assert r.status_code == 401


def test_logout_all_rotates_key_and_clears_storage():
    original = Secrets("cid", "csecret", new_jwt_signing_key(), new_storage_key())
    write_secrets(original)
    write(oauth_dir() / "clients" / "x", "state")
    logout_all()
    updated = load_secrets()
    assert updated.jwt_signing_key != original.jwt_signing_key
    assert (updated.github_client_id, updated.storage_encryption_key) == ("cid", original.storage_encryption_key)
    assert stat.S_IMODE(secrets_path().stat().st_mode) == 0o600
    assert not oauth_dir().exists()


def test_cli_serve_http_wires_settings_and_secrets(monkeypatch):
    write(user_config_path(), '[server]\nbase_url = "https://liber.example.com"\nallowed_github_logins = ["rick"]\n')
    write_secrets(Secrets("cid", "csecret", new_jwt_signing_key(), new_storage_key()))
    seen = {}
    monkeypatch.setattr(auth_module, "run_http", lambda settings, keys: seen.update(settings=settings, keys=keys))
    result = CliRunner().invoke(app, ["serve", "--http"])
    assert result.exit_code == 0, result.output
    assert seen["settings"].base_url == "https://liber.example.com"
    assert seen["keys"].github_client_id == "cid"


def test_cli_serve_http_without_setup():
    result = CliRunner().invoke(app, ["serve", "--http"])
    assert result.exit_code == 1
    assert "liber server init" in result.output


def test_cli_logout_all():
    write_secrets(Secrets("cid", "csecret", new_jwt_signing_key(), new_storage_key()))
    result = CliRunner().invoke(app, ["logout-all"])
    assert result.exit_code == 0, result.output
    assert "systemctl --user restart liber-mcp" in result.output
```

Run: `uv run pytest tests/test_server_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'liber.server.auth'`.

- [ ] **Step 2: Implement auth.py**

`src/liber/server/auth.py`:
```python
"""Remote access on fastmcp 4.0.10: GitHub OAuth (allow-listed at the callback) plus service tokens.

The allow-list override uses private fastmcp internals (_handle_idp_callback, _code_store,
_token_validator); fastmcp is pinned to exactly 4.0.10 and tests/test_server_auth.py guards it.
"""

import logging
import shutil
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, MultiAuth, TokenVerifier
from fastmcp.server.auth.providers.github import GitHubProvider
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from liber.errors import LiberError
from liber.server.app import build_server
from liber.server.settings import (
    Secrets, ServerSettings, data_dir, load_secrets, new_jwt_signing_key, write_secrets,
)
from liber.server.tokens import TokenStore

log = logging.getLogger("liber.auth")

HTTP_OPTIONS = {"path": "/mcp", "json_response": True, "stateless_http": True}
GITHUB_CACHE_SECONDS = 300
PRIVATE_PAGE = "<!doctype html><title>liber</title><p>This liber server is private.</p>"


def oauth_dir() -> Path:
    return data_dir() / "oauth"


def encrypted_file_storage(directory: Path, fernet_key: str) -> AsyncKeyValue:
    """Persistent, Fernet-encrypted store for OAuth clients and tokens."""
    directory.mkdir(parents=True, exist_ok=True)
    return FernetEncryptionWrapper(
        key_value=FileTreeStore(
            data_directory=directory,
            key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(directory),
            collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(directory),
        ),
        fernet=Fernet(fernet_key),
        raise_on_decryption_error=False,  # unreadable state is treated as missing; clients re-register
    )


class AllowListGitHubProvider(GitHubProvider):
    """GitHub OAuth proxy that only ever issues tokens to allow-listed GitHub logins."""

    def __init__(self, *, allowed_logins: list[str], **kwargs):
        super().__init__(**kwargs)
        self._allowed_logins = {login.lower() for login in allowed_logins}

    def _login_allowed(self, login: str | None) -> bool:
        return bool(login) and login.lower() in self._allowed_logins

    async def _handle_idp_callback(self, request: Request) -> HTMLResponse | RedirectResponse:
        # super() exchanges GitHub's code, stores a one-time liber authorization code, and returns a
        # redirect carrying it. Inspect that code before the browser sees it; on refusal, delete it.
        response = await super()._handle_idp_callback(request)
        if not isinstance(response, RedirectResponse):
            return response
        code = parse_qs(urlparse(response.headers["location"]).query).get("code", [None])[0]
        if code is None:
            return response  # an ?error=... redirect back to the client
        client_code = await self._code_store.get(key=code)
        login = None
        if client_code is not None:
            upstream = await self._token_validator.verify_token(client_code.idp_tokens["access_token"])
            login = upstream.claims.get("login") if upstream else None
        if not self._login_allowed(login):
            await self._code_store.delete(key=code)
            log.warning("refused GitHub login %r at the OAuth callback", login)
            return HTMLResponse(PRIVATE_PAGE, status_code=403)
        log.info("GitHub login %r accepted", login)
        return response

    async def load_access_token(self, token: str) -> AccessToken | None:  # type: ignore[override]
        validated = await super().load_access_token(token)
        if validated is None:
            return None
        if not self._login_allowed(validated.claims.get("login")):
            return None  # the allow-list changed after this token was issued
        payload = self.jwt_issuer.verify_token(token)
        client = await self.get_client(payload["client_id"])
        return validated.model_copy(update={
            "client_id": payload["client_id"],
            "claims": {
                **validated.claims,
                "liber_kind": "oauth",
                "mcp_client_name": getattr(client, "client_name", None),
            },
        })


class ServiceTokenVerifier(TokenVerifier):
    """Static service tokens. Re-reads the store on every request so revocation is immediate."""

    def __init__(self, store: TokenStore, *, scopes: list[str]):
        super().__init__()
        self._store = store
        self._scopes = scopes

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            name = self._store.match(token)
        except LiberError as exc:
            log.error("service token store unreadable: %s", exc)
            return None
        if name is None:
            return None
        return AccessToken(
            token=token, client_id=f"service:{name}", scopes=self._scopes,
            claims={"liber_kind": "service", "service_name": name},
        )


def build_http_server(
    settings: ServerSettings,
    secrets: Secrets,
    *,
    token_store: TokenStore | None = None,
    client_storage: AsyncKeyValue | None = None,
    provider_cls: type[AllowListGitHubProvider] = AllowListGitHubProvider,
    http_client=None,
    require_consent: bool = True,
) -> FastMCP:
    provider = provider_cls(
        allowed_logins=list(settings.allowed_github_logins),
        client_id=secrets.github_client_id,
        client_secret=secrets.github_client_secret,
        base_url=settings.base_url,
        jwt_signing_key=secrets.jwt_signing_key.encode("ascii"),  # bytes: used as-is, no PBKDF2
        client_storage=client_storage
        if client_storage is not None
        else encrypted_file_storage(oauth_dir(), secrets.storage_encryption_key),
        cache_ttl_seconds=GITHUB_CACHE_SECONDS,
        require_authorization_consent=require_consent,
        http_client=http_client,
    )
    verifier = ServiceTokenVerifier(token_store or TokenStore.default(), scopes=list(provider.required_scopes))
    return build_server("http", settings.ceilings, auth=MultiAuth(server=provider, verifiers=[verifier]))


def run_http(settings: ServerSettings, secrets: Secrets) -> None:
    mcp = build_http_server(settings, secrets)
    log.info("serving %s/mcp on http://%s:%d", settings.base_url, settings.host, settings.port)
    mcp.run(transport="http", host=settings.host, port=settings.port, show_banner=False, **HTTP_OPTIONS)


def logout_all() -> None:
    """Invalidate every OAuth session: new signing key, and forget stored clients and tokens."""
    current = load_secrets()
    write_secrets(replace(current, jwt_signing_key=new_jwt_signing_key()))
    shutil.rmtree(oauth_dir(), ignore_errors=True)
```

- [ ] **Step 3: Add `--http` to `serve`, and `logout-all`**

In `src/liber/cli.py`:
- Change the `liber.server.settings` import to `from liber.server.settings import load_ceilings, load_secrets, load_server_settings`.
- Replace the whole `serve_cmd` function with:
```python
@app.command("serve")
def serve_cmd(
    http: Annotated[bool, typer.Option("--http", help="Serve over HTTP with GitHub login, for the tunnel")] = False,
) -> None:
    """Run the liber MCP server: stdio for local apps (default), or HTTP for cloud apps."""
    _configure_server_logging()
    with handle_errors():
        if http:
            settings = load_server_settings()
            secrets = load_secrets()
            from liber.server import auth

            auth.run_http(settings, secrets)
        else:
            from liber.server.app import build_server

            build_server("stdio", load_ceilings()).run(transport="stdio", show_banner=False)
```
- Append:
```python
@app.command("logout-all")
def logout_all_cmd() -> None:
    """Log out every connected cloud app (they will need to sign in with GitHub again)."""
    with handle_errors():
        from liber.server import auth

        auth.logout_all()
    typer.echo("All OAuth sessions revoked. Restart the server to apply: systemctl --user restart liber-mcp")
```

Run: `uv run pytest -v`
Expected: all pass. If `test_allowed_github_login_full_flow` fails inside fastmcp, compare against the spike notes in this plan's header (the flow, the trailing-slash issuer, MemoryStore). Do not change `fastmcp`'s version.

- [ ] **Step 4: Commit**

```bash
git add src/liber/server/auth.py src/liber/cli.py tests/test_server_auth.py
git commit -m "feat(server): GitHub OAuth with allow-list, service tokens, serve --http, logout-all

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 7: `liber server init`

**Files:**
- Create: `src/liber/server/setup.py`
- Modify: `src/liber/cli.py` (the `server` sub-app with the `init` command)
- Test: `tests/test_server_setup.py`

**Interfaces:**
- Consumes:
  - From Task 1: `Secrets`, `write_secrets`, `secrets_path`, `new_jwt_signing_key`, `new_storage_key`, `DEFAULT_PORT`
  - `liber.config.user_config_path`, `write_user_config` (in tests)
- Produces:
  - `@dataclass(frozen=True) InitResult(written: list[Path], kept: list[Path], next_steps: list[str])`
  - `init_server(*, github_login, github_client_id, github_client_secret, base_url, tunnel_credentials: Path, liber_cmd: str, cloudflared_cmd: str, port: int = DEFAULT_PORT, force: bool = False) -> InitResult`
  - `find_tunnel_credentials() -> Path`
  - `require_command(name: str) -> str`
  - `systemd_user_dir() -> Path`, `cloudflared_dir() -> Path`
  - CLI `liber server init [--force] [--tunnel-credentials PATH]`

**Ruling (plan-level):** the spec's `[server]` defaults hard-code `kineticrick`. Because this repo is public, `init` instead prompts for the allowed GitHub login, with no default. The only default is the base URL, `https://liber.kineticrick.com`, as the spec states.

- [ ] **Step 1: Write failing tests**

`tests/test_server_setup.py`:
```python
import json
import stat

import pytest
from typer.testing import CliRunner

from helpers import write
from liber.cli import app
from liber.config import user_config_path, write_user_config
from liber.errors import LiberError
from liber.server import setup as server_setup
from liber.server.settings import Ceilings, load_secrets, load_server_settings, secrets_path
from liber.server.setup import cloudflared_dir, find_tunnel_credentials, init_server, systemd_user_dir


@pytest.fixture
def creds(tmp_path):
    return write(tmp_path / "cf" / "6f1c.json", json.dumps({"TunnelID": "6f1c-uuid", "AccountTag": "x"}))


@pytest.fixture
def with_config(vault):
    write_user_config(vault)
    return vault


def run_init(creds, **overrides):
    args = dict(
        github_login="rick", github_client_id="Ov23id", github_client_secret="sec",
        base_url="https://liber.example.com/", tunnel_credentials=creds,
        liber_cmd="/home/u/.local/bin/liber", cloudflared_cmd="/usr/bin/cloudflared",
    )
    args.update(overrides)
    return init_server(**args)


def test_init_writes_everything(with_config, creds):
    result = run_init(creds)
    keys = load_secrets()
    assert (keys.github_client_id, keys.github_client_secret) == ("Ov23id", "sec")
    assert stat.S_IMODE(secrets_path().stat().st_mode) == 0o600
    settings = load_server_settings()
    assert settings.base_url == "https://liber.example.com"
    assert settings.allowed_github_logins == ("rick",)
    assert settings.port == 8765 and settings.ceilings == Ceilings()
    assert f'vault = "{with_config.resolve()}"' in user_config_path().read_text()

    mcp_unit = (systemd_user_dir() / "liber-mcp.service").read_text()
    assert "ExecStart=/home/u/.local/bin/liber serve --http" in mcp_unit
    assert "Environment=FASTMCP_CHECK_FOR_UPDATES=off" in mcp_unit
    tunnel_unit = (systemd_user_dir() / "cloudflared-liber.service").read_text()
    config_path = cloudflared_dir() / "liber.yml"
    assert f"ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate --config {config_path} run" in tunnel_unit
    tunnel_config = config_path.read_text()
    for expected in ("tunnel: 6f1c-uuid", f"credentials-file: {creds}", "hostname: liber.example.com",
                     "service: http://127.0.0.1:8765", "service: http_status:404"):
        assert expected in tunnel_config

    assert set(result.written) == {secrets_path(), user_config_path(), systemd_user_dir() / "liber-mcp.service",
                                   systemd_user_dir() / "cloudflared-liber.service", config_path}
    assert "systemctl --user enable --now liber-mcp.service cloudflared-liber.service" in result.next_steps
    assert any("loginctl enable-linger" in step for step in result.next_steps)


def test_refuses_existing_secrets_without_force(with_config, creds):
    run_init(creds)
    first_key = load_secrets().jwt_signing_key
    with pytest.raises(LiberError, match="--force"):
        run_init(creds)
    run_init(creds, force=True, github_client_id="Ov23new")
    assert load_secrets().github_client_id == "Ov23new"
    assert load_secrets().jwt_signing_key != first_key


def test_existing_server_section_and_files_are_kept(with_config, creds):
    with user_config_path().open("a", encoding="utf-8") as handle:
        handle.write('\n[server]\nbase_url = "https://other.example.com"\nallowed_github_logins = ["someone"]\n')
    write(systemd_user_dir() / "liber-mcp.service", "custom")
    result = run_init(creds)
    assert load_server_settings().base_url == "https://other.example.com"
    assert (systemd_user_dir() / "liber-mcp.service").read_text() == "custom"
    assert user_config_path() in result.kept and systemd_user_dir() / "liber-mcp.service" in result.kept


def test_requires_https(with_config, creds):
    with pytest.raises(LiberError, match="https://"):
        run_init(creds, base_url="http://liber.example.com")


def test_requires_existing_config(creds):
    with pytest.raises(LiberError, match="liber init"):
        run_init(creds)


def test_find_tunnel_credentials(isolated_env):
    with pytest.raises(LiberError, match="cloudflared tunnel create liber"):
        find_tunnel_credentials()
    write(isolated_env / ".cloudflared" / "a.json", "{}")
    assert find_tunnel_credentials() == isolated_env / ".cloudflared" / "a.json"
    write(isolated_env / ".cloudflared" / "b.json", "{}")
    with pytest.raises(LiberError, match="--tunnel-credentials"):
        find_tunnel_credentials()


def test_cli_server_init(with_config, creds, monkeypatch):
    monkeypatch.setattr(server_setup, "require_command", lambda name: f"/usr/bin/{name}")
    result = CliRunner().invoke(
        app, ["server", "init", "--tunnel-credentials", str(creds)],
        input="https://liber.example.com\nrick\nOv23id\ntopsecretvalue\n",
    )
    assert result.exit_code == 0, result.output
    assert load_server_settings().allowed_github_logins == ("rick",)
    assert load_secrets().github_client_secret == "topsecretvalue"
    assert "systemctl --user enable --now" in result.output
    assert "topsecretvalue" not in result.output  # hidden prompt: never echoed
```

Run: `uv run pytest tests/test_server_setup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'liber.server.setup'`.

- [ ] **Step 2: Implement setup.py**

`src/liber/server/setup.py`:
```python
"""`liber server init`: write secrets, [server] config, systemd user units and the cloudflared config."""

import json
import os
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from liber.config import user_config_path
from liber.errors import LiberError
from liber.server.settings import (
    DEFAULT_PORT, Secrets, new_jwt_signing_key, new_storage_key, secrets_path, write_secrets,
)

MCP_UNIT = "liber-mcp.service"
TUNNEL_UNIT = "cloudflared-liber.service"


@dataclass(frozen=True)
class InitResult:
    written: list[Path]
    kept: list[Path]
    next_steps: list[str]


def systemd_user_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "systemd" / "user"


def cloudflared_dir() -> Path:
    return Path.home() / ".cloudflared"


def require_command(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise LiberError(f"`{name}` was not found on PATH; install it first (see the README)")
    return path


def find_tunnel_credentials() -> Path:
    found = sorted(cloudflared_dir().glob("*.json"))
    if not found:
        raise LiberError(
            f"no tunnel credentials in {cloudflared_dir()}; run `cloudflared tunnel create liber` first"
        )
    if len(found) > 1:
        names = ", ".join(p.name for p in found)
        raise LiberError(f"several tunnel credential files found ({names}); choose one with --tunnel-credentials")
    return found[0]


def _tunnel_id(credentials: Path) -> str:
    try:
        return str(json.loads(credentials.read_text(encoding="utf-8"))["TunnelID"])
    except (OSError, ValueError, KeyError):
        return credentials.stem


def _server_block(base_url: str, port: int, github_login: str) -> str:
    return (
        "\n[server]\n"
        f"base_url = {json.dumps(base_url)}\n"
        'host = "127.0.0.1"\n'
        f"port = {port}\n"
        f"allowed_github_logins = [{json.dumps(github_login)}]\n"
        "\n[server.ceilings]\n"
        'local = "private"\n'
        'oauth = "personal"\n'
        'service = "personal"\n'
    )


def _mcp_unit(liber_cmd: str) -> str:
    return (
        "[Unit]\nDescription=liber MCP server (HTTP)\nAfter=network-online.target\n\n"
        f"[Service]\nExecStart={liber_cmd} serve --http\nRestart=on-failure\nRestartSec=5\n"
        "Environment=FASTMCP_ENABLE_RICH_LOGGING=false\nEnvironment=FASTMCP_CHECK_FOR_UPDATES=off\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def _tunnel_unit(cloudflared_cmd: str, config: Path) -> str:
    return (
        "[Unit]\nDescription=Cloudflare Tunnel for liber\nAfter=network-online.target\nWants=network-online.target\n\n"
        f"[Service]\nExecStart={cloudflared_cmd} tunnel --no-autoupdate --config {config} run\n"
        "Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n"
    )


def _tunnel_config(tunnel_id: str, credentials: Path, hostname: str, port: int) -> str:
    return (
        f"tunnel: {tunnel_id}\n"
        f"credentials-file: {credentials}\n"
        "ingress:\n"
        f"  - hostname: {hostname}\n"
        f"    service: http://127.0.0.1:{port}\n"
        "  - service: http_status:404\n"
    )


def _write_if_new(path: Path, content: str, force: bool, written: list[Path], kept: list[Path]) -> None:
    if path.exists() and not force:
        kept.append(path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    written.append(path)


def init_server(
    *,
    github_login: str,
    github_client_id: str,
    github_client_secret: str,
    base_url: str,
    tunnel_credentials: Path,
    liber_cmd: str,
    cloudflared_cmd: str,
    port: int = DEFAULT_PORT,
    force: bool = False,
) -> InitResult:
    base_url = base_url.strip().rstrip("/")
    hostname = urlparse(base_url).hostname
    if not base_url.startswith("https://") or not hostname:
        raise LiberError("the public URL must start with https://, e.g. https://liber.example.com")
    if not github_login.strip() or not github_client_id.strip() or not github_client_secret.strip():
        raise LiberError("the GitHub login, client ID and client secret are all required")
    config = user_config_path()
    if not config.is_file():
        raise LiberError(f"{config} not found; run `liber init <path>` to create your vault first")
    if secrets_path().exists() and not force:
        raise LiberError(
            f"{secrets_path()} already exists; rerun with --force to replace it (this logs out every connected app)"
        )

    written: list[Path] = []
    kept: list[Path] = []
    write_secrets(Secrets(github_client_id.strip(), github_client_secret.strip(), new_jwt_signing_key(), new_storage_key()))
    written.append(secrets_path())

    existing = tomllib.loads(config.read_text(encoding="utf-8"))
    if "server" in existing:
        kept.append(config)
    else:
        text = config.read_text(encoding="utf-8")
        config.write_text(text + _server_block(base_url, port, github_login.strip()), encoding="utf-8")
        written.append(config)

    tunnel_config = cloudflared_dir() / "liber.yml"
    _write_if_new(systemd_user_dir() / MCP_UNIT, _mcp_unit(liber_cmd), force, written, kept)
    _write_if_new(systemd_user_dir() / TUNNEL_UNIT, _tunnel_unit(cloudflared_cmd, tunnel_config), force, written, kept)
    _write_if_new(
        tunnel_config, _tunnel_config(_tunnel_id(tunnel_credentials), tunnel_credentials, hostname, port),
        force, written, kept,
    )
    next_steps = [
        "systemctl --user daemon-reload",
        f"systemctl --user enable --now {MCP_UNIT} {TUNNEL_UNIT}",
        "loginctl enable-linger $USER",
        "liber server doctor",
    ]
    return InitResult(written, kept, next_steps)
```

- [ ] **Step 3: Add the `server` sub-app with `init`**

In `src/liber/cli.py`, append:
```python
server_app = typer.Typer(no_args_is_help=True, help="Set up and check the HTTP server for cloud apps.")
app.add_typer(server_app, name="server")


@server_app.command("init")
def server_init_cmd(
    force: Annotated[bool, typer.Option("--force", help="Replace existing secrets and generated files")] = False,
    tunnel_credentials: Annotated[
        Optional[Path], typer.Option("--tunnel-credentials", help="cloudflared tunnel credentials JSON")
    ] = None,
) -> None:
    """Write secrets, the [server] config, systemd user units and the cloudflared config."""
    from liber.server import setup as server_setup

    with handle_errors():
        credentials = tunnel_credentials.expanduser() if tunnel_credentials else server_setup.find_tunnel_credentials()
        liber_cmd = server_setup.require_command("liber")
        cloudflared_cmd = server_setup.require_command("cloudflared")
        base_url = typer.prompt("Public URL", default="https://liber.kineticrick.com")
        github_login = typer.prompt("GitHub login allowed to connect")
        client_id = typer.prompt("GitHub OAuth app client ID")
        client_secret = typer.prompt("GitHub OAuth app client secret", hide_input=True)
        result = server_setup.init_server(
            github_login=github_login, github_client_id=client_id, github_client_secret=client_secret,
            base_url=base_url, tunnel_credentials=credentials, liber_cmd=liber_cmd,
            cloudflared_cmd=cloudflared_cmd, force=force,
        )
    for path in result.written:
        typer.echo(f"wrote {path}")
    for path in result.kept:
        typer.echo(f"kept existing {path} (use --force to regenerate)")
    typer.echo("\nNext, run:")
    for step in result.next_steps:
        typer.echo(f"  {step}")
```

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/liber/server/setup.py src/liber/cli.py tests/test_server_setup.py
git commit -m "feat(server): liber server init writes secrets, config, units and tunnel config

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 8: `liber server doctor`

**Files:**
- Create: `src/liber/server/doctor.py`
- Modify: `src/liber/cli.py` (the `doctor` command on `server_app`)
- Test: `tests/test_server_doctor.py`

**Interfaces:**
- Consumes:
  - From Task 1: `load_server_settings`, `load_secrets`, `write_secrets`, `Secrets`, `new_jwt_signing_key`, `new_storage_key`
  - `liber.config.resolve_vault`, `liber.vaultconfig.load_vault_config`
  - `server_app` (Task 7)
- Produces:
  - `@dataclass(frozen=True) Check(name: str, ok: bool, detail: str, warning: bool = False)`
  - `run_doctor(*, http: httpx.Client, systemctl: Callable[[str], bool]) -> list[Check]`
  - `default_http_client() -> httpx.Client`
  - `systemctl_active(unit: str) -> bool`
  - Check names, in order: `server config`, `secrets`, `vault`, `local server`, `protected resource metadata`, `authorization server metadata`, `auth challenge`, `service liber-mcp`, `service cloudflared-liber`
  - CLI `liber server doctor`

- [ ] **Step 1: Write failing tests**

`tests/test_server_doctor.py`:
```python
import os

import httpx
import pytest
from typer.testing import CliRunner

from helpers import write
from liber.cli import app
from liber.config import user_config_path, write_user_config
from liber.server import doctor as doctor_module
from liber.server.doctor import run_doctor
from liber.server.settings import Secrets, new_jwt_signing_key, new_storage_key, secrets_path, write_secrets

BASE = "https://liber.example.com"
GOOD_AS = {
    "issuer": f"{BASE}/",
    "registration_endpoint": f"{BASE}/register",
    "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
    "code_challenge_methods_supported": ["S256"],
    "client_id_metadata_document_supported": True,
    "authorization_response_iss_parameter_supported": True,
}
CHALLENGE = f'Bearer scope="user", resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"'
NAMES = ["server config", "secrets", "vault", "local server", "protected resource metadata",
         "authorization server metadata", "auth challenge", "service liber-mcp", "service cloudflared-liber"]


def handler_with(prm=None, as_meta=None, challenge_status=401, challenge_header=CHALLENGE, local_up=True):
    prm = prm if prm is not None else {"resource": f"{BASE}/mcp", "authorization_servers": [f"{BASE}/"]}
    as_meta = as_meta if as_meta is not None else GOOD_AS

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "127.0.0.1":
            if not local_up:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(401, headers={"www-authenticate": CHALLENGE})
        if request.url.path == "/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(200, json=prm)
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=as_meta)
        if request.url.path == "/mcp" and request.method == "POST":
            headers = {"www-authenticate": challenge_header} if challenge_header else {}
            return httpx.Response(challenge_status, headers=headers)
        return httpx.Response(404)

    return handler


def doctor(handler, active=True):
    return run_doctor(http=httpx.Client(transport=httpx.MockTransport(handler)), systemctl=lambda unit: active)


@pytest.fixture
def configured(vault):
    write_user_config(vault)
    with user_config_path().open("a", encoding="utf-8") as handle:
        handle.write(f'\n[server]\nbase_url = "{BASE}"\nallowed_github_logins = ["rick"]\n')
    write_secrets(Secrets("cid", "sec", new_jwt_signing_key(), new_storage_key()))
    return vault


def by_name(checks):
    return {c.name: c for c in checks}


def test_all_good(configured):
    checks = doctor(handler_with())
    assert [c.name for c in checks] == NAMES
    assert all(c.ok for c in checks), [str(c) for c in checks if not c.ok]


@pytest.mark.parametrize("kwargs, failing, detail", [
    ({"prm": {"resource": f"{BASE}/", "authorization_servers": [f"{BASE}/"]}}, "protected resource metadata", "resource"),
    ({"prm": {"resource": f"{BASE}/mcp", "authorization_servers": [f"{BASE}/", "https://x/"]}},
     "protected resource metadata", "authorization_servers"),
    ({"as_meta": {**GOOD_AS, "code_challenge_methods_supported": ["plain"]}}, "authorization server metadata", "S256"),
    ({"as_meta": {**GOOD_AS, "token_endpoint_auth_methods_supported": ["client_secret_post"]}},
     "authorization server metadata", "none"),
    ({"as_meta": {**GOOD_AS, "client_id_metadata_document_supported": False}},
     "authorization server metadata", "client_id_metadata_document"),
    ({"as_meta": {**GOOD_AS, "issuer": "https://elsewhere.example.com/"}}, "authorization server metadata", "issuer"),
    ({"challenge_header": 'Bearer scope="user"'}, "auth challenge", "resource_metadata"),
    ({"challenge_status": 200, "challenge_header": None}, "auth challenge", "401"),
    ({"local_up": False}, "local server", "liber-mcp"),
])
def test_each_failure_is_reported(configured, kwargs, failing, detail):
    checks = by_name(doctor(handler_with(**kwargs)))
    assert checks[failing].ok is False
    assert detail in checks[failing].detail
    assert all(c.ok for name, c in checks.items() if name != failing)


def test_issuer_trailing_slash_is_normalised(configured):
    checks = by_name(doctor(handler_with(as_meta={**GOOD_AS, "issuer": BASE})))
    assert checks["authorization server metadata"].ok


def test_missing_settings_skips_network_checks(vault):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(500)

    checks = by_name(doctor(handler))
    assert checks["server config"].ok is False and "liber server init" in checks["server config"].detail
    assert "local server" not in checks and calls == []


def test_open_secrets_permissions(configured):
    os.chmod(secrets_path(), 0o644)
    assert "chmod 600" in by_name(doctor(handler_with()))["secrets"].detail


def test_inactive_services_are_warnings(configured):
    checks = by_name(doctor(handler_with(), active=False))
    assert checks["service liber-mcp"].ok is False and checks["service liber-mcp"].warning is True


def test_cli_doctor(configured, monkeypatch):
    monkeypatch.setattr(doctor_module, "default_http_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler_with())))
    monkeypatch.setattr(doctor_module, "systemctl_active", lambda unit: False)
    ok = CliRunner().invoke(app, ["server", "doctor"])
    assert ok.exit_code == 0, ok.output
    assert "✓ protected resource metadata" in ok.output and "! service liber-mcp" in ok.output
    monkeypatch.setattr(doctor_module, "default_http_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler_with(local_up=False))))
    bad = CliRunner().invoke(app, ["server", "doctor"])
    assert bad.exit_code == 1 and "✗ local server" in bad.output
```

Run: `uv run pytest tests/test_server_doctor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'liber.server.doctor'`.

- [ ] **Step 2: Implement doctor.py**

`src/liber/server/doctor.py`:
```python
"""`liber server doctor`: check everything strict MCP clients (claude.ai, ChatGPT) need."""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from liber.config import resolve_vault
from liber.errors import LiberError
from liber.server.settings import ServerSettings, load_secrets, load_server_settings
from liber.vaultconfig import load_vault_config

PRM_PATH = "/.well-known/oauth-protected-resource/mcp"
AS_PATH = "/.well-known/oauth-authorization-server"
UNITS = ("liber-mcp", "cloudflared-liber")
_PROBE = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
_PROBE_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    warning: bool = False

    def __str__(self) -> str:
        mark = "✓" if self.ok else ("!" if self.warning else "✗")
        return f"{mark} {self.name} — {self.detail}"


def default_http_client() -> httpx.Client:
    return httpx.Client(timeout=10.0, follow_redirects=False)


def systemctl_active(unit: str) -> bool:
    try:
        return subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", f"{unit}.service"], check=False
        ).returncode == 0
    except OSError:
        return False


def _norm(url: str) -> str:
    return url.rstrip("/") + "/"


def _attempt(checks: list[Check], name: str, fn, describe):
    try:
        value = fn()
    except LiberError as exc:
        checks.append(Check(name, False, str(exc)))
        return None
    checks.append(Check(name, True, describe(value)))
    return value


def _vault():
    vault = resolve_vault()
    load_vault_config(vault)
    return vault


def _get_json(http: httpx.Client, url: str) -> tuple[dict | None, str]:
    try:
        response = http.get(url)
    except httpx.HTTPError as exc:
        return None, f"could not reach {url}: {exc}"
    if response.status_code != 200:
        return None, f"{url} returned {response.status_code}"
    try:
        data = response.json()
    except ValueError:
        return None, f"{url} did not return JSON"
    return (data, "") if isinstance(data, dict) else (None, f"{url} did not return a JSON object")


def _local_server(http: httpx.Client, settings: ServerSettings) -> Check:
    url = f"http://{settings.host}:{settings.port}/mcp"
    try:
        response = http.post(url, json=_PROBE, headers=_PROBE_HEADERS)
    except httpx.HTTPError:
        return Check("local server", False, f"nothing answering at {url}; check: systemctl --user status liber-mcp")
    if response.status_code != 401:
        return Check("local server", False, f"{url} answered {response.status_code}; expected 401 (login required)")
    return Check("local server", True, f"answering at {url}")


def _resource_metadata(http: httpx.Client, base: str) -> Check:
    data, error = _get_json(http, base + PRM_PATH)
    if data is None:
        return Check("protected resource metadata", False, error)
    problems = []
    if data.get("resource") != f"{base}/mcp":
        problems.append(f"resource is {data.get('resource')!r}, expected '{base}/mcp'")
    servers = data.get("authorization_servers")
    if not (isinstance(servers, list) and len(servers) == 1 and _norm(str(servers[0])) == _norm(base)):
        problems.append(f"authorization_servers is {servers!r}, expected exactly ['{base}/']")
    return Check("protected resource metadata", not problems, "; ".join(problems) or "resource and server match")


def _server_metadata(http: httpx.Client, base: str) -> Check:
    data, error = _get_json(http, base + AS_PATH)
    if data is None:
        return Check("authorization server metadata", False, error)
    problems = []
    if "S256" not in (data.get("code_challenge_methods_supported") or []):
        problems.append("PKCE S256 not advertised")
    if "none" not in (data.get("token_endpoint_auth_methods_supported") or []):
        problems.append("'none' missing from token_endpoint_auth_methods_supported")
    if data.get("client_id_metadata_document_supported") is not True:
        problems.append("client_id_metadata_document_supported is not true")
    if not data.get("registration_endpoint"):
        problems.append("no registration_endpoint (DCR)")
    if _norm(str(data.get("issuer", ""))) != _norm(base):
        problems.append(f"issuer is {data.get('issuer')!r}, expected '{base}/'")
    if data.get("authorization_response_iss_parameter_supported") is not True:
        problems.append("authorization_response_iss_parameter_supported is not true")
    return Check("authorization server metadata", not problems, "; ".join(problems) or "PKCE, CIMD, DCR and iss OK")


def _auth_challenge(http: httpx.Client, base: str) -> Check:
    try:
        response = http.post(f"{base}/mcp", json=_PROBE, headers=_PROBE_HEADERS)
    except httpx.HTTPError as exc:
        return Check("auth challenge", False, f"could not reach {base}/mcp: {exc}")
    header = response.headers.get("www-authenticate", "")
    if response.status_code != 401 or "resource_metadata=" not in header:
        return Check("auth challenge", False,
                     f"expected 401 with a resource_metadata= challenge, got {response.status_code} {header!r}")
    return Check("auth challenge", True, "unauthenticated requests get 401 with resource_metadata")


def run_doctor(*, http: httpx.Client, systemctl: Callable[[str], bool]) -> list[Check]:
    checks: list[Check] = []
    settings = _attempt(checks, "server config", load_server_settings, lambda s: f"{s.base_url}, port {s.port}")
    _attempt(checks, "secrets", load_secrets, lambda _: "present, mode 600")
    _attempt(checks, "vault", _vault, str)
    if settings is not None:
        checks.append(_local_server(http, settings))
        checks.append(_resource_metadata(http, settings.base_url))
        checks.append(_server_metadata(http, settings.base_url))
        checks.append(_auth_challenge(http, settings.base_url))
    for unit in UNITS:
        active = systemctl(unit)
        detail = "active" if active else f"not active; start it with: systemctl --user enable --now {unit}.service"
        checks.append(Check(f"service {unit}", active, detail, warning=True))
    return checks
```

- [ ] **Step 3: Add the `doctor` command**

In `src/liber/cli.py`, append:
```python
@server_app.command("doctor")
def server_doctor_cmd() -> None:
    """Check the HTTP server end to end, the way claude.ai and ChatGPT will see it."""
    from liber.server import doctor

    with doctor.default_http_client() as http:
        checks = doctor.run_doctor(http=http, systemctl=doctor.systemctl_active)
    for check in checks:
        typer.echo(str(check))
    if any(not c.ok and not c.warning for c in checks):
        raise typer.Exit(1)
```

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/liber/server/doctor.py src/liber/cli.py tests/test_server_doctor.py
git commit -m "feat(server): liber server doctor

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

### Task 9: Documentation, live checklist and final verification

**Files:**
- Modify: `README.md`
- Create: `docs/manual-test/SERVER-CHECKLIST.md`

**Interfaces:**
- Consumes: every command from Tasks 4–8.
- Produces: user-facing docs.

- [ ] **Step 1: Update the README**

Make these edits in `README.md`:

1. In the "How it fits together" diagram, change `MCP server (later) → agents` to `liber serve (MCP) → AI tools`.

2. Replace the paragraph that begins `Planned later: an MCP access server` with:
```markdown
Every AI tool can reach the vault through liber's MCP server: Claude Code and Claude Desktop locally, and claude.ai (web and phone), ChatGPT and other cloud tools through `https://<your domain>/mcp`. See [Connect your AI tools](#connect-your-ai-tools). Planned next: a voice interviewer (OpenAI GPT-Live-1 for the conversation, Claude as the interviewer's brain) that drops transcripts into `inbox/`.
```

3. Insert this section immediately before `## Vault layout`:
````markdown
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
````

4. Add these rows to the end of the table in `## Commands`:
```markdown
| `liber serve` | Run the MCP server over stdio for local apps (sees everything) |
| `liber serve --http` | Run the MCP server over HTTP with GitHub login, for the tunnel (normally run by systemd) |
| `liber server init` | Set up the HTTP server: secrets, config, systemd services and tunnel config |
| `liber server doctor` | Check the HTTP server end to end, as claude.ai and ChatGPT will see it |
| `liber token create/list/revoke <name>` | Manage service tokens for tools that can't sign in |
| `liber logout-all` | Sign out every connected cloud app |
```

5. In `## Development`, change the sentence beginning `The skills live in` to:
```markdown
The skills live in `src/liber/skills/`. After changing them, walk through `docs/manual-test/CHECKLIST.md`. After changing the server, walk through `docs/manual-test/SERVER-CHECKLIST.md`. The designs and plans are in `docs/superpowers/`.
```

- [ ] **Step 2: Write the live checklist**

`docs/manual-test/SERVER-CHECKLIST.md`:
````markdown
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
````

- [ ] **Step 3: Full verification**

Run: `uv run pytest -q`
Expected: all tests pass (101 existing plus the new server tests) with pristine output.

Run: `uv run liber --help` and `uv run liber server --help` and `uv run liber token --help`
Expected:
- the top level lists `serve`, `logout-all`, `server` and `token` alongside the existing commands;
- `server` lists `init` and `doctor`;
- `token` lists `create`, `list` and `revoke`.

Run this stdio smoke test in a throwaway vault, which does not touch the real config:
```bash
export XDG_CONFIG_HOME=$(mktemp -d) XDG_DATA_HOME=$(mktemp -d)
export LIBER_VAULT=$(mktemp -d)/v
uv run liber init "$LIBER_VAULT"
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  | timeout 20 uv run liber serve | head -1
```
Expected: one JSON line with `"serverInfo":{"name":"liber"`, and nothing else on stdout.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/manual-test/SERVER-CHECKLIST.md
git commit -m "docs: connect your AI tools, access server setup, live checklist

Co-Authored-By: <your model name> <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LQ4ZT31spDEH82R7AvBdmV"
```

---

## After the plan: user-performed steps (not a subagent task)

These change external accounts and system services, so the user does them by following the README's "Cloud apps (one-time setup)" section:

1. Set up Cloudflare DNS for the domain.
2. Run the `cloudflared` login, create and route steps.
3. Create the GitHub OAuth app.
4. Run `liber server init`.
5. Run the `systemctl` and `loginctl` commands.
6. Run `liber server doctor`.
7. Add the connectors.
8. Walk through `docs/manual-test/SERVER-CHECKLIST.md`.
