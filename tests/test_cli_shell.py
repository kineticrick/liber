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
