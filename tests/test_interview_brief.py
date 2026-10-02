import pytest

from helpers import write
from liber.interview import prompts
from liber.interview.brief import BRIEF_MAX_CHARS, VaultBrief, build_brief


def fm(type, sens, body):
    return f"---\ntype: {type}\nupdated: 2026-09-30\nsensitivity: {sens}\ntags: []\n---\n\n{body}\n"


def test_brief_has_profile_questions_and_topic_excerpts(vault):
    write(vault / "career" / "acme.md", fm("career", "personal", "# Acme\nLed the payments migration at Acme."))
    write(vault / "open-questions.md", "# Open questions\n\n- What happened in 2019?\n")
    brief = build_brief(vault, "payments")
    assert "About me" in brief.profile
    assert "What happened in 2019?" in brief.open_questions
    assert [e["path"] for e in brief.excerpts] == ["career/acme.md"]
    text = brief.render()
    assert "payments migration" in text and "What happened in 2019?" in text and "career/acme.md" in text
    assert "Topic: payments" in text


def test_private_content_never_in_brief(vault):
    # Review Focus 1
    write(vault / "core" / "secret.md", fm("core", "private", "# Secret\nPRIVATEMARK payments"))
    write(vault / "sources" / "documents" / "x.md", "SOURCEMARK payments\n")
    brief = build_brief(vault, "payments")
    text = brief.render()
    assert "PRIVATEMARK" not in text and "SOURCEMARK" not in text
    assert "sources" not in brief.folders


def test_no_topic_means_no_excerpts(vault):
    assert build_brief(vault, None).excerpts == []
    assert "Topic: (to be chosen)" in build_brief(vault, None).render()


def test_previous_notes_included(vault):
    assert "LASTTIME" in build_brief(vault, "x", previous_notes="LASTTIME notes").render()


def test_render_trims_excerpts_then_questions():
    big = "q" * 30_000
    excerpts = [{"path": f"p{i}.md", "title": "t", "snippets": ["s" * 1000]} for i in range(8)]
    brief = VaultBrief("topic", "PROFILE", big, excerpts, ["core"])
    text = brief.render()
    assert len(text) <= BRIEF_MAX_CHARS
    assert "PROFILE" in text and "p0.md" not in text and big not in text


def test_prompts_mention_name_and_key_rules():
    text = prompts.interviewer_instructions("Rick", "my career")
    assert "Rick" in text and "my career" in text
    assert "Silence is thinking time" in text and "one question at a time" in text.lower()
    assert len(text) < 12_000
    assert "Rick" in prompts.opening_instruction("Rick", "Where did it start?")
    assert "Where did it start?" in prompts.opening_instruction("Rick", "Where did it start?")
    assert prompts.FALLBACK_LINE == "Let's keep going — tell me more about that."
    assert prompts.NOTES_SECTIONS == ("New facts", "Corrections to the vault", "People mentioned",
                                      "Preferences, values and feelings", "Follow-up questions")
    for section in prompts.NOTES_SECTIONS:
        assert f"## {section}" in prompts.NOTES_SYSTEM
    assert '"hint"' in prompts.STEER_SYSTEM and '"question"' in prompts.OPENING_SYSTEM
    assert "3 minutes" in prompts.time_warning("Rick", 3)
    assert "typed note" in prompts.typed_note_context("Rick", "X")
