"""The holiday persona files: every shipped one loads, and a broken one is skipped.

Also the notes prompts for a meeting too long for one call, which are built
apart from the whole-meeting prompt and must carry the same tone.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from backend.utils.long_meeting_notes import (
    _Notes,
    build_merge_prompt,
    build_part_prompt,
    context_for_notes,
)
from backend.utils.meeting_notes import NotesPromptContext
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


@pytest.mark.parametrize("with_persona", [True, False], ids=["persona", "none"])
def test_a_long_meetings_part_and_merge_prompts_carry_the_notes_tone(
    with_persona: bool,
) -> None:
    pirate = persona_by_slug("pirate")
    assert pirate is not None
    active = ActivePersona(pirate, "command") if with_persona else None
    context = context_for_notes(
        "| SPEAKER_00 | Mira |", None, None, None, NotesPromptContext(persona=active)
    )
    part = TranscriptPart(
        text="[00:00:01] SPEAKER_00: Hello.", start="00:00", end="00:01"
    )
    notes = _Notes(text="## Summary\nHello.", first=1, last=1, start=None, end=None)

    prompts = [
        build_part_prompt(context, 1, 2, part),
        build_merge_prompt(context, [notes], 2, final=True),
    ]

    for prompt in prompts:
        if active is None:
            assert TONE_HEADING not in prompt
        else:
            section = f"{TONE_HEADING}\n{active.tone('notes')}"
            assert prompt.index("# Notes Format") < prompt.index(section)
