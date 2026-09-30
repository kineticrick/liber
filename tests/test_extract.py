from typer.testing import CliRunner

from helpers import make_docx, make_pdf, write
from liber.cli import app
from liber.extract import extract_pending
from liber.inbox import NO_TEXT_MARKER, inbox_documents

TEXT = "The quick brown fox jumps over the lazy dog near liber."


def states(vault):
    return {d.name: d.state for d in inbox_documents(vault)}


def test_fake_converter_writes_sidecars(vault):
    write(vault / "inbox" / "a.pdf", "x")
    write(vault / "inbox" / "b.pdf", "x")
    write(vault / "inbox" / "c.md", "already text")
    texts = {"a.pdf": TEXT, "b.pdf": "   \n  "}
    results = extract_pending(vault, lambda p: texts[p.name])
    assert sorted((r.name, r.state) for r in results) == [("a.pdf", "extracted"), ("b.pdf", "no-text")]
    sidecar = (vault / "inbox" / "a.pdf.md").read_text()
    assert sidecar.startswith("<!-- liber: extracted from a.pdf -->") and TEXT in sidecar
    assert NO_TEXT_MARKER in (vault / "inbox" / "b.pdf.md").read_text()
    assert states(vault) == {"a.pdf": "extracted", "b.pdf": "no-text", "c.md": "ready"}


def test_already_extracted_is_not_reconverted(vault):
    write(vault / "inbox" / "a.pdf", "x")
    calls = []
    convert = lambda p: calls.append(p.name) or TEXT
    extract_pending(vault, convert)
    extract_pending(vault, convert)
    assert calls == ["a.pdf"]


def test_one_failure_does_not_stop_the_batch(vault):
    # Review Focus 3: a corrupt document is reported and left pending; others still extract.
    write(vault / "inbox" / "bad.pdf", "x")
    write(vault / "inbox" / "good.pdf", "x")

    def convert(path):
        if path.name == "bad.pdf":
            raise ValueError("corrupt file")
        return TEXT

    results = {r.name: r for r in extract_pending(vault, convert)}
    assert results["bad.pdf"].state == "failed" and "corrupt" in results["bad.pdf"].detail
    assert results["good.pdf"].state == "extracted"
    assert states(vault)["bad.pdf"] == "pending-extraction"


def test_real_html(vault):
    write(vault / "inbox" / "page.html", f"<html><body><h1>Title</h1><p>{TEXT}</p></body></html>")
    assert [r.state for r in extract_pending(vault)] == ["extracted"]
    assert "quick brown fox" in (vault / "inbox" / "page.html.md").read_text()


def test_real_pdf(vault):
    make_pdf(vault / "inbox" / "paper.pdf", "Hello liber this is a test document")
    assert [r.state for r in extract_pending(vault)] == ["extracted"]
    assert "Hello liber" in (vault / "inbox" / "paper.pdf.md").read_text()


def test_real_textless_pdf(vault):
    make_pdf(vault / "inbox" / "scan.pdf", None)
    assert [r.state for r in extract_pending(vault)] == ["no-text"]


def test_real_docx(vault):
    make_docx(vault / "inbox" / "resume.docx", TEXT)
    assert [r.state for r in extract_pending(vault)] == ["extracted"]
    assert "quick brown fox" in (vault / "inbox" / "resume.docx.md").read_text()


def test_cli_extract(configured_vault):
    write(configured_vault / "inbox" / "page.html", f"<p>{TEXT}</p>")
    runner = CliRunner()
    result = runner.invoke(app, ["extract"])
    assert result.exit_code == 0
    assert "page.html — extracted" in result.output
    again = runner.invoke(app, ["extract"])
    assert "Nothing to extract" in again.output
