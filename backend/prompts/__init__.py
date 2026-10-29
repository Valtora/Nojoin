"""Shipped LLM prompt blocks, validated and cached on first use."""

from __future__ import annotations

import tomllib
from functools import cache
from pathlib import Path
from string import Template
from typing import NamedTuple


class PromptSpec(NamedTuple):
    file: str
    block: str
    placeholders: frozenset[str] = frozenset()


# Each block's substitution contract is owned by code, independently of its prose.
# Rows group blocks that have exactly the same placeholder contract.
_CONTRACTS = (
    (
        "shared",
        "notes_legend no_user_notes documents_preamble note_file_explanation "
        "no_documents document_truncated no_calendar_event "
        "duration_under_minute no_metadata no_glossary glossary_notes_usage "
        "glossary_edge_usage heading_critical_rules heading_title_style "
        "heading_unresolved_speaker_labels heading_recording_metadata "
        "heading_glossary heading_user_notes_context heading_meeting_context "
        "heading_attached_documents heading_required_json_schema "
        "heading_notes_markdown_requirements heading_transcript "
        "heading_speaker_mapping heading_speaker_rules heading_notes_format "
        "heading_this_part heading_transcript_part heading_title_rules "
        "heading_speaker_names_to_settle heading_combining_rules "
        "heading_notes_of_the_parts meeting_wide_notes_preamble "
        "inline_notes_explanation notes_list_preamble language_british "
        "language_american language_transcript language_english "
        "heading_transcript_to_analyze heading_eligible_diarization_labels "
        "heading_transcript_spaced heading_speakers "
        "heading_technical_context_policy heading_earlier_context_summary "
        "heading_previously_suggested heading_user_focus "
        "heading_recent_transcript heading_topics heading_sentiment "
        "heading_questions heading_decisions",
        "",
    ),
    (
        "notes",
        "preamble default_sections table_rules fidelity_rules "
        "intelligence_intro intelligence_rules intelligence_schema title_short "
        "title_descriptive intro closing title_intro title_closing "
        "document_visual_origin document_ocr_origin document_text_origin",
        "",
    ),
    ("shared", "calendar_title recording_title", "title"),
    ("notes", "document_page_label", "number"),
    ("shared", "calendar_description", "description"),
    ("shared", "calendar_attendees", "attendee_list"),
    ("shared", "duration_hours", "hours plural_suffix"),
    ("shared", "duration_minutes", "minutes plural_suffix"),
    ("shared", "recorded_on", "recorded_on"),
    ("shared", "duration", "duration"),
    ("shared", "participants", "participants"),
    ("shared", "glossary", "usage cleaned"),
    (
        "speakers",
        "no_unresolved unresolved_intro all_eligible eligible_intro "
        "suggestion_intro suggestion_rules suggestion_schema mapping_header",
        "",
    ),
    (
        "long_meeting",
        "part_intro use_speaker_mapping part_intro_speakers part_speaker_rules "
        "part_closing part_closing_speakers merge_intro merge_intro_title "
        "merge_title_rules merge_labels_kept settle_speakers merge_closing "
        "merge_closing_title merge_closing_title_speakers labels_intro "
        "whole_meeting",
        "",
    ),
    ("long_meeting", "part_span", "start end"),
    ("long_meeting", "part_length merge_length", "words"),
    ("long_meeting", "part_instructions", "index total span length"),
    ("long_meeting", "single_part", "first total"),
    ("long_meeting", "multiple_parts", "first last total"),
    ("long_meeting", "described_span", "parts start end"),
    ("long_meeting", "combining_rules", "scope length"),
    ("long_meeting", "limited_closing", "markdown words"),
    ("long_meeting", "stretch", "parts"),
    ("long_meeting", "settlement", "SETTLE_SPEAKERS settle"),
    ("shared", "language_named", "language"),
    ("shared", "language_custom", "custom_instruction"),
    ("shared", "output_language", "normalized"),
    ("repair", "json", "validation_error original_prompt invalid_response"),
    (
        "chat",
        "no_transcript excerpts_preamble no_excerpts_preamble "
        "capabilities_with_notes_tool capabilities_without_tools sources_notes "
        "sources_live sources_excerpts sources_full live_trimmed_scope "
        "live_scope live_no_speech notes_tool_description "
        "notes_tool_content_description attached_document_label "
        "text_layer_origin visual_origin ocr_origin",
        "",
    ),
    ("chat", "live_transcript", "scope transcript"),
    ("chat", "full_transcript", "transcript"),
    ("chat", "context", "sources capabilities meeting_notes transcript_section"),
    ("chat", "excerpt_transcript", "preamble transcript"),
    ("chat", "question", "question"),
    ("chat", "no_excerpt_transcript", "preamble"),
    (
        "meeting_edge",
        "intro critical_rules json_schema context_level_1 context_level_2 "
        "context_level_3 context_level_4 context_level_5 no_rolling_summary "
        "no_previous_suggestions no_focus previous_questions_label "
        "previous_points_label",
        "",
    ),
    (
        "analytics",
        "intro critical_rules topic_rules sentiment_rules question_rules "
        "decision_rules json_schema closing transcript_fence_instruction "
        "speakers_intro",
        "",
    ),
    ("vision", "page", ""),
    ("vision", "text_layer", "text"),
    ("vision", "figure", "location"),
    ("vision", "slide_location", "title"),
    ("vision", "page_location", "number"),
    ("cli", "system history_heading assistant_label user_label", ""),
    (
        "structure",
        "instructions",
        "max_notes_template_name_length max_notes_template_description_length "
        "max_notes_sections_length default_notes_sections brief",
    ),
    ("chat", "recording_label", "id"),
    ("chat", "document_page_title_label", "label page title"),
    ("chat", "document_page_label", "label page"),
    ("chat", "related_transcript", "recording content"),
    ("chat", "retrieved_context_question", "context question"),
    ("chat", "retrieved_document", "recording location origin content"),
)


def _build_registry() -> dict[str, PromptSpec]:
    registry: dict[str, PromptSpec] = {}
    declarations: dict[str, str] = {}
    for row, (feature, blocks, names) in enumerate(_CONTRACTS):
        for position, block in enumerate(blocks.split()):
            prompt_id = f"{feature}.{block}"
            spec = PromptSpec(f"{feature}.toml", block, frozenset(names.split()))
            declaration = f"_CONTRACTS[{row}] block {position}: {spec!r}"
            if prompt_id in declarations:
                raise ValueError(
                    f"{prompt_id}: duplicate prompt id; "
                    f"first declaration {declarations[prompt_id]}; "
                    f"second declaration {declaration}"
                )
            declarations[prompt_id] = declaration
            registry[prompt_id] = spec
    return registry


REGISTRY = _build_registry()

PROMPT_DIRECTORY = Path(__file__).resolve().parent


def _validate_template(prompt_id: str, text: object, spec: PromptSpec) -> Template:
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{prompt_id}: prompt block is missing or empty")
    template = Template(text)
    if not template.is_valid():
        raise ValueError(f"{prompt_id}: invalid Template placeholder syntax")
    actual = frozenset(template.get_identifiers())
    if actual != spec.placeholders:
        raise ValueError(
            f"{prompt_id}: placeholder mismatch; "
            f"expected {sorted(spec.placeholders)}, found {sorted(actual)}"
        )
    return template


@cache
def load_registry(directory: Path = PROMPT_DIRECTORY) -> dict[str, Template]:
    """Load every registered block together, failing with the offending id."""
    files: dict[str, dict] = {}
    templates: dict[str, Template] = {}
    for prompt_id, spec in REGISTRY.items():
        if spec.file not in files:
            try:
                with (directory / spec.file).open("rb") as source:
                    files[spec.file] = tomllib.load(source)
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                raise ValueError(
                    f"{prompt_id}: cannot load {spec.file}: {exc}"
                ) from exc
        templates[prompt_id] = _validate_template(
            prompt_id, files[spec.file].get(spec.block), spec
        )
    return templates


def render_prompt(prompt_id: str, **values: str) -> str:
    """Substitute once; replacement text is opaque, including braces and dollars."""
    template = load_registry()[prompt_id]
    expected = REGISTRY[prompt_id].placeholders
    if values.keys() != expected:
        raise ValueError(
            f"{prompt_id}: expected values {sorted(expected)}, got {sorted(values)}"
        )
    return template.substitute(values)
