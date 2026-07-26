"""Tests for user-editable meeting-notes structures (issue #137).

Three properties matter more than the rest and are covered first: a custom
structure can never break prompt rendering, it can never remove the protected
rules, and resolution always degrades to something usable rather than failing a
meeting's notes.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session

# Registers every mapper, which the notes_templates -> users foreign key and
# User's own relationships both need before a query can compile.
import backend.models.registry  # noqa: F401
from backend.models.notes_template import NotesTemplate, NotesTemplateScope
from backend.processing.llm_backends.base import LLMBackend
from backend.tests.sqlite_schemas import NOTES_TEMPLATES_SCHEMA, USERS_SCHEMA
from backend.utils.meeting_intelligence import (
    AutomaticMeetingIntelligenceRequest,
    AutomaticMeetingIntelligenceResult,
    MeetingIntelligenceContractError,
    build_automatic_meeting_intelligence_prompt,
)
from backend.utils.meeting_notes import (
    DEFAULT_NOTES_SECTIONS,
    NOTES_BODY_SPEC,
    NOTES_FIDELITY_RULES,
    NOTES_SPEC_PREAMBLE,
    NOTES_TABLE_RULES,
    MeetingMetadata,
    NotesPromptContext,
    build_notes_body_spec,
    format_duration_for_prompt,
)
from backend.utils.notes_templates import (
    NotesTemplateError,
    build_meeting_metadata,
    builtin_notes_template,
    merge_glossaries,
    resolve_glossary,
    resolve_notes_template,
    validate_glossary,
    validate_notes_sections,
)

CUSTOM_SECTIONS = """## Questions Asked
Every question the interviewer put to the participant.

## Observations
What the participant did, in the order they did it."""


@pytest.fixture()
def session():
    # Hand-written DDL rather than create_all: users.settings is JSONB, which
    # SQLite cannot render, and notes_templates.user_id references it.
    engine = create_engine("sqlite://", future=True)
    with engine.begin() as connection:
        connection.execute(text(USERS_SCHEMA))
        connection.execute(text(NOTES_TEMPLATES_SCHEMA))
    with Session(engine) as db_session:
        yield db_session
    engine.dispose()


def test_default_body_spec_is_unchanged_by_the_split():
    """No custom structure means a byte-identical prompt to the pre-feature one."""
    assert build_notes_body_spec() == NOTES_BODY_SPEC
    assert build_notes_body_spec(None) == NOTES_BODY_SPEC
    assert build_notes_body_spec("   ") == NOTES_BODY_SPEC
    assert DEFAULT_NOTES_SECTIONS in NOTES_BODY_SPEC


def test_custom_sections_keep_every_protected_part():
    body = build_notes_body_spec(CUSTOM_SECTIONS)

    assert NOTES_SPEC_PREAMBLE in body
    assert NOTES_TABLE_RULES in body
    assert NOTES_FIDELITY_RULES in body
    assert "## Questions Asked" in body
    # The shipped structure is genuinely replaced, not appended to.
    assert "## Key Decisions" not in body


def test_braces_in_a_custom_structure_survive_prompt_rendering():
    """A user typing JSON or a placeholder must not break generation.

    The assembled template is passed through str.format, so an unescaped brace
    would raise KeyError at generation time -- long after the template was saved.
    """
    sections = '## Payload\nRecord any JSON such as {"id": 1} verbatim.'
    request = AutomaticMeetingIntelligenceRequest(
        resolved_transcript="[00:00 - 00:05] Sam: Hello.",
        unresolved_speakers=(),
        notes_sections=sections,
    )

    prompt = build_automatic_meeting_intelligence_prompt(request)

    assert '{"id": 1}' in prompt
    # Every doubled brace collapses during rendering; a leftover pair would mean
    # the escaping ran twice, and a raised KeyError would mean it never ran.
    assert "{{" not in prompt


def test_a_structure_of_pure_braces_still_renders():
    """The pathological case for the old str.format assembly.

    Nothing escapes or substitutes any more, so braces are simply characters.
    """
    sections = "## Payload\n{{{ }}} {name} {0} {} %s ${x}"
    body = build_notes_body_spec(sections)

    assert sections in body


def test_notes_prompt_includes_metadata_and_glossary():
    prompt = LLMBackend.build_notes_prompt(
        None,
        "[00:00 - 00:05] Sam: Hello.",
        {"SPEAKER_00": "Sam"},
        None,
        None,
        None,
        NotesPromptContext(
            glossary="ARR: annual recurring revenue",
            metadata=MeetingMetadata(
                title="Weekly sync",
                recorded_on="Monday 06 April 2026, 09:00",
                duration="45 minutes",
                participants=["Sam", "Priya"],
            ),
        ),
    )

    assert "Recording title: Weekly sync" in prompt
    assert "Duration: 45 minutes" in prompt
    assert "Participants: Sam, Priya" in prompt
    assert "ARR: annual recurring revenue" in prompt


def test_glossary_wording_differs_between_notes_and_meeting_edge():
    """Notes must not grow a glossary section; Meeting Edge exists to explain terms."""
    notes_prompt = LLMBackend.build_notes_prompt(
        None,
        "[00:00 - 00:05] Sam: Hello.",
        {},
        None,
        None,
        None,
        NotesPromptContext(glossary="ARR: annual recurring revenue"),
    )
    assert "Do not add a glossary section to the notes" in notes_prompt


def test_both_notes_paths_embed_the_default_body_spec():
    request = AutomaticMeetingIntelligenceRequest(
        resolved_transcript="[00:00 - 00:05] Sam: Hello.",
        unresolved_speakers=(),
    )

    assert NOTES_BODY_SPEC in build_automatic_meeting_intelligence_prompt(request)
    assert NOTES_BODY_SPEC in LLMBackend.build_notes_prompt(
        None, "[00:00 - 00:05] Sam: Hello.", {}
    )


def test_merge_glossaries_is_additive_with_personal_winning():
    merged = merge_glossaries(
        "ARR: annual recurring revenue\nNojoin: the product",
        "ARR: annualised run rate\nDRI: directly responsible individual",
    )

    lines = merged.splitlines()
    # Install ordering is preserved, the personal definition replaces in place,
    # and purely personal terms are appended rather than dropped.
    assert lines == [
        "ARR: annualised run rate",
        "Nojoin: the product",
        "DRI: directly responsible individual",
    ]


def test_merge_glossaries_handles_one_sided_and_empty_inputs():
    assert merge_glossaries(None, None) == ""
    assert merge_glossaries("ARR: x", None) == "ARR: x"
    assert merge_glossaries(None, "ARR: x") == "ARR: x"


def test_resolve_glossary_reads_both_tiers_from_merged_settings():
    resolved = resolve_glossary(
        {
            "install_glossary_terms": "ARR: annual recurring revenue",
            "glossary_terms": "DRI: directly responsible individual",
        }
    )
    assert "ARR" in resolved and "DRI" in resolved


def test_validate_notes_sections_rejects_structures_that_cannot_work():
    with pytest.raises(NotesTemplateError):
        validate_notes_sections("")
    with pytest.raises(NotesTemplateError):
        validate_notes_sections("just prose with no headings at all")
    with pytest.raises(NotesTemplateError):
        validate_notes_sections("## Heading\n" + "x" * 9000)

    # Braces are explicitly allowed: escaping makes them safe.
    assert validate_notes_sections('## Payload\nRecord {"a": 1}.').startswith("##")


def test_validate_glossary_allows_empty_but_rejects_control_characters():
    assert validate_glossary(None) == ""
    with pytest.raises(NotesTemplateError):
        validate_glossary("ARR\x00: annual recurring revenue")


def _make_template(session, **kwargs) -> NotesTemplate:
    template = NotesTemplate(
        name=kwargs.pop("name", "Interview notes"),
        sections=kwargs.pop("sections", CUSTOM_SECTIONS),
        scope=kwargs.pop("scope", NotesTemplateScope.PERSONAL.value),
        user_id=kwargs.pop("user_id", 1),
        **kwargs,
    )
    session.add(template)
    session.commit()
    session.refresh(template)
    return template


def test_resolution_order_prefers_the_explicit_choice(session):
    personal = _make_template(session, name="Personal")
    install = _make_template(
        session,
        name="Install",
        scope=NotesTemplateScope.INSTALL.value,
        user_id=None,
    )
    settings = {
        "notes_template_id": personal.id,
        "install_notes_template_id": install.id,
    }

    chosen = resolve_notes_template(
        session, settings, user_id=1, explicit_template_id=install.id
    )
    assert chosen.template_id == install.id

    chosen = resolve_notes_template(session, settings, user_id=1)
    assert chosen.template_id == personal.id

    chosen = resolve_notes_template(
        session, {"install_notes_template_id": install.id}, user_id=1
    )
    assert chosen.template_id == install.id


def test_resolution_degrades_to_builtin_rather_than_failing(session):
    # A deleted template, and another user's private template, both fall through
    # instead of stopping a meeting's notes from being generated.
    other_users_template = _make_template(session, user_id=2)

    assert (
        resolve_notes_template(
            session, {"notes_template_id": 9999}, user_id=1
        ).template_id
        is None
    )
    assert (
        resolve_notes_template(
            session, {"notes_template_id": other_users_template.id}, user_id=1
        ).template_id
        is None
    )
    assert resolve_notes_template(session, {}, user_id=1).sections == (
        builtin_notes_template().sections
    )


def test_install_templates_are_visible_to_every_user(session):
    install = _make_template(
        session,
        scope=NotesTemplateScope.INSTALL.value,
        user_id=None,
    )
    resolved = resolve_notes_template(
        session, {"notes_template_id": install.id}, user_id=42
    )
    assert resolved.template_id == install.id


def test_a_template_matching_the_builtin_is_not_treated_as_custom(session):
    template = _make_template(session, sections=DEFAULT_NOTES_SECTIONS)
    resolved = resolve_notes_template(
        session, {"notes_template_id": template.id}, user_id=1
    )
    assert resolved.is_custom is False
    assert builtin_notes_template().is_custom is False


def test_opening_heading_contract_is_relaxed_only_for_custom_structures():
    # The rule is the built-in structure's own first line, so a user-written
    # structure that opens with a different heading level must still validate.
    result = AutomaticMeetingIntelligenceResult(
        speaker_mapping={},
        title="Interview with Priya",
        notes_markdown="### Questions Asked\n- What broke first?",
        require_section_heading=False,
    )
    assert result.notes_markdown.startswith("### ")

    with pytest.raises(MeetingIntelligenceContractError):
        AutomaticMeetingIntelligenceResult(
            speaker_mapping={},
            title="Interview with Priya",
            notes_markdown="### Questions Asked\n- What broke first?",
        )


def test_meeting_metadata_drops_placeholder_speaker_names():
    class _Recording:
        name = "Weekly sync"
        created_at = None
        duration_seconds = 3720

    class _Speaker:
        def __init__(self, name):
            self.local_name = name
            self.diarization_label = "SPEAKER_00"
            self.global_speaker = None

    metadata = build_meeting_metadata(
        _Recording(),
        [_Speaker("Sam"), _Speaker("SPEAKER_01"), _Speaker("Unknown")],
    )

    assert metadata.participants == ["Sam"]
    assert metadata.duration == "1 hour 2 minutes"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (None, None),
        (0, None),
        (30, "under a minute"),
        (60, "1 minute"),
        (3600, "1 hour"),
        (5400, "1 hour 30 minutes"),
    ],
)
def test_format_duration_for_prompt(seconds, expected):
    assert format_duration_for_prompt(seconds) == expected


def test_template_description_is_optional_and_bounded():
    from backend.utils.notes_templates import validate_notes_template_description

    assert validate_notes_template_description(None) is None
    assert validate_notes_template_description("   ") is None
    assert (
        validate_notes_template_description("  Interview notes  ") == "Interview notes"
    )
    with pytest.raises(NotesTemplateError):
        validate_notes_template_description("x" * 201)


def test_generator_prompt_carries_the_brief_and_bans_the_protected_parts():
    from backend.utils.notes_structure_generator import (
        build_notes_structure_generator_prompt,
    )

    prompt = build_notes_structure_generator_prompt(
        "  Weekly user interviews; questions, observations, follow-ups.  "
    )

    assert "Weekly user interviews" in prompt
    # The brief is user text and the instructions contain literal JSON braces, so
    # nothing here may be a format placeholder.
    assert "{brief}" not in prompt
    assert "never invent facts" in prompt.lower()
    assert "Placeholders of any kind" in prompt
    # The shipped structure rides along as a style reference.
    assert "## Summary" in prompt


def test_generated_structure_is_validated_like_a_hand_written_one():
    from backend.utils.notes_structure_generator import (
        parse_generated_notes_structure,
    )

    # Raw string: the newline inside the JSON value must reach the parser as the
    # two-character escape it would be on the wire, not as a real newline (which
    # is an invalid control character inside a JSON string).
    result = parse_generated_notes_structure(
        r"""Sure! Here you go:
```json
{"name": "Interview notes",
 "description": "Questions, observations and follow-ups.",
 "sections": "## Questions Asked\nEvery question put to the participant."}
```"""
    )

    assert result.name == "Interview notes"
    assert result.sections.startswith("## Questions Asked")

    # A model that returns prose, or a structure with no headings, must fail here
    # rather than at generation time on a real meeting.
    with pytest.raises(NotesTemplateError):
        parse_generated_notes_structure("I would suggest starting with a summary.")
    with pytest.raises(NotesTemplateError):
        parse_generated_notes_structure(
            '{"name": "X", "description": "", "sections": "just prose"}'
        )


def test_generator_brief_is_bounded():
    from backend.utils.notes_structure_generator import validate_generator_brief

    assert validate_generator_brief("  interviews  ") == "interviews"
    with pytest.raises(NotesTemplateError):
        validate_generator_brief("   ")
    with pytest.raises(NotesTemplateError):
        validate_generator_brief("x" * 1501)
