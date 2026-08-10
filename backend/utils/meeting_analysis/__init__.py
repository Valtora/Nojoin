"""The AI analytics tier: topics, sentiment, questions, and decision ownership.

Optional by construction. An install with no AI provider shows the
deterministic and delivery tiers exactly as before and reports this one as
``unavailable``, which is a normal state rather than an error.

Sentiment here is a reading of the *words* and nothing else. There is
deliberately no emotion model anywhere in Nojoin, and the measured delivery
descriptors are not a second sentiment reading: the two are presented as
separate, differently-sourced things and must never be fused into one score.
"""

from .contract import (
    MEETING_ANALYSIS_METHOD_VERSION,
    AnalysisCitation,
    AnalysisDecision,
    AnalysisQuestion,
    AnalysisSentiment,
    AnalysisTopic,
    ExclusionCounts,
    MeetingAnalysisContractError,
    MeetingAnalysisRequest,
    MeetingAnalysisResult,
    QuoteIndex,
    SpeakerAllowlist,
    build_quote_index,
    build_speaker_allowlist,
    parse_meeting_analysis_response,
    serialize_meeting_analysis_result,
)
from .prompt import (
    CONTESTED_LEADERSHIP,
    build_meeting_analysis_prompt,
    build_meeting_analysis_prompt_parts,
)

__all__ = [
    "CONTESTED_LEADERSHIP",
    "MEETING_ANALYSIS_METHOD_VERSION",
    "AnalysisCitation",
    "AnalysisDecision",
    "AnalysisQuestion",
    "AnalysisSentiment",
    "AnalysisTopic",
    "ExclusionCounts",
    "MeetingAnalysisContractError",
    "MeetingAnalysisRequest",
    "MeetingAnalysisResult",
    "QuoteIndex",
    "SpeakerAllowlist",
    "build_meeting_analysis_prompt",
    "build_meeting_analysis_prompt_parts",
    "build_quote_index",
    "build_speaker_allowlist",
    "parse_meeting_analysis_response",
    "serialize_meeting_analysis_result",
]
