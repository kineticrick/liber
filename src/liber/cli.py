"""Thin command-line layer: parse args, call modules, print results."""

import asyncio
import logging
import math
import sys
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Optional

import typer

from liber.archive import archive_document, archive_notes
from liber.bundle import build_bundle
from liber.check import run_checks
from liber.clipboard import copy_to_clipboard
from liber.config import resolve_vault, write_user_config
from liber.errors import LiberError
from liber.extract import extract_pending
from liber.inbox import add_files, add_note, inbox_status
from liber.scaffold import default_skills_dir, init_vault
from liber.server.settings import load_ceilings, load_secrets, load_server_settings
from liber.server.tokens import TokenStore

app = typer.Typer(
    no_args_is_help=True,
    help="liber: a personal knowledge base about you, readable by any LLM or agent.",
)

STATE_LABELS = {
    "ready": "ready",
    "pending-extraction": "needs extraction (run liber extract)",
    "extracted": "extracted",
    "no-text": "no text found",
    "unsupported-folder": "folder — move its files into inbox/ directly",
}


@app.callback()
def _root() -> None:
    """liber: a personal knowledge base about you."""


@contextmanager
def handle_errors():
    try:
        yield
    except LiberError as exc:
        typer.secho(f"error: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1) from exc


@app.command("init")
def init_cmd(path: Annotated[Path, typer.Argument(help="Where to create the vault, e.g. ~/liber-vault")]) -> None:
    """Create a new vault and make it the default."""
    with handle_errors():
        target = path.expanduser().resolve()
        init_vault(target, default_skills_dir())
        config = write_user_config(target)
    typer.echo(f"Created liber vault at {target}")
    typer.echo(f"Default vault set in {config}")
    typer.echo("Next: open it in Obsidian, add notes to inbox.md, then run /ingest in Claude Code there.")


@app.command("check")
def check_cmd() -> None:
    """Validate the vault: frontmatter, structure, links, sync conflicts, AGENTS.md size."""
    with handle_errors():
        problems = run_checks(resolve_vault())
    if not problems:
        typer.echo("✓ no problems found")
        return
    for problem in problems:
        typer.echo(str(problem))
    typer.echo(f"{len(problems)} problem(s) found")
    raise typer.Exit(1)


@app.command("note")
def note_cmd(text: Annotated[list[str], typer.Argument(help="The note; quotes optional")]) -> None:
    """Add a quick note to inbox.md."""
    with handle_errors():
        line = add_note(resolve_vault(), " ".join(text), datetime.now())
    typer.echo(f"Added to inbox.md: {line}")


@app.command("add")
def add_cmd(files: Annotated[list[Path], typer.Argument(help="Documents to copy into inbox/")]) -> None:
    """Copy documents into inbox/ for the next /ingest."""
    with handle_errors():
        vault = resolve_vault()
        added = add_files(vault, [f.expanduser() for f in files])
    for path in added:
        typer.echo(f"Added {path.relative_to(vault).as_posix()}")


@app.command("status")
def status_cmd() -> None:
    """Show what's waiting in the inbox."""
    with handle_errors():
        status = inbox_status(resolve_vault())
    typer.echo(f"Inbox notes: {len(status.notes)}")
    for line in status.notes:
        typer.echo(f"  {line}")
    typer.echo(f"Documents: {len(status.documents)}")
    for doc in status.documents:
        typer.echo(f"  {doc.name} — {STATE_LABELS[doc.state]}")
    if status.conflicts:
        typer.echo("Sync conflicts:")
        for rel in status.conflicts:
            typer.echo(f"  {rel}")
    else:
        typer.echo("Sync conflicts: none")


@app.command("extract")
def extract_cmd() -> None:
    """Convert waiting PDF, Word, and HTML documents in inbox/ to Markdown text."""
    with handle_errors():
        results = extract_pending(resolve_vault())
    if not results:
        typer.echo("Nothing to extract.")
        return
    labels = {"extracted": "extracted", "no-text": "no text found", "failed": "failed"}
    for result in results:
        suffix = f" ({result.detail})" if result.detail else ""
        typer.echo(f"{result.name} — {labels[result.state]}{suffix}")


@app.command("archive")
def archive_cmd(
    name: Annotated[Optional[str], typer.Argument(help="Document in inbox/ to move to sources/documents/")] = None,
    notes: Annotated[bool, typer.Option("--notes", help="Archive the notes in inbox.md instead")] = False,
    count: Annotated[Optional[int], typer.Option("--count", help="With --notes: archive only the first N notes")] = None,
) -> None:
    """Move a processed item out of the inbox into sources/. Prints the final path(s)."""
    with handle_errors():
        if (name is None) == (not notes):
            raise LiberError("give either a document name or --notes (not both)")
        if count is not None and not notes:
            raise LiberError("--count only applies with --notes")
        vault = resolve_vault()
        if notes:
            dest = archive_notes(vault, datetime.now(), count)
            moved = [dest] if dest else []
        else:
            moved = archive_document(vault, name)
    if not moved:
        typer.echo("No notes to archive.")
    for path in moved:
        typer.echo(path.relative_to(vault).as_posix())


@app.command("bundle")
def bundle_cmd(
    topics: Annotated[Optional[str], typer.Option("--topics", help="Comma-separated top-level folders, e.g. career,goals")] = None,
    max_sensitivity: Annotated[str, typer.Option("--max-sensitivity", help="public, personal, or private")] = "personal",
    copy: Annotated[bool, typer.Option("--copy", help="Copy to the clipboard instead of printing")] = False,
) -> None:
    """Combine AGENTS.md and vault files into one Markdown document for any chatbot."""
    with handle_errors():
        topic_list = [t.strip() for t in topics.split(",") if t.strip()] if topics else None
        bundle = build_bundle(resolve_vault(), topic_list, max_sensitivity)
    summary = f"{len(bundle.included)} file(s) included, {len(bundle.excluded)} excluded above '{max_sensitivity}' or without valid sensitivity"
    excluded_lines = [f"excluded: {rel} ({reason})" for rel, reason in bundle.excluded]
    if copy and copy_to_clipboard(bundle.text):
        typer.echo(f"Copied to clipboard: {summary}", err=True)
        for line in excluded_lines:
            typer.echo(line, err=True)
        return
    if copy:
        typer.echo("warning: no clipboard tool worked (install wl-copy or xclip); printing instead", err=True)
    typer.echo(bundle.text)
    typer.echo(summary, err=True)
    for line in excluded_lines:
        typer.echo(line, err=True)


def _configure_server_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )


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


@app.command("logout-all")
def logout_all_cmd() -> None:
    """Log out every connected cloud app (they will need to sign in with GitHub again)."""
    with handle_errors():
        from liber.server import auth

        auth.logout_all()
    typer.echo("All OAuth sessions revoked. Restart the server to apply: systemctl --user restart liber-mcp")


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
    from liber.server.settings import secrets_path, user_config_path

    with handle_errors():
        # Check secrets before prompting
        server_setup.check_secrets_absent(force)

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
    config_path = user_config_path()
    for path in result.kept:
        if path == config_path:
            typer.echo(f"kept existing {path}")
        else:
            typer.echo(f"kept existing {path} (use --force to regenerate)")
    typer.echo("\nNext, run:")
    for step in result.next_steps:
        typer.echo(f"  {step}")


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


@app.command("interview")
def interview_cmd(
    topic: Annotated[Optional[str], typer.Argument(help="What to talk about; omit and liber picks a gap")] = None,
    continue_last: Annotated[bool, typer.Option("--continue", help="Continue the most recent interview's topic")] = False,
    minutes: Annotated[Optional[int], typer.Option("--minutes", help="Time limit for this interview")] = None,
    model: Annotated[Optional[str], typer.Option("--model", help="Claude model for this interview")] = None,
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Print the page URL instead of opening it")] = False,
    setup: Annotated[bool, typer.Option("--setup", help="Store your first name and API keys")] = False,
    notes: Annotated[Optional[Path], typer.Option("--notes", help="Regenerate notes for this transcript")] = None,
    recover: Annotated[bool, typer.Option("--recover", help="Finish interviews that were cut off")] = False,
) -> None:
    """Have a voice interview that fills your vault (transcript + notes land in inbox/)."""
    _configure_server_logging()
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    with handle_errors():
        from liber.interview import runner
        from liber.interview.settings import load_anthropic_key, load_voice_keys, save_voice_setup

        if setup:
            name = typer.prompt("Your first name (the interviewer will use it)")
            openai_key = typer.prompt("OpenAI API key", hide_input=True)
            anthropic_key = typer.prompt("Anthropic API key", hide_input=True)
            save_voice_setup(name, openai_key, anthropic_key)
            typer.echo("Saved. Start an interview with: liber interview")
            return
        if topic and continue_last:
            raise LiberError("give either a topic or --continue, not both")
        runner.require_voice_extra()
        settings = runner.prepare_settings(minutes, model)
        vault = resolve_vault()
        if recover:
            results = asyncio.run(runner.run_recover(settings=settings, anthropic_key=load_anthropic_key(), vault=vault))
            if not results:
                typer.echo("No unfinished interviews.")
            for result in results:
                typer.echo(f"Recovered: {result.transcript} {result.notes or '(notes failed)'}")
            return
        if notes is not None:
            path = asyncio.run(runner.run_notes(
                transcript=notes.expanduser(), settings=settings, anthropic_key=load_anthropic_key(), vault=vault))
            typer.echo(f"Notes written: {path}")
            return
        result = asyncio.run(runner.run_interview(
            topic=topic, continue_last=continue_last, settings=settings, keys=load_voice_keys(), vault=vault,
            open_browser=not no_browser, announce=typer.echo,
        ))
    if result.failed:
        typer.echo("Saving failed; your draft is kept. Run: liber interview --recover")
        return
    if result.transcript is None:
        typer.echo("Nothing was recorded, so no files were written.")
        return
    typer.echo(f"Transcript: {result.transcript}")
    typer.echo(f"Notes: {result.notes}" if result.notes else
               f"Notes failed; retry with: liber interview --notes {result.transcript}")
    if result.voice_seconds > 0:
        typer.echo(f"Voice time: {max(1, math.ceil(result.voice_seconds / 60))} min")
    typer.echo("Next: open Claude Code in your vault and run /ingest.")
