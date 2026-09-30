"""Thin command-line layer: parse args, call modules, print results."""

from contextlib import contextmanager

import typer

from liber.errors import LiberError

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
