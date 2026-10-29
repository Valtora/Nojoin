"""The holiday persona files: every shipped one loads, and a broken one is skipped.

Also where the notes tone sits in every notes prompt builder: the standalone
and unified prompts, and the part and merge prompts for a meeting too long
for one call. It comes before all of the meeting's own content.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from backend.processing.llm_backends.base import LLMBackend
from backend.utils.long_meeting_notes import (
    _Notes,
    build_merge_prompt,
    build_part_prompt,
    context_for_notes,
)
from backend.utils.meeting_intelligence import (
    AutomaticMeetingIntelligenceRequest,
    build_automatic_meeting_intelligence_prompt,
)
from backend.utils.meeting_notes import AttachedDocument, NotesPromptContext
from backend.utils.personas import (
    PERSONAS_DIR,
    SURFACES,
    TONE_HEADING,
    ActivePersona,
    load_personas,
    persona_by_slug,
)
from backend.utils.transcript_parts import TranscriptPart

GOOD = """
name = "Talk Like a Pirate Day"
slug = "{slug}"
order = 10
date = "09-19"
tooltip = "Talk Like a Pirate Day: a pirate."
chat = "Say ahoy."
edge = "One nautical word."
notes = "A pirate flavour."
"""


def test_every_shipped_persona_file_loads() -> None:
    files = sorted(path.stem for path in PERSONAS_DIR.glob("*.toml"))

    loaded = load_personas(PERSONAS_DIR)

    assert len(files) == 33
    assert sorted(persona.slug for persona in loaded) == files


@pytest.mark.parametrize(
    ("name", "content", "problem"),
    [
        ("bad-toml", 'name = "unclosed', "Unterminated string"),
        (
            "no-chat",
            GOOD.format(slug="no-chat").replace('chat = "Say ahoy."', ""),
            "chat",
        ),
        (
            "bad-rule",
            GOOD.format(slug="bad-rule").replace(
                'date = "09-19"', 'rule = "last Friday of July"'
            ),
            "not understood",
        ),
        (
            "both",
            GOOD.format(slug="both") + 'rule = "first Monday of May"\n',
            "exactly one",
        ),
        ("bad-date", GOOD.format(slug="bad-date").replace("09-19", "13-40"), "month"),
        ("renamed", GOOD.format(slug="pirate"), "match the file name"),
    ],
)
def test_a_broken_persona_file_is_skipped_with_a_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    name: str,
    content: str,
    problem: str,
) -> None:
    (tmp_path / "good.toml").write_text(GOOD.format(slug="good"), encoding="utf-8")
    (tmp_path / f"{name}.toml").write_text(content, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="backend.utils.personas"):
        loaded = load_personas(tmp_path)

    assert [persona.slug for persona in loaded] == ["good"]
    assert len(caplog.records) == 1
    assert f"{name}.toml" in caplog.text and problem in caplog.text


def test_every_persona_tone_carries_the_safety_line() -> None:
    # Pinned as text, not the constant, so rewording it away fails here.
    safety = (
        "The persona affects tone only and must never change facts, numbers, "
        "names or quotes from the meeting."
    )
    for persona in load_personas(PERSONAS_DIR):
        for surface in SURFACES:
            tone = ActivePersona(persona, "dated").tone(surface)
            assert tone.startswith(persona.line(surface))
            assert safety in tone


USER_NOTE = "PRIVATE USER NOTE"
DOCUMENT = "PRIVATE DOCUMENT CONTENT"
SPOKEN = "SPEAKER_00: Hello."


def _pirate() -> ActivePersona:
    pirate = persona_by_slug("pirate")
    assert pirate is not None
    return ActivePersona(pirate, "command")


def _assert_tone_before(prompt: str, section: str, *content: str) -> None:
    assert prompt.count(TONE_HEADING) == 1
    for text in content:
        assert prompt.index(section) < prompt.index(text), text


def test_the_standalone_and_unified_notes_prompts_put_the_tone_before_the_meeting() -> (
    None
):
    active = _pirate()
    documents = [AttachedDocument(title="Agenda", text=DOCUMENT)]
    standalone = LLMBackend.build_notes_prompt(
        None,
        f"[00:00:01] {SPOKEN}",
        {},
        user_notes=USER_NOTE,
        notes_context=NotesPromptContext(persona=active, documents=documents),
    )
    request = AutomaticMeetingIntelligenceRequest(
        resolved_transcript=f"[00:00:01] {SPOKEN}",
        unresolved_speakers=("SPEAKER_00",),
        user_notes=USER_NOTE,
        documents=documents,
        tone=active.tone("notes"),
    )
    unified = build_automatic_meeting_intelligence_prompt(request)

    section = f"{TONE_HEADING}\n{active.tone('notes')}"
    _assert_tone_before(standalone, section, USER_NOTE, DOCUMENT, SPOKEN)
    _assert_tone_before(unified, section, USER_NOTE, DOCUMENT, SPOKEN)


@pytest.mark.parametrize("with_persona", [True, False], ids=["persona", "none"])
def test_a_long_meetings_part_and_merge_prompts_carry_the_notes_tone(
    with_persona: bool,
) -> None:
    active = _pirate() if with_persona else None
    documents = [AttachedDocument(title="Agenda", text=DOCUMENT)]
    context = context_for_notes(
        "| SPEAKER_00 | Mira |",
        USER_NOTE,
        None,
        None,
        NotesPromptContext(persona=active, documents=documents),
    )
    part = TranscriptPart(
        text="[00:00:01] SPEAKER_00: Hello.", start="00:00", end="00:01"
    )
    notes = _Notes(text="## Summary\nHello.", first=1, last=1, start=None, end=None)

    draft = build_part_prompt(context, 1, 2, part)
    merge = build_merge_prompt(context, [notes], 2, final=True)

    if active is None:
        assert TONE_HEADING not in draft + merge
    else:
        section = f"{TONE_HEADING}\n{active.tone('notes')}"
        _assert_tone_before(draft, section, USER_NOTE, DOCUMENT, SPOKEN)
        # The merge reads the parts' notes, not the documents or transcript.
        _assert_tone_before(merge, section, USER_NOTE, "## Summary\nHello.")
