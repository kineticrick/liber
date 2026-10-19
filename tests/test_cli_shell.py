from typer.testing import CliRunner

from liber.cli import app, handle_errors
from liber.errors import LiberError

runner = CliRunner()


def test_help_runs():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "liber" in result.output


def test_handle_errors_converts_liber_error(capsys):
    import typer

    try:
        with handle_errors():
            raise LiberError("boom")
    except typer.Exit as exc:
        assert exc.exit_code == 1
    assert "error: boom" in capsys.readouterr().err


def test_status_sweeps_files_not_notes(configured_vault):
    vault = configured_vault
    from liber import ledger

    (vault / "inbox").mkdir(exist_ok=True)
    (vault / "inbox" / "phone.md").write_text("from my phone", encoding="utf-8")
    (vault / "inbox.md").write_text("# Inbox\n\n- 2026-10-01 09:00 — a note\n", encoding="utf-8")
    assert runner.invoke(app, ["status"]).exit_code == 0
    (entry,) = ledger.load(vault).values()
    assert entry.path == "inbox/phone.md" and entry.tool == "sweep"
