from pyannote.audio import Inference
import inspect
try:
    print(f"Signature: {inspect.signature(Inference.__init__)}")
except Exception as e:
    print(f"Error inspecting signature: {e}")
