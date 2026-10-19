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
    env = {**os.environ, "GIT_AUTHOR_DATE": f"{day}T12:00:00+00:00", "GIT_COMMITTER_DATE": f"{day}T12:00:00+00:00"}
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
