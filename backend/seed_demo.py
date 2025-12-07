import os
import wave
import logging
from datetime import datetime, timedelta
from sqlmodel import select
from backend.core.db import async_session_maker
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.speaker import RecordingSpeaker, GlobalSpeaker
from backend.models.chat import ChatMessage
from backend.models.user import User
from backend.utils.path_manager import PathManager

logger = logging.getLogger(__name__)

async def seed_demo_data():
    """
    Seeds the database with a demo recording if it doesn't exist.
    Creates a silent WAV file and populates DB with rich metadata.
    """
    logger.info("Checking for demo data...")
    
    # Ensure recordings directory exists
    # In docker, this is mapped to /app/recordings
    recordings_dir = "/app/recordings"
    if not os.path.exists(recordings_dir):
        os.makedirs(recordings_dir, exist_ok=True)
        
    audio_filename = "demo_welcome.wav"
    audio_path = os.path.join(recordings_dir, audio_filename)
    
    # Create silent WAV file if it doesn't exist
    if not os.path.exists(audio_path):
        logger.info(f"Generating silent demo audio at {audio_path}")
        try:
            with wave.open(audio_path, 'wb') as wav_file:
                wav_file.setnchannels(1) # Mono
                wav_file.setsampwidth(2) # 16-bit
                wav_file.setframerate(16000) # 16kHz
                # Generate 30 seconds of silence
                n_frames = 16000 * 30
                wav_file.writeframes(b'\x00' * n_frames * 2)
        except Exception as e:
            logger.error(f"Failed to create demo audio file: {e}")
            return

    async with async_session_maker() as session:
        # Get the first user (admin) to assign ownership
        user_statement = select(User).limit(1)
        user_result = await session.execute(user_statement)
        admin_user = user_result.scalars().first()
        
        if not admin_user:
            logger.error("Cannot seed demo data: No users found.")
            return

        # Check if demo recording exists
        statement = select(Recording).where(Recording.name == "Welcome to Nojoin")
        results = await session.execute(statement)
        existing_recording = results.scalars().first()
        
        if existing_recording:
            logger.info("Demo recording already exists.")
            return

        logger.info("Creating demo recording...")
        
        # 1. Create Recording
        recording = Recording(
            name="Welcome to Nojoin",
            audio_path=audio_path,
            duration_seconds=30.0,
            file_size_bytes=os.path.getsize(audio_path),
            status=RecordingStatus.PROCESSED,
            is_archived=False,
            is_deleted=False,
            user_id=admin_user.id,
            # Use a fixed date for consistency
            created_at=datetime.now() - timedelta(days=1) 
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
            # Create Global Speaker if needed (optional, but good for demo)
            global_spk = GlobalSpeaker(name=spk["name"], color=spk["color"])
            session.add(global_spk)
            await session.commit()
            await session.refresh(global_spk)
            
            # Link to Recording
            rec_spk = RecordingSpeaker(
                recording_id=recording.id,
                global_speaker_id=global_spk.id,
                diarization_label=spk["label"],
                local_name=spk["name"]
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
            {"start": 0.0, "end": 4.0, "text": "Welcome everyone to the Nojoin demo meeting. Today we'll walk through the key features.", "speaker": "SPEAKER_00"},
            {"start": 4.5, "end": 8.0, "text": "Thanks Alice. I'm excited to show off the new meeting intelligence capabilities.", "speaker": "SPEAKER_01"},
            {"start": 8.5, "end": 13.0, "text": "The engineering team has been working hard on the local processing pipeline. Everything runs on your GPU.", "speaker": "SPEAKER_02"},
            {"start": 13.5, "end": 18.0, "text": "And the interface is looking great. We've focused on making the transcript easy to read and edit.", "speaker": "SPEAKER_03"},
            {"start": 18.5, "end": 21.0, "text": "Exactly. You can click any word to jump to that point in the audio.", "speaker": "SPEAKER_00"},
            {"start": 21.5, "end": 25.0, "text": "Don't forget about the speaker management. You can easily rename speakers and merge them if needed.", "speaker": "SPEAKER_01"},
            {"start": 25.5, "end": 28.0, "text": "Plus, the AI generates automatic notes and action items for us.", "speaker": "SPEAKER_02"},
            {"start": 28.5, "end": 30.0, "text": "Let's dive in!", "speaker": "SPEAKER_03"},
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
            transcript_status="completed"
        )
        session.add(transcript)
        
        # 4. Create Chat Messages (Optional)
        chat_msg = ChatMessage(
            recording_id=recording.id,
            user_id=admin_user.id,
            role="user",
            content="What are the key features mentioned?"
        )
        session.add(chat_msg)
        
        chat_response = ChatMessage(
            recording_id=recording.id,
            user_id=admin_user.id,
            role="assistant",
            content="The key features mentioned are local GPU processing, a user-friendly interface for transcript editing, speaker management tools, and AI-generated notes and action items."
        )
        session.add(chat_response)
        
        await session.commit()
        logger.info("Demo recording created successfully!")
