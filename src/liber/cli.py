"""Thin command-line layer: parse args, call modules, print results."""

from contextlib import contextmanager
from datetime import datetime
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
    if copy and copy_to_clipboard(bundle.text):
        typer.echo(f"Copied to clipboard: {summary}", err=True)
        return
    if copy:
        typer.echo("warning: no clipboard tool worked (install wl-copy or xclip); printing instead", err=True)
    typer.echo(bundle.text)
    typer.echo(summary, err=True)
