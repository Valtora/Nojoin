"""Meeting chat inference in the io lane (CLI OAuth).

The chat endpoint dispatches here only when the user's resolved provider is
``cli`` — the Claude Agent SDK lives only in ``worker-io``. Tokens are published
to a Redis list keyed by this task's id; the API relays them to the browser over
SSE (see ``backend.services.chat_relay``). This task is the *single writer* of
the assistant ``ChatMessage``; the API relay never persists.
"""

from backend.models.chat import ChatMessage
from backend.services.chat_relay import ChatStreamPublisher, friendly_chat_error

from .constants import *  # noqa: F401,F403 - shared task imports (celery_app, models, resolvers)


@celery_app.task(  # noqa: F405
    name="backend.worker.tasks.meeting_chat_task",
    base=DatabaseTask,
    bind=True,  # noqa: F405
)
def meeting_chat_task(
    self,
    recording_id: int,
    augmented_message: str,
    conversation_history: list | None = None,
) -> None:
    session = self.session
    publisher = ChatStreamPublisher(self.request.id)
    try:
        recording = session.get(Recording, recording_id)  # noqa: F405
        if not recording:
            publisher.publish_error("Recording not found.")
            return

        transcript = session.exec(  # noqa: F405
            select(Transcript).where(Transcript.recording_id == recording_id)  # noqa: F405
        ).first()
        meeting_notes = (transcript.notes if transcript else "") or ""

        user_settings = {}
        if recording.user_id:
            user = session.get(User, recording.user_id)  # noqa: F405
            if user and user.settings:
                user_settings = user.settings

        llm_config = resolve_llm_config(  # noqa: F405
            session, user_settings, user_id=recording.user_id
        )
        backend = _llm_backend_from_config(llm_config)  # noqa: F405

        full_response = ""
        for chunk in backend.ask_question_streaming(
            user_question=augmented_message,
            meeting_notes=meeting_notes,
            diarized_transcript=None,  # rebuilt inside via recording_id
            conversation_history=conversation_history,
            recording_id=recording.id,
        ):
            # Only a degraded secondary backend can emit control dicts (e.g.
            # notes_update); its DB side effect already ran. CLI itself never
            # does — skip framing non-text chunks.
            if isinstance(chunk, dict):
                continue
            text = str(chunk)
            full_response += text
            publisher.publish_token(text)

        if full_response.strip():
            session.add(
                ChatMessage(
                    recording_id=recording.id,
                    user_id=recording.user_id,
                    role="assistant",
                    content=full_response,
                )
            )
            session.commit()
        publisher.publish_done()
    except Exception as exc:  # noqa: BLE001 - relay a friendly error to the client
        logger.error(  # noqa: F405
            "meeting_chat_task failed for recording %s: %s", recording_id, exc
        )
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        publisher.publish_error(friendly_chat_error(exc))
        publisher.publish_done()
    finally:
        publisher.close()
