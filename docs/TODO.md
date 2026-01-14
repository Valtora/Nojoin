# Nojoin To-Do List

## Speaker Segment Length & Transcript View

- I've noticed that when a speaker speaks for a long time in a meeting they're assigned one huge speaker segment. The problem with this is that it creates a large chat bubble in the Transcript View that can reduce the quality of the experience. Not only this but longer segments tend to be less accurate for voice model embeddings since they tend to contain interjections from other speakers which are not registered as a separate segment.
- I therefore think that there should be a hard upper limit to how long a speaker segment can be, in my opinion this should be around 10 seconds. This gives the user a better experience and also makes voice model embeddings more accurate while hopefully capturing speaker interjections better.
- Also, when a speaker segment is playing the Transcript window scrolls to the segment that is playing back but while playback is active the user can't scroll away from the segment that is being played.
- Once the initial scroll to the segment is complete there's no need to 'lock' the scroll position to the segment that is being played. The user should be able to manually scroll away from the segment that is being played.
- If they click to seek to another part of the audio while playback is active the scroll position should be go to that point, just how it works as-is with the current implementation but again, without the aggressive lock to the segment that is playing.

## PyTorch 2.6 Safe Globals

- The project uses PyTorch 2.6+ which defaults `weights_only=True` for security.
- The Pyannote embedding model (`wespeaker`) requires `torch.torch_version.TorchVersion` and `pyannote.audio.core.task.Specifications` to be in the safe globals list.
- We must add these to `torch.serialization.add_safe_globals([...])` before loading models. This is now handled in `embedding_core.py` and `diarize.py` at the module level.ing.

## .env Variables

- Currently the user is prompted to enter sensitive API keys and tokens in the frontend during the first-run setup wizard.
- This is fine however we should also support setting these variables in the .env file.
- The first-run wizard (and config manager) should check if the .env file exists and if it does, it should load the variables from it. If it doesn't, it should prompt the user to enter them.
