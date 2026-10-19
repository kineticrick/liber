import json
import os
import subprocess
from datetime import date

from helpers import write
from liber import stats
from liber.vaultconfig import load_vault_config

FACT = "- I like X. (2026-10) (src: [[sources/a.md]])\n"


def _out(vault, files):
    return stats.output_from_files(files, load_vault_config(vault))


def test_fact_detection(vault):
    text = FACT + "- no source here\nProse (src: [[b]])\n"
    out = _out(vault, [("interests/x.md", text)])
    assert out.facts == 1
    assert out.private_facts == 0


def test_private_facts_counted(vault):
    text = "---\nsensitivity: private\n---\n" + FACT
    out = _out(vault, [("interests/x.md", text)])
    assert (out.facts, out.private_facts) == (1, 1)


def test_people_interests_projects_counts(vault):
    out = _out(vault, [("people/a.md", "x"), ("interests/b.md", "x"), ("career/projects/c.md", "x")])
    assert (out.people, out.interests, out.projects, out.files) == (1, 1, 1, 3)


def test_words_exclude_frontmatter(vault):
    out = _out(vault, [("people/a.md", "---\nname: a\n---\none two three\n")])
    assert out.words == 3


def test_earliest_year(vault):
    text = "- Lived there [1995–1999] (src: [[sources/notes/2001-01.md]])\n" + FACT
    assert _out(vault, [("interests/x.md", text)]).earliest_year == 1995
    assert _out(vault, [("interests/x.md", "- a (src: [[sources/notes/2001-01.md]])\n")]).earliest_year is None


def test_sources_by_subfolder(vault):
    write(vault / "sources/documents/a.pdf", "x")
    write(vault / "sources/documents/b.pdf", "x")
    write(vault / "sources/notes/2026-10.md", "x")
    assert stats.output_now(vault).sources == {"documents": 2, "notes": 1}


def test_output_now_reads_working_tree(vault):
    write(vault / "interests/x.md", FACT)
    write(vault / "inbox/y.md", FACT)
    out = stats.output_now(vault)
    assert (out.facts, out.interests) == (1, 1)


def _commit(repo, msg, day):
    stamp = day if "T" in day else f"{day}T12:00:00+00:00"
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-q", "-m", msg],
                   check=True, env=env)


def _repo(tmp_path, vault):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "liber.toml").write_text((vault / "liber.toml").read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    return repo


def test_growth_by_month(tmp_path, vault):
    repo = _repo(tmp_path, vault)
    write(repo / "interests/x.md", FACT)
    write(repo / "inbox/ignored.md", FACT)
    _commit(repo, "one", "2026-09-15")
    write(repo / "interests/x.md", FACT * 3)
    _commit(repo, "two", "2026-10-10")
    lines = [
        {"key": "k1", "kind": "note", "date": "2026-09-20", "tool": "t", "words": 10, "owner_words": 10},
        {"key": "k2", "kind": "note", "date": "2026-10-02", "tool": "t", "words": 5, "owner_words": 4},
        {"key": "k3", "kind": "document", "date": "2026-10-03", "tool": "t"},
        {"key": "k4", "kind": "derived", "date": "2026-10-03", "tool": "t", "words": 99, "owner_words": 99},
        {"key": "meta:backfill", "kind": "other", "date": "2026-10-03", "tool": "t", "words": 77, "owner_words": 77},
    ]
    (repo / "ledger.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    rows = stats.growth(repo, date(2026, 10, 18))
    assert rows == [
        stats.MonthRow("2026-09", 10, 10, 1, 1),
        stats.MonthRow("2026-10", 5, 4, 3, 2),
    ]


def test_growth_without_git(tmp_path, vault):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "liber.toml").write_text((vault / "liber.toml").read_text(encoding="utf-8"), encoding="utf-8")
    write(plain / "interests/x.md", FACT)
    rows = stats.growth(plain, date(2026, 10, 18))
    assert rows == [stats.MonthRow("2026-10", 0, 0, 1, 1)]


def test_growth_non_ascii_filename(tmp_path, vault):
    repo = _repo(tmp_path, vault)
    write(repo / "people/José.md", FACT)
    _commit(repo, "one", "2026-10-05")
    rows = stats.growth(repo, date(2026, 10, 18))
    assert rows[-1].facts == 1 == stats.output_now(repo).facts


def test_growth_commit_month_is_local(tmp_path, vault):
    repo = _repo(tmp_path, vault)
    write(repo / "interests/x.md", FACT)
    _commit(repo, "late", "2026-09-30T21:00:00-05:00")
    rows = stats.growth(repo, date(2026, 10, 18))
    assert [(r.month, r.facts) for r in rows] == [("2026-09", 1), ("2026-10", 1)]


def test_earliest_year_ignores_out_of_range(vault):
    text = "- a 1850 (src: [[x]])\n- b 2150 (src: [[x]])\n- c 2020 (src: [[x]])\n"
    assert _out(vault, [("interests/x.md", text)]).earliest_year == 2020


# --- liber stats report ---

def _entry(key, **kw):
    from liber import ledger
    base = dict(kind="document", date="2026-10-18", tool="t")
    return ledger.Entry(key=key, **{**base, **kw})


def _report_vault(vault):
    from liber import ledger
    write(vault / "interests/x.md", "- a (1995-03) (src: [[s]])\n- b (2020) (src: [[s]])\n")
    write(vault / "interests/p.md", "---\nsensitivity: private\n---\n- c (2021) (src: [[s]])\n")
    write(vault / "sources/documents/d.pdf", "x")
    ledger.record(vault, _entry("a", kind="email", owner_words=1000, words=1000, years=[2004, 2010]))
    ledger.record(vault, _entry("b", kind="talk-audio", owner_words=234, words=300, audio_seconds=7200, years=[2020, 2026]))
    return vault


def test_headline_wording(vault):
    _report_vault(vault)
    first = stats.render_text(stats.build_report(vault, date(2026, 10, 18))).splitlines()[0]
    assert first == "liber has taken in 1,234 words I've written or said, plus 2.0 hours of talks, spanning 2004–2026."


def test_second_headline_line(vault):
    _report_vault(vault)
    second = stats.render_text(stats.build_report(vault, date(2026, 10, 18))).splitlines()[1]
    assert second == ("It stores 3 facts about me (1 private), going back to 1995, "
                      "drawn from 1 archived sources.")


def test_derived_excluded_from_totals(vault):
    from liber import ledger
    _report_vault(vault)
    before = stats.build_report(vault, date(2026, 10, 18))["input"]
    ledger.record(vault, _entry("d", kind="derived", words=999, owner_words=0))
    ledger.record(vault, _entry("meta:x", kind="other", words=999, owner_words=999))
    assert stats.build_report(vault, date(2026, 10, 18))["input"] == before


def test_no_labels_or_paths_anywhere(vault):
    import json
    from liber import ledger
    _report_vault(vault)
    ledger.record(vault, _entry("k", label="bloodwork.pdf", path="inbox/bloodwork.pdf", words=5, owner_words=5))
    rep = stats.build_report(vault, date(2026, 10, 18))
    for blob in (stats.render_text(rep), stats.render_markdown(rep), json.dumps(rep)):
        assert "bloodwork" not in blob and "inbox/" not in blob


def test_markdown_has_sections(vault):
    _report_vault(vault)
    md = stats.render_markdown(stats.build_report(vault, date(2026, 10, 18)))
    assert "## Input" in md and "## Output" in md and "## Growth" in md
    assert "| --- |" in md or "|---|" in md


def test_read_to_get_there_sums_raw(vault):
    from liber import ledger
    ledger.record(vault, _entry("a", raw={"emails_exported": 18000}))
    ledger.record(vault, _entry("b", raw={"emails_exported": 2}))
    assert stats.build_report(vault, date(2026, 10, 18))["read_to_get_there"] == {"emails_exported": 18002}


def test_unknown_owner_words_note(vault):
    from liber import ledger
    ledger.record(vault, _entry("a", owner_words=10, words=10))
    ledger.record(vault, _entry("b", kind="talk-audio", words=500))
    rep = stats.build_report(vault, date(2026, 10, 18))
    assert rep["owner_words_unknown"] == 1
    assert "Note: 1 item(s) have no owner word count yet (set [owner] name in ~/.config/liber/config.toml)." in stats.render_text(rep)
    assert "10 words" in stats.render_text(rep)


def test_recorded_cost_and_no_ledger(vault):
    from liber import ledger
    ledger.ledger_path(vault).unlink(missing_ok=True)
    rep = stats.build_report(vault, date(2026, 10, 18))
    assert rep["has_ledger"] is False and rep["recorded_cost_usd"] is None
    assert stats.render_text(rep).rstrip().endswith("No input ledger yet. Run: liber ledger backfill")
    ledger.record(vault, _entry("a", owner_words=1, words=1, cost_usd=1.5, cost_basis="invoice"))
    rep = stats.build_report(vault, date(2026, 10, 18))
    assert rep["recorded_cost_usd"] == 1.5 and "recorded cost" in stats.render_text(rep)


def test_growth_bar(vault):
    rep = stats.build_report(vault, date(2026, 10, 18))
    rep["growth"] = [dict(month="2026-01", words_in=0, owner_words_in=0, facts=60, facts_added=60),
                     dict(month="2026-02", words_in=0, owner_words_in=0, facts=1, facts_added=1),
                     dict(month="2026-03", words_in=0, owner_words_in=0, facts=0, facts_added=0)]
    lines = [l for l in map(str.strip, stats.render_text(rep).splitlines()) if l.startswith("2026-")]
    assert "█" * 30 in lines[0] and "▏" in lines[1] and "█" not in lines[1] and "▏" not in lines[2]
