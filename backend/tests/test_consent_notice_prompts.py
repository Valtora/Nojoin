"""Every notes and live guidance path carries the shared procedure rule."""

from backend.processing.llm_backends.base import LLMBackend
from backend.utils.long_meeting_notes import (
    _Notes,
    build_merge_prompt,
    build_part_prompt,
    context_for_notes,
)
from backend.utils.meeting_analysis.prompt import build_meeting_analysis_prompt_parts
from backend.utils.meeting_edge import (
    MeetingEdgeRequest,
    build_meeting_edge_prompt_parts,
)
from backend.utils.meeting_intelligence import (
    AutomaticMeetingIntelligenceRequest,
    build_automatic_meeting_intelligence_prompt,
)
from backend.utils.prompt_blocks import RECORDING_NOTICE_PROCEDURE
from backend.utils.transcript_parts import split_transcript


def test_procedure_rule_is_composed_once_in_each_generation_path() -> None:
    transcript = "Alex: Does everyone agree to being recorded?\nSam: Yes."
    context = context_for_notes("", None, None, None, None)
    part = split_transcript(transcript, 5000)[0]
    edge_prefix, edge_suffix = build_meeting_edge_prompt_parts(
        MeetingEdgeRequest(recent_transcript=transcript)
    )
    analysis_prefix, analysis_suffix = build_meeting_analysis_prompt_parts(
        transcript=transcript, speaker_names=["Alex", "Sam"]
    )
    prompts = [
        LLMBackend.build_notes_prompt(None, transcript, {}),
        LLMBackend.build_title_prompt(None, transcript),
        build_automatic_meeting_intelligence_prompt(
            AutomaticMeetingIntelligenceRequest(
                resolved_transcript=transcript, unresolved_speakers=()
            )
        ),
        build_part_prompt(context, 1, 1, part),
        build_merge_prompt(context, [_Notes("Notes", 1, 1, None, None)], 1, final=True),
        edge_prefix + edge_suffix,
        analysis_prefix + analysis_suffix,
    ]
    for prompt in prompts:
        assert prompt.count(RECORDING_NOTICE_PROCEDURE) == 1
    assert RECORDING_NOTICE_PROCEDURE in edge_prefix
    assert RECORDING_NOTICE_PROCEDURE in analysis_prefix
    assert transcript in edge_suffix
