import sys
import os
import pickle
import logging
import torch
from pyannote.audio import Pipeline
from pyannote.audio.pipelines.utils.hook import ProgressHook
from dotenv import load_dotenv
import pathlib
import traceback

# Add project root to sys.path to allow imports from backend
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Ensure audio environment is setup
try:
    from backend.core.audio_setup import setup_audio_environment
    setup_audio_environment()
except ImportError:
    # Fallback if running in a context where backend package isn't fully resolvable
    # This might happen if PYTHONPATH isn't set correctly, but the sys.path insert above should fix it.
    print("Warning: Could not import backend.core.audio_setup")
except ImportError:
    print("Warning: Could not import backend.utils.hf_patch in subprocess", file=sys.stderr)

# Setup logging to stdout
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

class StdoutProgressHook:
    def __init__(self):
        self.last_percent = -1
    def __call__(self, step_name=None, step_artifact=None, file=None, total=None, completed=None):
        if completed is not None and total:
            percent = int((completed / total) * 100)
            if percent != self.last_percent:
                print(f"PROGRESS: {percent}", flush=True)
                self.last_percent = percent

# Usage: python diarize_subprocess_entry.py <audio_path> <output_path> <hf_token> <device>
def main():
    if len(sys.argv) < 5:
        print("Usage: python diarize_subprocess_entry.py <audio_path> <output_path> <hf_token> <device>", file=sys.stderr)
        sys.exit(1)
    audio_path = sys.argv[1]
    output_path = sys.argv[2]
    hf_token = sys.argv[3]
    device_str = sys.argv[4]

    if not os.path.exists(audio_path):
        print(f"Audio file not found: {audio_path}", file=sys.stderr)
        sys.exit(2)

    try:
        device = torch.device(device_str)
        pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=hf_token)
        pipeline.to(device)
        file = {"audio": audio_path}
        hook = StdoutProgressHook()
        diarization_result = pipeline(file, hook=hook)
        # Save result to output_path using pickle
        with open(output_path, 'wb') as f:
            pickle.dump(diarization_result, f)
        print("DONE", flush=True)
    except Exception as e:
        print(f"Error in offline diarization subprocess: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(3)
    finally:
        os.chdir(cwd)

if __name__ == "__main__":
    main() 