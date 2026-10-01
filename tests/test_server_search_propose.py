from datetime import datetime
from pathlib import Path

import pytest

from helpers import write
from liber.errors import LiberError
from liber.inbox import inbox_documents
from liber.server.knowledge import VaultView, proposal_label
from liber.vaultconfig import SENSITIVITY_LEVELS, sensitivity_rank

NOW = datetime(2026, 10, 1, 14, 5, 9)


def fm(type, sens, body, updated="2026-09-30"):
    return f"---\ntype: {type}\nupdated: {updated}\nsensitivity: {sens}\ntags: []\n---\n\n{body}\n"


def paths(results):
    return [r["path"] for r in results]


def test_results_with_all_terms_rank_first(vault):
    write(vault / "interests" / "a.md", fm("interest", "personal", "# Wheel\nwheel wheel wheel"))
    write(vault / "interests" / "b.md", fm("interest", "personal", "# Notes\npottery wheel"))
    assert paths(VaultView(vault, "personal").search("pottery wheel"))[:2] == ["interests/b.md", "interests/a.md"]


def test_title_hits_count_double(vault):
    write(vault / "interests" / "t.md", fm("interest", "personal", "# Sailing\nnothing"))
    write(vault / "interests" / "u.md", fm("interest", "personal", "# Other\nsailing"))
    results = VaultView(vault, "personal").search("sailing")
    assert [(r["path"], r["score"]) for r in results] == [("interests/t.md", 2), ("interests/u.md", 1)]


def test_ties_break_by_newest_updated_then_path(vault):
    write(vault / "interests" / "old.md", fm("interest", "personal", "kayak", updated="2025-01-01"))
    write(vault / "interests" / "new.md", fm("interest", "personal", "kayak", updated="2026-09-01"))
    write(vault / "interests" / "new2.md", fm("interest", "personal", "kayak", updated="2026-09-01"))
    assert paths(VaultView(vault, "personal").search("Kayak")) == [
        "interests/new.md", "interests/new2.md", "interests/old.md",
    ]


def test_frontmatter_is_not_searched(vault):
    write(vault / "interests" / "z.md",
          "---\ntype: interest\nupdated: 2026-09-30\nsensitivity: personal\ntags: [zebraquux]\n---\n\n# Z\nbody\n")
    assert VaultView(vault, "personal").search("zebraquux") == []


def test_snippets_are_centered_and_bounded(vault):
    body = "filler " * 100 + "MARKER one " + "filler " * 100 + "MARKER two " + "filler " * 100
    write(vault / "interests" / "s.md", fm("interest", "personal", body))
    [result] = VaultView(vault, "personal").search("marker")
    assert result["title"] == "s"
    assert len(result["snippets"]) == 2
    for snippet in result["snippets"]:
        assert "MARKER" in snippet
        assert len(snippet) <= 202
    assert result["snippets"][0].startswith("…") and result["snippets"][0].endswith("…")


def test_at_most_three_snippets(vault):
    write(vault / "interests" / "m.md", fm("interest", "personal", ("kiwi " + "pad " * 80) * 6))
    [result] = VaultView(vault, "personal").search("kiwi")
    assert len(result["snippets"]) == 3


@pytest.mark.parametrize("ceiling", SENSITIVITY_LEVELS)
def test_search_respects_ceiling_both_directions(vault, ceiling):
    # Review Focus 3: invisible files never match, so they never leak snippets
    for sens in SENSITIVITY_LEVELS:
        write(vault / "core" / f"{sens}.md", fm("core", sens, f"UNIQUETERM {sens}"))
    write(vault / "core" / "public (Conflicted copy x).md", fm("core", "public", "UNIQUETERM conflict"))
    write(vault / "core" / "nosens.md", "---\ntype: core\nupdated: 2026-09-30\n---\n\nUNIQUETERM nosens\n")
    write(vault / "sources" / "documents" / "d.md", "UNIQUETERM source\n")
    results = VaultView(vault, ceiling).search("uniqueterm")
    expected = {f"core/{s}.md" for s in SENSITIVITY_LEVELS if sensitivity_rank(s) <= sensitivity_rank(ceiling)}
    if ceiling == "private":
        expected.add("sources/documents/d.md")
    assert set(paths(results)) == expected
    joined = " ".join(s for r in results for s in r["snippets"])
    assert "conflict" not in joined and "nosens" not in joined


def test_folder_filter_and_limit(vault):
    for i in range(12):
        write(vault / "interests" / f"k{i:02}.md", fm("interest", "personal", "kayak"))
    write(vault / "goals" / "kayak-goal.md", fm("goal", "personal", "kayak"))
    view = VaultView(vault, "personal")
    assert len(view.search("kayak", limit=5)) == 5
    assert paths(view.search("kayak", folders=["goals/"])) == ["goals/kayak-goal.md"]


def test_empty_query_and_bad_limit(vault):
    view = VaultView(vault, "personal")
    with pytest.raises(LiberError, match="empty"):
        view.search("   ")
    for bad in (0, 51):
        with pytest.raises(LiberError, match="between 1 and 50"):
            view.search("x", limit=bad)


def test_propose_writes_inbox_file(vault):
    out = VaultView(vault, "personal").propose("I left Acme in September.", "chat about jobs", "Claude", NOW)
    assert out["file"] == "inbox/proposal-2026-10-01T14-05-09-claude.md"
    assert "/ingest" in out["message"]
    assert (vault / out["file"]).read_text(encoding="utf-8") == (
        "<!-- liber proposal -->\n"
        "# Proposed update from claude — 2026-10-01 14:05\n\n"
        "**Context:** chat about jobs\n\n"
        "I left Acme in September.\n"
    )


def test_propose_without_context(vault):
    out = VaultView(vault, "personal").propose("fact", None, "x", NOW)
    assert "**Context:** none given" in (vault / out["file"]).read_text(encoding="utf-8")


def test_same_second_collision_gets_suffix(vault):
    view = VaultView(vault, "personal")
    assert view.propose("one", None, "x", NOW)["file"] == "inbox/proposal-2026-10-01T14-05-09-x.md"
    assert view.propose("two", None, "x", NOW)["file"] == "inbox/proposal-2026-10-01T14-05-09-x-2.md"


@pytest.mark.parametrize("raw, label", [
    ("Claude", "claude"),
    ("claude.ai", "claude.ai"),
    ("../../etc/passwd", "etc-passwd"),
    ("Ünïcode Client!", "n-code-client"),
    ("x..y", "x.y"),
    ("a" * 60, "a" * 40),
    ("...", "unknown"),
    ("", "unknown"),
    (None, "unknown"),
])
def test_proposal_label(raw, label):
    assert proposal_label(raw) == label


@pytest.mark.parametrize("client", ["../../etc/passwd", "a/b\\c", "Ünïcode", "", None])
def test_hostile_client_names_stay_in_inbox(vault, client):
    # Review Focus 4
    out = VaultView(vault, "personal").propose("text", None, client, NOW)
    created = vault / out["file"]
    assert created.parent == vault / "inbox" and created.is_file()


def test_propose_limits(vault):
    view = VaultView(vault, "personal")
    with pytest.raises(LiberError, match="empty"):
        view.propose(" \n ", None, "x", NOW)
    with pytest.raises(LiberError, match="20000"):
        view.propose("a" * 20001, None, "x", NOW)
    with pytest.raises(LiberError, match="2000"):
        view.propose("ok", "c" * 2001, "x", NOW)
    assert list((vault / "inbox").glob("proposal-*.md")) == []
    view.propose("a" * 20000, "c" * 2000, "x", NOW)
    assert len(list((vault / "inbox").glob("proposal-*.md"))) == 1


def test_pending_cap(vault):
    for i in range(50):
        write(vault / "inbox" / f"proposal-{i}.md", "x")
    with pytest.raises(LiberError, match="/ingest"):
        VaultView(vault, "personal").propose("more", None, "x", NOW)


def test_proposals_are_invisible_to_tools(vault):
    view = VaultView(vault, "private")
    view.propose("SECRETPROPOSAL", None, "x", NOW)
    assert VaultView(vault, "private").search("secretproposal") == []


def test_proposals_are_ready_for_ingest(vault):
    out = VaultView(vault, "personal").propose("fact", None, "x", NOW)
    assert [(d.name, d.state) for d in inbox_documents(vault)] == [(Path(out["file"]).name, "ready")]
