from datetime import date

from liber.interview.transcript import (
    AI, ME, NOTE, RESUME, TranscriptAssembler, Turn, dialogue, mmss, render_transcript, slugify,
)


def test_mmss():
    assert mmss(0) == "00:00" and mmss(125_400) == "02:05" and mmss(3_665_000) == "61:05" and mmss(-5) == "00:00"


def test_slugify():
    assert slugify("My Career!") == "my-career"
    assert slugify("  ") == "interview"
    assert slugify("a" * 60) == "a" * 40
    assert slugify("Ünïcode & people") == "n-code-people"


def test_groups_by_speaker_and_gap():
    a = TranscriptAssembler()
    a.add_delta(AI, " Hi Rick", 2000, 2200)
    a.add_delta(AI, ", how are you?", 2200, 2600)
    a.add_delta(ME, " Good", 4000, 4200)
    a.add_delta(ME, " thanks.", 4300, 4500)
    a.add_delta(ME, " Anyway", 7100, 7300)  # gap 2,600 ms -> new turn
    assert a.turns() == [
        Turn(AI, 2000, 2600, "Hi Rick, how are you?"),
        Turn(ME, 4000, 4500, "Good thanks."),
        Turn(ME, 7100, 7300, "Anyway"),
    ]
    assert a.turns()[1].words == 2


def test_out_of_order_fragments_are_sorted():
    a = TranscriptAssembler()
    a.add_delta(ME, " world", 1200, 1400)
    a.add_delta(ME, " hello", 1000, 1200)
    assert a.turns() == [Turn(ME, 1000, 1400, "hello world")]


def test_interleaved_speakers_split_turns():
    a = TranscriptAssembler()
    a.add_delta(ME, " one", 0, 100)
    a.add_delta(AI, " mm", 150, 200)
    a.add_delta(ME, " two", 250, 300)
    assert [t.speaker for t in a.turns()] == [ME, AI, ME]


def test_notes_and_resume_offsets():
    a = TranscriptAssembler()
    a.add_delta(ME, " before", 1000, 2000)
    a.add_note("  typed thing ", 2500)
    offset = a.begin_resume()
    assert offset == 3500 and a.offset_ms == 3500
    a.add_delta(ME, " after", 100, 300)
    turns = a.turns()
    assert [t.speaker for t in turns] == [ME, NOTE, RESUME, ME]
    assert turns[1].text == "typed thing"
    assert turns[3].start_ms == 3600
    assert a.end_ms == 3800


def test_state_roundtrip():
    a = TranscriptAssembler()
    a.add_delta(ME, " hi", 0, 100)
    a.begin_resume()
    b = TranscriptAssembler.from_state(a.to_state())
    assert b.turns() == a.turns() and b.offset_ms == a.offset_ms


def test_empty_deltas_ignored():
    a = TranscriptAssembler()
    a.add_delta(ME, "", 0, 100)
    assert a.turns() == [] and a.end_ms == 0


def test_dialogue_for_claude():
    turns = [Turn(AI, 0, 100, "Question?"), Turn(ME, 2000, 3000, "Answer."), Turn(NOTE, 4000, 4000, "typed"),
             Turn(RESUME, 5000, 5000, ""), Turn(ME, 6000, 7000, "More.")]
    assert dialogue(turns) == (
        "Interviewer [00:00]: Question?\nMe [00:02]: Answer.\nTyped note [00:04]: typed\n— resumed —\nMe [00:06]: More."
    )
    assert dialogue(turns, limit=1) == "Me [00:06]: More."


def test_render_transcript():
    turns = [Turn(AI, 12_000, 13_000, "Hi?"), Turn(ME, 20_000, 21_000, "Hello."),
             Turn(NOTE, 21_400, 21_400, "Also X."), Turn(RESUME, 30_000, 30_000, ""), Turn(ME, 31_000, 32_000, "Back.")]
    md = render_transcript(topic="my career", day=date(2026, 10, 2), minutes=38, voice="marin",
                           model="claude-sonnet-5-5", notes_stem="interview-2026-10-02-my-career-notes", turns=turns)
    assert md == (
        "<!-- liber interview transcript -->\n"
        "# Interview — my career — 2026-10-02\n\n"
        "- Duration: 38 min · Voice: gpt-live-1 (marin) · Brain: claude-sonnet-5-5\n"
        "- Notes: [[interview-2026-10-02-my-career-notes]]\n\n"
        "**Interviewer** [00:12]: Hi?\n\n"
        "**Me** [00:20]: Hello.\n\n"
        "*(typed note)* [00:21]: Also X.\n\n"
        "— resumed —\n\n"
        "**Me** [00:31]: Back.\n"
    )
