import logging
import os
import wave
from datetime import timedelta

from sqlmodel import select

from backend.core.db import async_session_maker
from backend.models.chat import ChatMessage
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.utils.path_manager import PathManager
from backend.utils.time import utc_now

logger = logging.getLogger(__name__)


async def _demo_person(session, user_id: int, name: str, color: str) -> GlobalSpeaker:
    """The user's own person for one of the demo's speakers.

    A re-seed links the person an earlier seed made, so the user's library
    never holds a name twice.
    """
    existing = (
        (
            await session.execute(
                select(GlobalSpeaker).where(
                    GlobalSpeaker.name == name, GlobalSpeaker.user_id == user_id
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing
    person = GlobalSpeaker(name=name, color=color, user_id=user_id)
    session.add(person)
    await session.commit()
    await session.refresh(person)
    return person


def _report_missing_user(user_id: int | None) -> None:
    if user_id:
        logger.error("Cannot seed demo data: user %s not found.", user_id)
    else:
        # A fresh install, before first-run setup. Setup seeds the sample
        # meeting for the owner it creates.
        logger.info("No users yet, so no demo recording to seed.")


async def seed_demo_data(user_id: int | None = None, force: bool = False) -> None:
    """
    Seeds the database with a demo recording if it doesn't exist.
    Creates a silent WAV file and populates DB with rich metadata.
    """
    logger.info("Checking for demo data...")

    pm = PathManager()
    recordings_dir = pm.recordings_directory
    if not os.path.exists(recordings_dir):
        os.makedirs(recordings_dir, exist_ok=True)

    async with async_session_maker() as session:
        target_user = None
        if user_id:
            target_user = await session.get(User, user_id)
        else:
            user_statement = select(User).limit(1)
            user_result = await session.execute(user_statement)
            target_user = user_result.scalars().first()

        if not target_user:
            _report_missing_user(user_id)
            return

        if target_user.has_seen_demo_recording and not force:
            logger.info(
                f"User {target_user.username} has already seen the demo recording. Skipping."
            )
            return

        # One file per user, because audio_path is unique.
        audio_filename = f"demo_welcome_{target_user.id}.wav"
        audio_path = os.path.join(recordings_dir, audio_filename)

        if not os.path.exists(audio_path):
            logger.info(f"Generating silent demo audio at {audio_path}")
            try:
                with wave.open(audio_path, "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)  # 16-bit
                    wav_file.setframerate(16000)
                    # Generate 30 seconds of silence
                    n_frames = 16000 * 30
                    wav_file.writeframes(b"\x00" * n_frames * 2)
            except (OSError, wave.Error) as e:
                logger.error(f"Failed to create demo audio file: {e}")
                return

        statement = select(Recording).where(
            Recording.name == "Welcome to Nojoin", Recording.user_id == target_user.id
        )
        results = await session.execute(statement)
        existing_recording = results.scalars().first()

        if existing_recording:
            if force:
                logger.info(
                    f"Force re-seeding: Deleting existing demo recording for user {target_user.username}..."
                )
                await session.delete(existing_recording)
                # Commit the delete before inserting the replacement so a failure
                # mid-reseed cannot leave two demo recordings for the same user.
                await session.commit()
            else:
                logger.info(
                    f"Demo recording already exists for user {target_user.username}."
                )
                if not target_user.has_seen_demo_recording:
                    target_user.has_seen_demo_recording = True
                    session.add(target_user)
                    await session.commit()
                return

        logger.info(f"Creating demo recording for user {target_user.username}...")

        # 1. Create Recording
        recording = Recording(
            name="Welcome to Nojoin",
            audio_path=audio_path,
            duration_seconds=30.0,
            file_size_bytes=os.path.getsize(audio_path),
            status=RecordingStatus.PROCESSED,
            is_archived=False,
            is_deleted=False,
            user_id=target_user.id,
            created_at=utc_now() - timedelta(days=1),
        )
        session.add(recording)
        await session.commit()
        await session.refresh(recording)

        # 2. Create Speakers
        speakers_data = [
            {"label": "SPEAKER_00", "name": "Alice (Host)", "color": "blue"},
            {"label": "SPEAKER_01", "name": "Bob (Product)", "color": "green"},
            {"label": "SPEAKER_02", "name": "Charlie (Eng)", "color": "orange"},
            {"label": "SPEAKER_03", "name": "Dana (Design)", "color": "purple"},
        ]

        for spk in speakers_data:
            global_spk = await _demo_person(
                session, target_user.id, spk["name"], spk["color"]
            )

            rec_spk = RecordingSpeaker(
                recording_id=recording.id,
                global_speaker_id=global_spk.id,
                diarization_label=spk["label"],
                local_name=spk["name"],
            )
            session.add(rec_spk)

        # 3. Create Transcript
        transcript_text = (
            "Alice: Welcome everyone to the Nojoin demo meeting. Today we'll walk through the key features.\n"
            "Bob: Thanks Alice. I'm excited to show off the new meeting intelligence capabilities.\n"
            "Charlie: The engineering team has been working hard on the local processing pipeline. Everything runs on your GPU.\n"
            "Dana: And the interface is looking great. We've focused on making the transcript easy to read and edit.\n"
            "Alice: Exactly. You can click any word to jump to that point in the audio.\n"
            "Bob: Don't forget about the speaker management. You can easily rename speakers and merge them if needed.\n"
            "Charlie: Plus, the AI generates automatic notes and action items for us.\n"
            "Dana: Let's dive in!"
        )

        segments = [
            {
                "start": 0.0,
                "end": 4.0,
                "text": "Welcome everyone to the Nojoin demo meeting. Today we'll walk through the key features.",
                "speaker": "SPEAKER_00",
            },
            {
                "start": 4.5,
                "end": 8.0,
                "text": "Thanks Alice. I'm excited to show off the new meeting intelligence capabilities.",
                "speaker": "SPEAKER_01",
            },
            {
                "start": 8.5,
                "end": 13.0,
                "text": "The engineering team has been working hard on the local processing pipeline. Everything runs on your GPU.",
                "speaker": "SPEAKER_02",
            },
            {
                "start": 13.5,
                "end": 18.0,
                "text": "And the interface is looking great. We've focused on making the transcript easy to read and edit.",
                "speaker": "SPEAKER_03",
            },
            {
                "start": 18.5,
                "end": 21.0,
                "text": "Exactly. You can click any word to jump to that point in the audio.",
                "speaker": "SPEAKER_00",
            },
            {
                "start": 21.5,
                "end": 25.0,
                "text": "Don't forget about the speaker management. You can easily rename speakers and merge them if needed.",
                "speaker": "SPEAKER_01",
            },
            {
                "start": 25.5,
                "end": 28.0,
                "text": "Plus, the AI generates automatic notes and action items for us.",
                "speaker": "SPEAKER_02",
            },
            {
                "start": 28.5,
                "end": 30.0,
                "text": "Let's dive in!",
                "speaker": "SPEAKER_03",
            },
        ]

        notes = """
# Meeting Summary
The team gathered to demonstrate the core features of the Nojoin platform, highlighting its local processing capabilities, user interface improvements, and AI-powered meeting intelligence.

## Key Topics
- **Local Processing**: All transcription and diarization runs locally on the user's GPU, ensuring privacy.
- **User Interface**: Focus on readability and ease of editing for transcripts.
- **Speaker Management**: Tools to rename and merge speakers are built-in.
- **AI Features**: Automatic generation of notes, summaries, and action items.

## Action Items
- [ ] Explore the speaker management panel to rename speakers.
- [ ] Try clicking on a transcript segment to seek the audio.
- [ ] Review the generated meeting notes.
        """

        transcript = Transcript(
            recording_id=recording.id,
            text=transcript_text,
            segments=segments,
            notes=notes,
            notes_status="completed",
            transcript_status="completed",
        )
        session.add(transcript)

        # 4. Create Chat Messages (Optional)
        chat_msg = ChatMessage(
            recording_id=recording.id,
            user_id=target_user.id,
            role="user",
            content="What are the key features mentioned?",
        )
        session.add(chat_msg)

        chat_response = ChatMessage(
            recording_id=recording.id,
            user_id=target_user.id,
            role="assistant",
            content="The key features mentioned are local GPU processing, a user-friendly interface for transcript editing, speaker management tools, and AI-generated notes and action items.",
        )
        session.add(chat_response)

        target_user.has_seen_demo_recording = True
        session.add(target_user)

        await session.commit()
        logger.info("Demo recording created successfully!")
