Pyannote models bundled with the repository. Nojoin loads them from here first, then from the Hugging Face cache, and downloads them only as a last resort. `NOJOIN_PYANNOTE_MODELS_DIR` points it at a different directory.

- `pyannote/speaker-diarization-community-1`
- `pyannote/wespeaker-voxceleb-resnet34-LM`
- `pyannote/segmentation-3.0`

To refresh them, set `HF_TOKEN` in `.env` to a token with access to these gated models, then run:

```bash
set -a && source .env && set +a
source .venv/bin/activate
python3 scripts/vendor_pyannote_models.py
```
