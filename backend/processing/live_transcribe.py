# backend/processing/live_transcribe.py
# Live transcription lane: a Celery task that transcribes recording segments
# as they arrive, producing provisional transcript segments. A sequence-gated
# buffer re-imposes ordering on concurrently uploaded segments and carries the
# trailing (incomplete) utterance forward across runs.

import json
import logging
import os
import re
from typing import Any

from backend.celery_app import celery_app
from backend.utils.config_manager import config_manager, is_meeting_edge_enabled
from backend.utils.recording_storage import recording_upload_temp_dir

logger = logging.getLogger(__name__)

# Tolerance (seconds) for treating a speech region as touching the buffer end.
TRAIL_EPS = 0.20
# Default maximum length (seconds) of a trailing utterance before a forced cut.
DEFAULT_FORCED_MAX = 8.0
# Absolute maximum length (seconds) of any emitted provisional live segment.
DEFAULT_MAX_SEGMENT_S = 20.0
# Sample rate of the live audio buffer.
LIVE_SAMPLE_RATE = 16000
# Silence threshold (ms) for the live lane: longer than the batch default so
# normal inter-phrase pauses do not fragment the live transcript.
LIVE_MIN_SILENCE_MS = 700
LIVE_SPEAKER_MATCH_THRESHOLD = 0.72
LIVE_SPEAKER_SOFT_MATCH_THRESHOLD = 0.45
LIVE_NEW_SPEAKER_THRESHOLD = 0.35
LIVE_GLOBAL_SPEAKER_MATCH_THRESHOLD = 0.78
LIVE_MIN_EMBEDDING_DURATION_S = 0.5
LIVE_MIN_NEW_SPEAKER_DURATION_S = 2.0

_STATE_FILENAME = "state.json"
_BUFFER_FILENAME = "buffer.wav"
_CONTEXT_FILENAME = "context.wav"


def read_live_state(live_dir) -> dict:
    """Read the live lane state, returning defaults when absent or unreadable."""
    state_path = os.path.join(str(live_dir), _STATE_FILENAME)
    default = {"next_expected": 1, "buffer_abs_start": 0.0, "last_speaker_label": None}
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "next_expected": int(data.get("next_expected", 1)),
            "buffer_abs_start": float(data.get("buffer_abs_start", 0.0)),
            "last_speaker_label": data.get("last_speaker_label"),
        }
    except (FileNotFoundError, ValueError, OSError):
        return default


def write_live_state(live_dir, state: dict) -> None:
    """Persist the live lane state to disk."""
    state_path = os.path.join(str(live_dir), _STATE_FILENAME)
    state_payload = {
        "next_expected": int(state["next_expected"]),
        "buffer_abs_start": float(state["buffer_abs_start"]),
    }
    if state.get("last_speaker_label"):
        state_payload["last_speaker_label"] = state["last_speaker_label"]
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state_payload, f)


def classify_speech(
    speech: list[dict],
    combined_len: float,
    *,
    forced_max_s: float = DEFAULT_FORCED_MAX,
    max_segment_s: float = DEFAULT_MAX_SEGMENT_S,
) -> tuple[list[dict], float]:
    """Split detected speech regions into completed regions and a carry-over cut point.

    Returns (complete_segments, cut_point) where cut_point is the buffer offset
    (seconds) from which unconsumed audio should be carried into the next run.
    """
    if not speech:
        # No speech: drop the silent buffer entirely.
        return [], combined_len

    last = speech[-1]
    trailing_incomplete = last["end"] >= combined_len - TRAIL_EPS

    if trailing_incomplete and (combined_len - last["start"]) >= forced_max_s:
        # Trailing utterance has run too long; treat it as complete now.
        complete_regions = speech
        cut_point = combined_len
    elif trailing_incomplete:
        # Carry the trailing utterance forward from its start.
        complete_regions = speech[:-1]
        cut_point = last["start"]
    else:
        # Last region ended with silence; everything is complete.
        complete_regions = speech
        cut_point = last["end"]

    return _split_complete_regions(complete_regions, max_segment_s=max_segment_s), cut_point


def _split_complete_regions(
    regions: list[dict],
    *,
    max_segment_s: float,
) -> list[dict]:
    if max_segment_s <= 0:
        return regions

    split_regions: list[dict] = []
    for region in regions:
        split_regions.extend(_split_region(region, max_segment_s=max_segment_s))
    return split_regions


def _split_region(region: dict, *, max_segment_s: float) -> list[dict]:
    start = float(region["start"])
    end = float(region["end"])
    duration = end - start

    if duration <= max_segment_s:
        return [region]

    split_regions: list[dict] = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(end, chunk_start + max_segment_s)
        chunk = dict(region)
        chunk["start"] = chunk_start
        chunk["end"] = chunk_end
        split_regions.append(chunk)
        chunk_start = chunk_end

    return split_regions


def _build_live_config() -> dict:
    """Build a minimal config dict for the live transcription engine call."""
    backend = config_manager.get("transcription_backend", "whisper")
    return {
        "transcription_backend": backend,
        "parakeet_model": config_manager.get("parakeet_model", "parakeet-tdt-0.6b-v3"),
        "whisper_model_size": config_manager.get("whisper_model_size", "turbo"),
        "processing_device": config_manager.get("processing_device", "auto"),
        "context_window_s": config_manager.get("live_context_window_s", 5.0),
        "forced_max_s": config_manager.get("live_forced_max_s", DEFAULT_FORCED_MAX),
        "max_segment_s": config_manager.get("live_max_segment_s", DEFAULT_MAX_SEGMENT_S),
        "speech_pad_ms": config_manager.get("live_speech_pad_ms", 300),
    }


def _get_live_speaker_display_name(index: int) -> str:
    return f"Speaker {index}"


def _get_live_speaker_label(index: int) -> str:
    return f"LIVE_{index:02d}"


def _get_speaker_display_name(speaker: Any) -> str:
    global_speaker = getattr(speaker, "global_speaker", None)
    return (
        getattr(speaker, "local_name", None)
        or (getattr(global_speaker, "name", None) if global_speaker else None)
        or getattr(speaker, "name", None)
        or _get_live_speaker_display_name(1)
    )


def _resolve_live_speaker(
    *,
    session,
    recording_id: int,
    user_id: int | None,
    audio_path: str,
    merged_config: dict,
    fallback_label: str | None = None,
) -> str:
    from sqlmodel import select

    from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
    from backend.processing.embedding import cosine_similarity, merge_embeddings
    from backend.processing.embedding_core import extract_embedding_for_segments

    existing_speakers = session.exec(
        select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)
    ).all()
    live_speakers = [
        speaker
        for speaker in existing_speakers
        if speaker.diarization_label.startswith("LIVE_")
    ]
    live_speaker_by_label = {speaker.diarization_label: speaker for speaker in live_speakers}

    embedding = None
    duration = 0.0
    try:
        import soundfile as sf

        info = sf.info(audio_path)
        duration = float(info.frames) / float(info.samplerate)
    except Exception:
        duration = 0.0

    if duration >= LIVE_MIN_EMBEDDING_DURATION_S:
        try:
            embedding = extract_embedding_for_segments(
                audio_path,
                [(0.0, duration)],
                device_str=merged_config.get("processing_device", "auto"),
                hf_token=merged_config.get("hf_token"),
            )
        except Exception as exc:
            logger.warning(
                "Live embedding extraction failed for recording %s: %s",
                recording_id,
                exc,
                exc_info=True,
            )

    if not embedding:
        if fallback_label and fallback_label in live_speaker_by_label:
            return fallback_label
        if len(live_speakers) == 1:
            return live_speakers[0].diarization_label
        if live_speakers:
            return live_speakers[-1].diarization_label

    if embedding:
        best_speaker = None
        best_score = 0.0
        for speaker in live_speakers:
            if not speaker.embedding:
                continue
            score = cosine_similarity(speaker.embedding, embedding)
            if score > best_score:
                best_score = score
                best_speaker = speaker

        if not best_speaker and fallback_label and fallback_label in live_speaker_by_label:
            fallback_speaker = live_speaker_by_label[fallback_label]
            fallback_speaker.embedding = embedding
            session.add(fallback_speaker)
            session.flush()
            return fallback_speaker.diarization_label

        if best_speaker and best_score >= LIVE_SPEAKER_MATCH_THRESHOLD:
            best_speaker.embedding = merge_embeddings(
                best_speaker.embedding,
                embedding,
                alpha=0.25,
                drift_guard=False,
            )
            session.add(best_speaker)
            session.flush()
            return best_speaker.diarization_label

        if best_speaker and best_score >= LIVE_SPEAKER_SOFT_MATCH_THRESHOLD:
            best_speaker.embedding = merge_embeddings(
                best_speaker.embedding,
                embedding,
                alpha=0.10,
                drift_guard=False,
            )
            session.add(best_speaker)
            session.flush()
            return best_speaker.diarization_label

        if user_id:
            global_speakers = session.exec(
                select(GlobalSpeaker)
                .where(GlobalSpeaker.user_id == user_id)
                .where(GlobalSpeaker.embedding != None)
            ).all()
            best_global = None
            best_global_score = 0.0
            for global_speaker in global_speakers:
                score = cosine_similarity(global_speaker.embedding, embedding)
                if score > best_global_score:
                    best_global_score = score
                    best_global = global_speaker

            if best_global and best_global_score >= LIVE_GLOBAL_SPEAKER_MATCH_THRESHOLD:
                linked_speaker = next(
                    (
                        speaker
                        for speaker in live_speakers
                        if speaker.global_speaker_id == best_global.id
                    ),
                    None,
                )
                if linked_speaker:
                    linked_speaker.embedding = merge_embeddings(
                        linked_speaker.embedding or [],
                        embedding,
                        alpha=0.25,
                        drift_guard=False,
                    )
                    session.add(linked_speaker)
                    session.flush()
                    return linked_speaker.diarization_label

                next_index = len(live_speakers) + 1
                live_speaker = RecordingSpeaker(
                    recording_id=recording_id,
                    diarization_label=_get_live_speaker_label(next_index),
                    name=None,
                    global_speaker_id=best_global.id,
                    embedding=embedding,
                )
                session.add(live_speaker)
                session.flush()
                return live_speaker.diarization_label

        if live_speakers:
            if (
                best_speaker
                and best_score > LIVE_NEW_SPEAKER_THRESHOLD
            ) or duration < LIVE_MIN_NEW_SPEAKER_DURATION_S:
                if fallback_label and fallback_label in live_speaker_by_label:
                    return fallback_label
                if best_speaker:
                    return best_speaker.diarization_label
                return live_speakers[-1].diarization_label

    next_index = len(live_speakers) + 1
    live_speaker = RecordingSpeaker(
        recording_id=recording_id,
        diarization_label=_get_live_speaker_label(next_index),
        name=_get_live_speaker_display_name(next_index),
        embedding=embedding,
    )
    session.add(live_speaker)
    session.flush()
    return live_speaker.diarization_label


def _extract_region_text(result: dict, prefix_s: float) -> str:
    """Select, from an engine result for a context-prefixed clip, the text that
    belongs to the speech region (the audio after `prefix_s` seconds).

    The clip handed to the engine is `left_context ++ region`; `prefix_s` is the
    length of the left-context run-up. Segment/word timestamps are clip-relative.
    """
    EPS = 0.10
    segments = result.get("segments") or []
    if not segments:
        if prefix_s <= 0:
            return (result.get("text") or "").strip()
        return ""

    kept: list[str] = []
    for seg in segments:
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        seg_text = (seg.get("text") or "").strip()
        if not seg_text:
            continue
        if end <= prefix_s + EPS:
            # Pure context: entirely within the run-up.
            continue
        if start >= prefix_s - EPS:
            # Entirely within the region.
            kept.append(seg_text)
            continue
        # Straddles the prefix boundary.
        words = seg.get("words")
        if words:
            region_words = [
                (w.get("word") or "")
                for w in words
                if float(w.get("start", 0.0)) >= prefix_s - EPS
            ]
            joined = " ".join(p.strip() for p in region_words if p.strip())
            if joined:
                kept.append(joined)
        elif (start + end) / 2 >= prefix_s:
            kept.append(seg_text)

    return re.sub(r"\s+", " ", " ".join(kept)).strip()


def _strip_repetition(text: str) -> str:
    """Lightweight hallucination guard: collapse runs of repeated words or short
    phrases. Defensive only — on any doubt the text is returned unchanged.
    """
    if not text:
        return text
    words = text.split()
    if len(words) < 3:
        return text

    # Collapse a run of 3+ consecutive identical words to a single occurrence.
    deduped: list[str] = []
    i = 0
    n_words = len(words)
    while i < n_words:
        j = i
        while j < n_words and words[j] == words[i]:
            j += 1
        run = j - i
        deduped.extend([words[i]] if run >= 3 else words[i:j])
        i = j

    # Collapse a short phrase (2-5 words) repeated 3+ times consecutively.
    out: list[str] = []
    i = 0
    n = len(deduped)
    while i < n:
        collapsed = False
        for plen in range(2, 6):
            if i + plen * 3 > n:
                continue
            phrase = deduped[i : i + plen]
            reps = 1
            j = i + plen
            while j + plen <= n and deduped[j : j + plen] == phrase:
                reps += 1
                j += plen
            if reps >= 3:
                out.extend(phrase)
                i = j
                collapsed = True
                break
        if not collapsed:
            out.append(deduped[i])
            i += 1

    return " ".join(out)


@celery_app.task(bind=True)
def transcribe_segment_live_task(self, recording_id: int, sequence: int):
    """Transcribe an uploaded recording segment in the live lane.

    Sequence-gated: only the task holding next_expected drains the contiguous
    run of segments on disk. Any failure is logged and the lane still advances;
    the final processing pipeline recovers everything.
    """
    import torch

    from backend.core.db import get_sync_session
    from backend.models.recording import Recording, RecordingStatus
    from backend.models.user import User
    from backend.processing.vad import detect_speech_segments, safe_read_audio
    from backend.processing.transcribe import transcribe_audio
    from backend.worker.tasks import resolve_llm_config

    config_manager.reload()

    # The live lane is only meaningful while the recording is uploading. Once
    # finalize() runs, the API deletes the upload temp dir; a live task that is
    # still queued must stop here rather than recreate an orphan dir or crash on
    # the vanished buffer/state files. The final pipeline is authoritative.
    session = get_sync_session()
    try:
        recording = session.get(Recording, recording_id)
        if not recording or recording.status != RecordingStatus.UPLOADING:
            return
    finally:
        session.close()

    temp_dir = recording_upload_temp_dir(recording_id, create=False)
    live_dir = temp_dir / "live"
    live_dir.mkdir(parents=True, exist_ok=True)

    state = read_live_state(live_dir)
    next_expected = state["next_expected"]
    buffer_abs_start = state["buffer_abs_start"]

    # --- Gating ---
    if sequence < next_expected:
        # Already consumed by an earlier run.
        return
    if sequence > next_expected:
        # Gap: this segment waits on disk until the run reaches it.
        return

    # --- Drain: contiguous run starting at next_expected ---
    run = []
    n = next_expected
    while os.path.exists(str(temp_dir / f"{n}.wav")):
        run.append(n)
        n += 1
    if not run:
        # Defensive: the triggering segment should exist; nothing to do.
        return

    buffer_path = str(live_dir / _BUFFER_FILENAME)

    try:
        # --- Build combined buffer ---
        parts = []
        if os.path.exists(buffer_path):
            parts.append(safe_read_audio(buffer_path, sampling_rate=LIVE_SAMPLE_RATE))
        for seg_n in run:
            parts.append(
                safe_read_audio(str(temp_dir / f"{seg_n}.wav"), sampling_rate=LIVE_SAMPLE_RATE)
            )

        combined = torch.cat(parts) if parts else torch.zeros(0)
        combined_len = combined.numel() / LIVE_SAMPLE_RATE
        combined_abs_start = buffer_abs_start

        # --- Build the live engine config (needed before the VAD call) ---
        live_config = _build_live_config()
        W = int(live_config["context_window_s"] * LIVE_SAMPLE_RATE)

        # --- Load user-aware config once for live speaker matching ---
        merged_config = live_config
        user_id = None
        session = get_sync_session()
        try:
            recording = session.get(Recording, recording_id)
            if recording:
                user_id = getattr(recording, "user_id", None)
                user_settings = {}
                if user_id:
                    user = session.get(User, user_id)
                    if user and user.settings:
                        user_settings = user.settings
                if hasattr(session, "exec"):
                    merged_config = resolve_llm_config(session, user_settings).merged_config
                merged_config.setdefault("transcription_backend", live_config["transcription_backend"])
                live_config.update(
                    {
                        "transcription_backend": merged_config.get(
                            "transcription_backend",
                            live_config["transcription_backend"],
                        ),
                        "parakeet_model": merged_config.get(
                            "parakeet_model",
                            live_config["parakeet_model"],
                        ),
                        "whisper_model_size": merged_config.get(
                            "whisper_model_size",
                            live_config["whisper_model_size"],
                        ),
                        "processing_device": merged_config.get(
                            "processing_device",
                            live_config["processing_device"],
                        ),
                        "forced_max_s": merged_config.get(
                            "live_forced_max_s",
                            live_config["forced_max_s"],
                        ),
                        "max_segment_s": merged_config.get(
                            "live_max_segment_s",
                            live_config["max_segment_s"],
                        ),
                    }
                )
        finally:
            session.close()

        # --- Detect speech and classify ---
        speech = detect_speech_segments(
            combined,
            min_silence_duration_ms=LIVE_MIN_SILENCE_MS,
            speech_pad_ms=live_config["speech_pad_ms"],
        )
        complete, cut_point = classify_speech(
            speech,
            combined_len,
            forced_max_s=float(live_config["forced_max_s"]),
            max_segment_s=float(live_config["max_segment_s"]),
        )

        # --- Read the rolling left-context buffer (already-consumed audio) ---
        context_path = str(live_dir / _CONTEXT_FILENAME)
        if os.path.exists(context_path):
            prev_context = safe_read_audio(context_path, sampling_rate=LIVE_SAMPLE_RATE)
        else:
            prev_context = torch.zeros(0)

        # --- Transcribe each completed speech region ---
        new_segments = []
        for sp in complete:
            start_sample = int(sp["start"] * LIVE_SAMPLE_RATE)
            end_sample = int(sp["end"] * LIVE_SAMPLE_RATE)
            region = combined[start_sample:end_sample]
            if region.numel() == 0:
                continue

            # Prepend a rolling audio context window so the engine has run-up.
            left_context = torch.cat([prev_context, combined[:start_sample]])
            if W > 0:
                left_context = left_context[-W:]
            else:
                left_context = left_context[:0]
            clip = torch.cat([left_context, region])
            prefix_s = left_context.numel() / LIVE_SAMPLE_RATE

            clip_path = str(live_dir / "clip.wav")
            region_path = str(live_dir / f"speaker_region_{sp['start']:.3f}_{sp['end']:.3f}.wav")
            try:
                import silero_vad

                tensor = clip if clip.ndim > 1 else clip.unsqueeze(0)
                silero_vad.save_audio(clip_path, tensor, sampling_rate=LIVE_SAMPLE_RATE)
                region_tensor = region if region.ndim > 1 else region.unsqueeze(0)
                silero_vad.save_audio(
                    region_path,
                    region_tensor,
                    sampling_rate=LIVE_SAMPLE_RATE,
                )
                result = transcribe_audio(clip_path, config=live_config)
                speaker_label = "UNKNOWN"
                session = get_sync_session()
                try:
                    speaker_label = _resolve_live_speaker(
                        session=session,
                        recording_id=recording_id,
                        user_id=user_id,
                        audio_path=region_path,
                        merged_config=merged_config,
                        fallback_label=state.get("last_speaker_label"),
                    )
                    session.commit()
                except Exception as speaker_exc:
                    if hasattr(session, "rollback"):
                        session.rollback()
                    logger.warning(
                        "Live speaker matching failed for recording %s region %.2f-%.2f: %s",
                        recording_id,
                        combined_abs_start + sp["start"],
                        combined_abs_start + sp["end"],
                        speaker_exc,
                        exc_info=True,
                    )
                finally:
                    session.close()

            finally:
                if os.path.exists(clip_path):
                    try:
                        os.remove(clip_path)
                    except OSError:
                        pass
                if os.path.exists(region_path):
                    try:
                        os.remove(region_path)
                    except OSError:
                        pass

            if not result:
                continue
            text = _strip_repetition(_extract_region_text(result, prefix_s))
            if not text:
                continue

            new_segments.append(
                {
                    "start": combined_abs_start + sp["start"],
                    "end": combined_abs_start + sp["end"],
                    "speaker": speaker_label,
                    "text": text,
                    "provisional": True,
                    "segment_source": "live",
                }
            )
            if speaker_label != "UNKNOWN":
                state["last_speaker_label"] = speaker_label

        # --- Carry over the unconsumed trailing audio ---
        cut_sample = int(cut_point * LIVE_SAMPLE_RATE)
        new_buffer = combined[cut_sample:]
        if new_buffer.numel() > 0:
            tensor = new_buffer if new_buffer.ndim > 1 else new_buffer.unsqueeze(0)
            import silero_vad

            silero_vad.save_audio(buffer_path, tensor, sampling_rate=LIVE_SAMPLE_RATE)
        elif os.path.exists(buffer_path):
            try:
                os.remove(buffer_path)
            except OSError:
                pass
        new_abs_start = combined_abs_start + cut_point

        # --- Update the rolling left-context buffer ---
        # consumed = the already-consumed audio immediately preceding the new
        # buffer; its last W samples become run-up for the next run.
        consumed = torch.cat([prev_context, combined[:cut_sample]])
        if W > 0:
            consumed = consumed[-W:]
        else:
            consumed = consumed[:0]
        if consumed.numel() > 0:
            tensor = consumed if consumed.ndim > 1 else consumed.unsqueeze(0)
            import silero_vad

            silero_vad.save_audio(context_path, tensor, sampling_rate=LIVE_SAMPLE_RATE)
        elif os.path.exists(context_path):
            try:
                os.remove(context_path)
            except OSError:
                pass

        # --- Persist provisional segments (race-guarded) ---
        if new_segments:
            from sqlalchemy.orm.attributes import flag_modified

            should_dispatch_meeting_edge = False
            session = get_sync_session()
            try:
                recording = session.get(Recording, recording_id)
                if recording and recording.status == RecordingStatus.UPLOADING:
                    transcript = recording.transcript
                    if transcript is not None:
                        transcript.segments = (transcript.segments or []) + new_segments
                        flag_modified(transcript, "segments")
                        session.add(transcript)
                        session.commit()

                        user_settings = getattr(
                            getattr(recording, "user", None),
                            "settings",
                            None,
                        )
                        if user_settings is None and getattr(recording, "user_id", None):
                            from backend.models.user import User

                            user = session.get(User, recording.user_id)
                            user_settings = getattr(user, "settings", None) if user else None

                        should_dispatch_meeting_edge = is_meeting_edge_enabled(user_settings)
            finally:
                session.close()

            if should_dispatch_meeting_edge:
                try:
                    from backend.worker.tasks import refresh_meeting_edge_task

                    refresh_meeting_edge_task.delay(recording_id)
                except Exception as exc:
                    logger.warning(
                        "Failed to dispatch Meeting Edge refresh for recording %s: %s",
                        recording_id,
                        exc,
                    )

        # --- Advance the lane ---
        state["next_expected"] = run[-1] + 1
        state["buffer_abs_start"] = new_abs_start
        write_live_state(live_dir, state)

    except Exception as exc:
        # Non-fatal: log, advance past the run, do not re-raise. The final
        # processing pipeline re-transcribes everything from the source audio.
        logger.error(
            "Live transcription failed for recording %s run %s: %s",
            recording_id,
            run,
            exc,
            exc_info=True,
        )
        # Best-effort advance: if the live dir vanished (recording finalized
        # mid-run) there is nothing left to advance — the final pipeline owns
        # the transcript now.
        try:
            state["next_expected"] = run[-1] + 1
            write_live_state(live_dir, state)
        except OSError:
            pass
