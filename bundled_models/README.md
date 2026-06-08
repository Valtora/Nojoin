This directory contains repository-bundled model assets that Nojoin loads locally before falling back to Hugging Face.

Current contents:

- `pyannote/speaker-diarization-community-1`
- `pyannote/wespeaker-voxceleb-resnet34-LM`
- `pyannote/segmentation-3.0`

To refresh these snapshots, run:

```bash
set -a && source .env && set +a
source .venv/bin/activate
python3 scripts/vendor_pyannote_models.py
```
