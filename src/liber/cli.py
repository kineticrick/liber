"""Thin command-line layer: parse args, call modules, print results."""

from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer

from liber.check import run_checks
from liber.config import resolve_vault, write_user_config
from liber.errors import LiberError
from liber.scaffold import default_skills_dir, init_vault

app = typer.Typer(
    no_args_is_help=True,
    help="liber: a personal knowledge base about you, readable by any LLM or agent.",
)


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
