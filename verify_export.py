import sys
import os
from datetime import datetime
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.getcwd())

# Mock fastAPI dependencies to avoid import errors if environment is partial
import backend.api.v1.endpoints.transcripts as transcripts_module

# Mock objects
class MockSpeaker:
    def __init__(self, name, label):
        self.name = name
        self.local_name = name
        self.diarization_label = label
        self.global_speaker = None

class MockRecording:
    def __init__(self):
        self.id = 1
        self.name = "Test Meeting Verification"
        self.created_at = datetime.now()
        self.duration_seconds = 125.5
        self.speakers = [
            MockSpeaker("Alice", "SPEAKER_00"),
            MockSpeaker("Bob", "SPEAKER_01")
        ]

class MockTranscript:
    def __init__(self):
        self.recording_id = 1
        self.segments = [
            {"start": 0.0, "end": 10.0, "speaker": "SPEAKER_00", "text": "Hello everyone, welcome to the test meeting."},
            {"start": 10.5, "end": 20.0, "speaker": "SPEAKER_01", "text": "Thanks Alice. I'm glad to be here."},
            {"start": 21.0, "end": 25.0, "speaker": "SPEAKER_00", "text": "Let's discuss the export feature."}
        ]
        self.notes = """# Meeting Notes

## Attendees
- Alice
- Bob

## Discussion Points
1. Exporting to PDF works.
2. Exporting to **DOCX** works too.

## Action Items
* Verify the file output.
* Update *frontend* components.
"""

def test_pdf_generation():
    print("Testing PDF Generation...")
    rec = MockRecording()
    trans = MockTranscript()
    
    try:
        pdf_bytes = transcripts_module._generate_pdf_export(rec, trans, True, True)
        print(f"PDF Generated successfully. Size: {len(pdf_bytes)} bytes")
        with open("test_output.pdf", "wb") as f:
            f.write(pdf_bytes)
        print("Saved to test_output.pdf")
    except Exception as e:
        print(f"PDF Generation Failed: {e}")
        raise

def test_docx_generation():
    print("\nTesting DOCX Generation...")
    rec = MockRecording()
    trans = MockTranscript()
    
    try:
        docx_bytes = transcripts_module._generate_docx_export(rec, trans, True, True)
        print(f"DOCX Generated successfully. Size: {len(docx_bytes)} bytes")
        with open("test_output.docx", "wb") as f:
            f.write(docx_bytes)
        print("Saved to test_output.docx")
    except Exception as e:
        print(f"DOCX Generation Failed: {e}")
        raise

if __name__ == "__main__":
    test_pdf_generation()
    test_docx_generation()
