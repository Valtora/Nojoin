"""Prompt text and composition for the AI analytics tier.

Composed from ``(heading, body)`` blocks with no substitution step, like every
other prompt in Nojoin: the speaker allowlist and the transcript are both
user-authored in practice, and a stray brace in either would otherwise fail at
generation time. See :mod:`backend.utils.prompt_blocks`.

The transcript is fenced as an attached document with an explicit
data-not-instructions rule, matching how meeting notes fence uploaded files. A
transcript is untrusted input: anyone who spoke in the meeting, or any tool that
pasted text into it, can write an instruction into the material this prompt
carries.
"""

from __future__ import annotations

from typing import Sequence

from backend.prompts import render_prompt
from backend.utils.languages import build_output_language_prompt_section
from backend.utils.prompt_blocks import render_prompt_blocks

# The value ``led_by`` takes when no single speaker drove a topic. Resolved
# after the speaker allowlist, so a speaker genuinely called "contested" still
# wins the name.
CONTESTED_LEADERSHIP = "contested"

MEETING_ANALYSIS_INTRO = render_prompt("analytics.intro")

MEETING_ANALYSIS_CRITICAL_RULES = render_prompt("analytics.critical_rules")

MEETING_ANALYSIS_TOPIC_RULES = render_prompt("analytics.topic_rules")

MEETING_ANALYSIS_SENTIMENT_RULES = render_prompt("analytics.sentiment_rules")

MEETING_ANALYSIS_QUESTION_RULES = render_prompt("analytics.question_rules")

MEETING_ANALYSIS_DECISION_RULES = render_prompt("analytics.decision_rules")

MEETING_ANALYSIS_JSON_SCHEMA = render_prompt("analytics.json_schema")

MEETING_ANALYSIS_CLOSING = render_prompt("analytics.closing")

# The transcript is untrusted input, fenced exactly as attached documents are.
TRANSCRIPT_FENCE_INSTRUCTION = render_prompt("analytics.transcript_fence_instruction")


def build_speakers_prompt_section(speaker_names: Sequence[str]) -> str:
    """The allowlist, rendered for the prompt.

    Names are the only identifiers the model is given, and the parser rejects
    anything not on this list, so the two must agree exactly.
    """
    names = [str(name).strip() for name in speaker_names if str(name).strip()]
    if not names:
        return ""
    lines = [render_prompt("analytics.speakers_intro")]
    lines.extend(f"- {name}" for name in names)
    return "\n".join(lines)


def build_transcript_prompt_section(transcript: str) -> str:
    return f"<meeting_transcript>\n{transcript.strip()}\n</meeting_transcript>"


def build_meeting_analysis_prompt_parts(
    *,
    transcript: str,
    speaker_names: Sequence[str],
    output_language_instruction: str | None = None,
    prompt_override: str | None = None,
) -> tuple[str, str]:
    """Compose the prompt as a cache-stable prefix and a per-meeting suffix.

    The prefix is identical for every meeting on an install, so a provider that
    supports prompt caching can reuse it; the suffix carries the speakers and
    the transcript. The two always concatenate to the whole prompt, so the model
    sees the same text either way.

    Returns ``("", prompt)`` for a caller-supplied override, which callers treat
    as "do not cache".
    """
    if prompt_override:
        return "", prompt_override

    prefix = render_prompt_blocks(
        [
            (None, MEETING_ANALYSIS_INTRO),
            (
                render_prompt("shared.heading_critical_rules"),
                MEETING_ANALYSIS_CRITICAL_RULES,
            ),
            (render_prompt("shared.heading_topics"), MEETING_ANALYSIS_TOPIC_RULES),
            (
                render_prompt("shared.heading_sentiment"),
                MEETING_ANALYSIS_SENTIMENT_RULES,
            ),
            (
                render_prompt("shared.heading_questions"),
                MEETING_ANALYSIS_QUESTION_RULES,
            ),
            (
                render_prompt("shared.heading_decisions"),
                MEETING_ANALYSIS_DECISION_RULES,
            ),
            (
                render_prompt("shared.heading_required_json_schema"),
                MEETING_ANALYSIS_JSON_SCHEMA,
            ),
            # Carries its own heading.
            (None, build_output_language_prompt_section(output_language_instruction)),
        ]
    )
    suffix = render_prompt_blocks(
        [
            (
                render_prompt("shared.heading_speakers"),
                build_speakers_prompt_section(speaker_names),
            ),
            (
                render_prompt("shared.heading_transcript"),
                render_prompt_blocks(
                    [
                        (None, TRANSCRIPT_FENCE_INSTRUCTION),
                        (None, build_transcript_prompt_section(transcript)),
                    ]
                ),
            ),
            (None, MEETING_ANALYSIS_CLOSING),
        ]
    )
    return f"{prefix}\n\n", f"{suffix}\n"


def build_meeting_analysis_prompt(
    *,
    transcript: str,
    speaker_names: Sequence[str],
    output_language_instruction: str | None = None,
    prompt_override: str | None = None,
) -> str:
    prefix, suffix = build_meeting_analysis_prompt_parts(
        transcript=transcript,
        speaker_names=speaker_names,
        output_language_instruction=output_language_instruction,
        prompt_override=prompt_override,
    )
    return f"{prefix}{suffix}"
