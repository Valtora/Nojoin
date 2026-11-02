"""Shipped LLM prompt blocks, validated and cached on first use."""

from __future__ import annotations

import hashlib
import logging
import os
import tomllib
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from string import Template
from typing import Any, NamedTuple


class PromptSpec(NamedTuple):
    file: str
    block: str
    placeholders: frozenset[str] = frozenset()
    required_literals: tuple[str, ...] = ()


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


# Output keys, tags, enum values and copied-example sentinels consumed by parsers.
_REQUIRED_LITERALS = {
    "notes.default_sections": ("##",),
    "notes.intelligence_rules": ("speaker_mapping", "notes_markdown", "title"),
    "notes.intelligence_schema": (
        '"speaker_mapping"',
        '"title"',
        '"notes_markdown"',
        "##",
    ),
    "speakers.no_unresolved": ("speaker_mapping",),
    "speakers.unresolved_intro": ("speaker_mapping",),
    "speakers.suggestion_rules": ("confidence", "0.0", "1.0"),
    "speakers.suggestion_schema": (
        '"suggestions"',
        '"diarization_label"',
        '"suggested_name"',
        '"confidence"',
        '"rationale"',
        '"signals"',
        '"evidence_spans"',
        '"quote"',
        '"reason"',
        '"start_seconds"',
        '"end_seconds"',
    ),
    "meeting_edge.intro": (
        "summary",
        "rolling_summary",
        "questions",
        "points",
        "concepts",
    ),
    "meeting_edge.critical_rules": ("questions", "points"),
    "meeting_edge.json_schema": (
        '"summary"',
        '"rolling_summary"',
        '"questions"',
        '"points"',
        '"concepts"',
        '"term"',
        '"explanation"',
    ),
    "analytics.critical_rules": ("speaker",),
    "analytics.topic_rules": ("led_by", '"contested"'),
    "analytics.sentiment_rules": (
        "tone",
        '"positive"',
        '"negative"',
        '"neutral"',
        '"mixed"',
        "citations",
    ),
    "analytics.question_rules": ("answered_by", "answer_summary", "null"),
    "analytics.decision_rules": (
        "consensus",
        '"stated"',
        '"assumed"',
        '"none"',
        "agreed_by",
        "objected_by",
        "citations",
    ),
    "analytics.json_schema": (
        '"topics"',
        '"title"',
        '"start_seconds"',
        '"end_seconds"',
        '"summary"',
        '"led_by"',
        '"leadership_basis"',
        '"sentiment"',
        '"speaker"',
        '"tone"',
        '"mixed"',
        '"citations"',
        '"quote"',
        '"questions"',
        '"question"',
        '"asked_by"',
        '"asked_at_seconds"',
        '"answered_by"',
        '"answered_at_seconds"',
        '"answer_summary"',
        '"decisions"',
        '"decision"',
        '"proposed_by"',
        '"agreed_by"',
        '"objected_by"',
        '"consensus"',
        '"stated"',
    ),
    "analytics.transcript_fence_instruction": ("<meeting_transcript>",),
    "structure.instructions": ('"name"', '"description"', '"sections"', "#"),
    "long_meeting.part_speaker_rules": (
        "<speaker_names>",
        "</speaker_names>",
        "Person name or role",
        ":",
    ),
    "long_meeting.part_closing_speakers": ("<speaker_names>",),
    "long_meeting.merge_title_rules": (
        "<title>",
        "</title>",
    ),
    "long_meeting.settle_speakers": (
        "<speaker_names>",
        "</speaker_names>",
        "One of the names listed for SPEAKER_00",
        ":",
    ),
    "long_meeting.merge_closing_title": ("<title>",),
    "long_meeting.merge_closing_title_speakers": ("<title>", "<speaker_names>"),
}


def _build_registry() -> dict[str, PromptSpec]:
    registry: dict[str, PromptSpec] = {}
    declarations: dict[str, str] = {}
    for row, (feature, blocks, names) in enumerate(_CONTRACTS):
        for position, block in enumerate(blocks.split()):
            prompt_id = f"{feature}.{block}"
            spec = PromptSpec(
                f"{feature}.toml",
                block,
                frozenset(names.split()),
                _REQUIRED_LITERALS.get(prompt_id, ()),
            )
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
    for literal in spec.required_literals:
        if literal not in text:
            raise ValueError(f"{prompt_id}: missing required literal {literal!r}")
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
    if developer_mode_enabled():
        # Deferred to avoid the config -> LLM backends -> prompts import cycle.
        from backend.utils.config_manager import config_manager

        config_manager.reload()
        overrides = config_manager.get("prompt_overrides", {})
        override = overrides.get(prompt_id) if isinstance(overrides, Mapping) else None
        if override is not None:
            try:
                template = validate_override(prompt_id, _override_text(override))
            except ValueError as exc:
                logging.getLogger(__name__).warning("Default used: %s", exc)
    return template.substitute(values)


def developer_mode_enabled() -> bool:
    """Only the process environment can enable developer mode."""
    return os.environ.get("NOJOIN_DEVELOPER_MODE", "").strip().lower() == "true"


def validate_override(prompt_id: str, text: object) -> Template:
    """Use the shipped block's code-owned contract for edits and stored text."""
    return _validate_template(prompt_id, text, REGISTRY[prompt_id])


def default_sha256(prompt_id: str) -> str:
    """Hash the shipped block, including its literal placeholder syntax."""
    return hashlib.sha256(
        load_registry()[prompt_id].template.encode("utf-8")
    ).hexdigest()


def _override_text(override: object) -> object:
    return override.get("text") if isinstance(override, Mapping) else None


def prompt_catalogue_entry(prompt_id: str, override: object) -> dict[str, Any]:
    """Describe an edit without discarding invalid or outdated saved text."""
    spec = REGISTRY[prompt_id]
    status = "default"
    if override is not None:
        try:
            validate_override(prompt_id, _override_text(override))
            status = (
                "overridden"
                if isinstance(override, Mapping)
                and override.get("base_sha256") == default_sha256(prompt_id)
                else "default changed"
            )
        except ValueError:
            status = "invalid"
    text = _override_text(override)
    return {
        "id": prompt_id,
        "feature": prompt_id.split(".", 1)[0],
        "placeholders": sorted(spec.placeholders),
        "required_literals": list(spec.required_literals),
        "default_text": load_registry()[prompt_id].template,
        "override_text": text if isinstance(text, str) else None,
        "status": status,
    }
