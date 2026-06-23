"""Characterization snapshot of mounted endpoint route contracts (BE-007).

Guards the behaviour-preserving split of oversized endpoint modules into
router-aggregator packages. The snapshots below capture every route exposed by
the affected routers as ``(path, methods, name, response_model_name)`` so that
any change to a path, HTTP method, handler name, or response model is caught.
"""

from backend.api.v1.endpoints import speakers, transcripts


def _route_contract(router):
    rows = []
    for route in router.routes:
        methods = tuple(sorted(m for m in (route.methods or set()) if m != "HEAD"))
        response_model = getattr(route, "response_model", None)
        response_model_name = (
            getattr(response_model, "__name__", None)
            if response_model is not None
            else None
        )
        rows.append((route.path, methods, route.name, response_model_name))
    return sorted(rows)


TRANSCRIPTS_ROUTE_CONTRACT = [
    ("/{recording_id}/chat", ("DELETE",), "clear_chat_history", None),
    ("/{recording_id}/chat", ("GET",), "get_chat_history", "List"),
    ("/{recording_id}/chat", ("POST",), "chat_with_meeting", None),
    ("/{recording_id}/export", ("GET",), "export_content", None),
    (
        "/{recording_id}/meeting-edge-focus",
        ("PUT",),
        "update_meeting_edge_focus",
        None,
    ),
    ("/{recording_id}/notes", ("GET",), "get_notes", None),
    ("/{recording_id}/notes", ("PUT",), "update_notes", None),
    ("/{recording_id}/notes/generate", ("POST",), "generate_notes", None),
    (
        "/{recording_id}/replace",
        ("POST",),
        "find_and_replace",
        "TranscriptPublicRead",
    ),
    (
        "/{recording_id}/segments",
        ("PUT",),
        "update_transcript_segments",
        "TranscriptPublicRead",
    ),
    (
        "/{recording_id}/segments/{segment_index}",
        ("PUT",),
        "update_segment_speaker",
        None,
    ),
    (
        "/{recording_id}/segments/{segment_index}/text",
        ("PUT",),
        "update_transcript_segment_text",
        "TranscriptPublicRead",
    ),
    ("/{recording_id}/user-notes", ("GET",), "get_user_notes", None),
    ("/{recording_id}/user-notes", ("PUT",), "update_user_notes", None),
    (
        "/{recording_id}/utterances",
        ("GET",),
        "get_transcript_utterances",
        "TranscriptUtteranceListRead",
    ),
    (
        "/{recording_id}/utterances/{utterance_id}/speaker",
        ("PATCH",),
        "update_transcript_utterance_speaker",
        "TranscriptPublicRead",
    ),
    (
        "/{recording_id}/utterances/{utterance_id}/text",
        ("PATCH",),
        "update_transcript_utterance_text",
        "TranscriptPublicRead",
    ),
]


SPEAKERS_ROUTE_CONTRACT = [
    ("/", ("GET",), "list_global_speakers", "List"),
    ("/", ("POST",), "create_global_speaker", "GlobalSpeaker"),
    ("/merge", ("POST",), "merge_speakers", "GlobalSpeaker"),
    ("/recordings/{recording_id}", ("PUT",), "update_recording_speaker", "List"),
    (
        "/recordings/{recording_id}/merge",
        ("POST",),
        "merge_recording_speakers",
        "RecordingPublicRead",
    ),
    (
        "/recordings/{recording_id}/speakers/{diarization_label}",
        ("DELETE",),
        "delete_recording_speaker",
        None,
    ),
    (
        "/recordings/{recording_id}/speakers/{diarization_label}/promote",
        ("POST",),
        "promote_speaker_to_global",
        "RecordingSpeakerPublicRead",
    ),
    (
        "/recordings/{recording_id}/speakers/{diarization_label}/split",
        ("POST",),
        "split_local_speaker",
        "List",
    ),
    (
        "/recordings/{recording_id}/speakers/{diarization_label}/suggestions/accept",
        ("POST",),
        "accept_recording_speaker_suggestion",
        "dict",
    ),
    (
        "/recordings/{recording_id}/speakers/{diarization_label}/suggestions/reject",
        ("POST",),
        "reject_recording_speaker_suggestion",
        "dict",
    ),
    (
        "/recordings/{recording_id}/speakers/{diarization_label}/voiceprint",
        ("DELETE",),
        "delete_voiceprint",
        None,
    ),
    (
        "/recordings/{recording_id}/speakers/{diarization_label}/voiceprint/apply",
        ("POST",),
        "apply_voiceprint_action",
        None,
    ),
    (
        "/recordings/{recording_id}/speakers/{diarization_label}/voiceprint/extract",
        ("POST",),
        "extract_voiceprint",
        None,
    ),
    (
        "/recordings/{recording_id}/speakers/{label}/color",
        ("PUT",),
        "update_speaker_color",
        "dict",
    ),
    (
        "/recordings/{recording_id}/voiceprints/extract-all",
        ("POST",),
        "extract_all_voiceprints",
        None,
    ),
    ("/{speaker_id}", ("DELETE",), "delete_global_speaker", None),
    ("/{speaker_id}", ("PUT",), "update_global_speaker", "GlobalSpeaker"),
    (
        "/{speaker_id}/embedding",
        ("DELETE",),
        "delete_global_speaker_embedding",
        None,
    ),
    ("/{speaker_id}/recalibrate", ("POST",), "recalibrate_voiceprint", None),
    ("/{speaker_id}/scan-matches", ("POST",), "scan_for_matches", None),
    ("/{speaker_id}/segments", ("GET",), "get_speaker_segments", "List"),
    ("/{speaker_id}/split", ("POST",), "split_speaker", "GlobalSpeaker"),
]


def test_transcripts_route_contract_is_unchanged():
    assert _route_contract(transcripts.router) == TRANSCRIPTS_ROUTE_CONTRACT


def test_speakers_route_contract_is_unchanged():
    assert _route_contract(speakers.router) == SPEAKERS_ROUTE_CONTRACT


def test_transcripts_router_is_reexported_from_package():
    # The package __init__ must continue to expose ``router`` so that
    # ``backend/api/v1/api.py`` can mount ``transcripts.router`` unchanged.
    assert hasattr(transcripts, "router")
    assert hasattr(speakers, "router")
