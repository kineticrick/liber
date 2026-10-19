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


def test_record_cli_and_duplicate(configured_vault):
    args = ["ledger", "record", "--kind", "email", "--key", "bulk:takeout-2026-10-18", "--items", "4937",
            "--words", "452223", "--owner-words", "452223", "--years", "2004-2026",
            "--raw", '{"emails_exported": 18000}', "--file", str(configured_vault / "README.md")]
    (configured_vault / "README.md").write_text("x", encoding="utf-8")
    first = runner.invoke(app, args)
    assert first.exit_code == 0 and "Recorded." in first.output
    assert "Already recorded" in runner.invoke(app, args).output
    from liber import ledger

    e = ledger.load(configured_vault)["bulk:takeout-2026-10-18"]
    assert e.years == [2004, 2026] and e.raw == {"emails_exported": 18000} and e.files and e.tool == "liber ledger record"


def test_record_cli_rejects_bad_raw(configured_vault):
    r = runner.invoke(app, ["ledger", "record", "--kind", "email", "--key", "k", "--raw", "{nope"])
    assert r.exit_code == 1 and "--raw" in r.output


def test_list_prints_labels(configured_vault):
    from liber import ledger

    ledger.record(configured_vault, ledger.Entry(key="k", kind="document", date="2026-10-18", tool="t", label="my-label.pdf"))
    r = runner.invoke(app, ["ledger", "list"])
    assert r.exit_code == 0 and "my-label.pdf" in r.output


def test_stats_without_ledger(configured_vault):
    (configured_vault / "ledger.jsonl").unlink(missing_ok=True)
    r = runner.invoke(app, ["stats"])
    assert r.exit_code == 0 and "liber ledger backfill" in r.output


def test_stats_json_and_md(configured_vault):
    import json
    assert "## Input" in runner.invoke(app, ["stats", "--md"]).output
    assert "output" in json.loads(runner.invoke(app, ["stats", "--json"]).output)


def test_record_cli_rejects_bad_date(configured_vault):
    r = runner.invoke(app, ["ledger", "record", "--kind", "email", "--key", "k", "--date", "2026-13-01"])
    assert r.exit_code == 1 and "--date" in r.output


def test_backfill_cli_counts_only_real_items(configured_vault):
    docs = configured_vault / "sources" / "documents"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "mine.md").write_text("my words", encoding="utf-8")
    (docs / "facts-from-writing.md").write_text("<!-- liber proposal: analysis -->\nai words", encoding="utf-8")
    r = runner.invoke(app, ["ledger", "backfill"])
    assert r.exit_code == 0 and "Backfilled 1 item." in r.output
