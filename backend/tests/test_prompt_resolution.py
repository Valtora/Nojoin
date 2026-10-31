"""Saved developer edits reach already-imported builders and provider requests."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import Any

import pytest

from backend.processing.documents.types import PageSource
from backend.processing.documents.vision import build_page_prompt
from backend.processing.llm_backends.anthropic import AnthropicLLMBackend
from backend.processing.llm_backends.base import LLMBackend
from backend.prompts import load_registry
from backend.services.live_chat import _recent_text
from backend.tests.test_prompt_overrides import (
    PromptAPI,
)
from backend.tests.test_prompt_overrides import (
    anyio_backend as anyio_backend,
)
from backend.tests.test_prompt_overrides import (
    prompt_api as prompt_api,
)
from backend.utils.chat_prompt import TranscriptExcerpts, build_chat_context
from backend.utils.long_meeting_notes import (
    _Notes,
    build_merge_prompt,
    build_part_prompt,
    context_for_notes,
)
from backend.utils.meeting_analysis.prompt import build_meeting_analysis_prompt
from backend.utils.meeting_edge import MeetingEdgeRequest, build_meeting_edge_prompt
from backend.utils.meeting_intelligence import (
    AutomaticMeetingIntelligenceRequest,
    build_automatic_meeting_intelligence_prompt,
)
from backend.utils.meeting_notes import AttachedDocument, build_documents_prompt_section
from backend.utils.notes_structure_generator import (
    build_notes_structure_generator_prompt,
)
from backend.utils.notes_templates import builtin_notes_template
from backend.utils.timeline_notes import (
    NotePlacement,
    render_notes_list_context,
    render_user_notes_context,
)
from backend.utils.transcript_parts import TranscriptPart


def _long_part() -> str:
    context = context_for_notes("", None, None, None, None)
    return build_part_prompt(
        context, 1, 2, TranscriptPart("Speaker: hello", None, None)
    )


def _long_merge() -> str:
    context = context_for_notes("", None, None, None, None)
    notes = [_Notes("## Notes\nHello", 1, 1, None, None)]
    return build_merge_prompt(context, notes, 2, final=False)


def _timeline_context() -> str:
    note = SimpleNamespace(body="Agenda")
    return render_user_notes_context([NotePlacement(note, "head")]) or ""


def _timeline_list() -> str:
    note = SimpleNamespace(body="Agenda")
    return render_notes_list_context([NotePlacement(note, "head")]) or ""


BUILDERS: list[tuple[str, Callable[[], str]]] = [
    ("notes.intro", lambda: LLMBackend.build_notes_prompt(None, "Speaker: hello", {})),
    ("notes.default_sections", lambda: builtin_notes_template().sections),
    (
        "notes.default_sections",
        lambda: build_notes_structure_generator_prompt("Weekly sync"),
    ),
    (
        "notes.intelligence_intro",
        lambda: build_automatic_meeting_intelligence_prompt(
            AutomaticMeetingIntelligenceRequest("Speaker: hello", ())
        ),
    ),
    (
        "speakers.suggestion_intro",
        lambda: LLMBackend.build_speaker_suggestion_prompt(None, "Speaker: hello"),
    ),
    (
        "analytics.intro",
        lambda: build_meeting_analysis_prompt(
            transcript="Speaker: hello", speaker_names=[]
        ),
    ),
    (
        "analytics.transcript_fence_instruction",
        lambda: build_meeting_analysis_prompt(
            transcript="Speaker: hello", speaker_names=[]
        ),
    ),
    (
        "meeting_edge.intro",
        lambda: build_meeting_edge_prompt(MeetingEdgeRequest("Speaker: hello")),
    ),
    (
        "meeting_edge.context_level_2",
        lambda: build_meeting_edge_prompt(MeetingEdgeRequest("Speaker: hello")),
    ),
    (
        "shared.heading_earlier_context_summary",
        lambda: build_meeting_edge_prompt(MeetingEdgeRequest("Speaker: hello")),
    ),
    ("long_meeting.part_intro", _long_part),
    ("long_meeting.merge_intro", _long_merge),
    ("vision.page", lambda: build_page_prompt(PageSource(1))),
    ("chat.capabilities_without_tools", lambda: build_chat_context("## Notes", None)),
    ("chat.no_transcript", lambda: build_chat_context("## Notes", None)),
    (
        "chat.excerpts_preamble",
        lambda: build_chat_context(
            "## Notes", TranscriptExcerpts((), "Speaker: hello")
        ),
    ),
    (
        "shared.documents_preamble",
        lambda: build_documents_prompt_section([AttachedDocument("Agenda", "Hello")]),
    ),
    ("shared.meeting_wide_notes_preamble", _timeline_context),
    ("shared.notes_list_preamble", _timeline_list),
]


@pytest.mark.anyio
@pytest.mark.parametrize("prompt_id,builder", BUILDERS)
async def test_already_imported_builders_follow_saved_edits_and_reset(
    prompt_api: PromptAPI, prompt_id: str, builder: Callable[[], str]
) -> None:
    original = builder()
    shipped = load_registry()[prompt_id].template
    for marker in ("FIRST café {opaque}", "SECOND edit after another render"):
        response = await prompt_api.call(
            "PUT",
            f"/settings/prompts/{prompt_id}",
            payload={"text": marker + "\n" + shipped},
        )
        assert response.status_code == 200, response.text
        rendered = builder()
        assert marker in rendered
        assert ("FIRST café {opaque}" in rendered) == marker.startswith("FIRST")
    response = await prompt_api.call("DELETE", f"/settings/prompts/{prompt_id}")
    assert response.status_code == 200
    assert builder() == original


@pytest.mark.anyio
async def test_live_trimming_preserves_a_saved_multiline_notes_legend(
    prompt_api: PromptAPI,
) -> None:
    legend = "Notes are user annotations.\nKeep their attribution café."
    response = await prompt_api.call(
        "PUT", "/settings/prompts/shared.notes_legend", payload={"text": legend}
    )
    assert response.status_code == 200
    transcript = (
        legend + "\n[00:00] Ana: " + "Older speech. " * 100 + "Latest decision."
    )
    shortened = _recent_text(transcript, 180)
    assert shortened.startswith(legend + "\n[00:00] Ana: [...] ")
    assert shortened.endswith("Latest decision.")
    assert len(shortened.encode("utf-8")) <= 180


class _ProviderStream(AbstractContextManager):
    def __exit__(self, *args: object) -> None:
        return None

    def get_final_message(self) -> SimpleNamespace:
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="## Summary\nA decision.")],
            stop_reason="end_turn",
        )


@pytest.mark.anyio
async def test_api_edits_change_the_next_provider_request_and_gate_off_restores_bytes(
    prompt_api: PromptAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, Any]] = []

    def capture(**kwargs: Any) -> _ProviderStream:
        sent.append(kwargs)
        return _ProviderStream()

    backend = object.__new__(AnthropicLLMBackend)
    backend.model = "claude-sonnet-4-5"
    backend.client = SimpleNamespace(messages=SimpleNamespace(stream=capture))
    assert (
        backend.generate_meeting_notes("Speaker: hello", {})
        == "## Summary\nA decision."
    )
    shipped_request = sent[-1]
    response = await prompt_api.call(
        "PUT",
        "/settings/prompts/notes.intro",
        payload={"text": "Changed instructions café."},
    )
    assert response.status_code == 200
    backend.generate_meeting_notes("Speaker: hello", {})
    assert sent[-1]["messages"][0]["content"].startswith("Changed instructions café.")
    assert sent[-1] != shipped_request
    monkeypatch.setenv("NOJOIN_DEVELOPER_MODE", "false")
    backend.generate_meeting_notes("Speaker: hello", {})
    assert sent[-1] == shipped_request
    monkeypatch.setenv("NOJOIN_DEVELOPER_MODE", "true")
    response = await prompt_api.call("DELETE", "/settings/prompts/notes.intro")
    assert response.status_code == 200
    backend.generate_meeting_notes("Speaker: hello", {})
    assert sent[-1] == shipped_request
