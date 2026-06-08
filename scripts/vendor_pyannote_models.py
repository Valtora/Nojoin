from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from huggingface_hub import snapshot_download


MODEL_IDS = (
    "pyannote/speaker-diarization-community-1",
    "pyannote/wespeaker-voxceleb-resnet34-LM",
    "pyannote/segmentation-3.0",
)


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    target_root = project_root / "bundled_models" / "pyannote"
    target_root.mkdir(parents=True, exist_ok=True)

    token = os.getenv("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN must be set to vendor the gated Pyannote models.", file=sys.stderr)
        return 1

    for model_id in MODEL_IDS:
        local_dir = target_root / model_id.split("/", 1)[1]
        print(f"Vendoring {model_id} -> {local_dir}")
        snapshot_download(
            repo_id=model_id,
            token=token,
            local_dir=str(local_dir),
        )
        shutil.rmtree(local_dir / ".cache", ignore_errors=True)

    print("Pyannote models vendored successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
