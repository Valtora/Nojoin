"""Where the live label state lives, and how it is removed.

Standard library only: the API calls ``remove_live_label_state`` when a capture
ends, and the API image has no SciPy or torch.

The state sits in the recording's ``live/`` directory beside the lane's
``state.json``. It holds speaker centroids and turn embeddings, so it is
removed when the capture is finalised rather than left for the temp-directory
cleanup.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILENAME = "speaker_labels.npz"
BANK_FILENAME = "speaker_labels.bank"
DISABLED_FILENAME = "speaker_labels.off"
# flock target that keeps two label steps of one recording from overlapping.
LOCK_FILENAME = "speaker_labels.lock"
LIVE_LABEL_FILENAMES = (STATE_FILENAME, BANK_FILENAME, DISABLED_FILENAME, LOCK_FILENAME)


def remove_live_label_state(live_dir: Path) -> None:
    """Delete the label state. Never raises: a leftover goes with the temp dir."""
    for name in LIVE_LABEL_FILENAMES:
        try:
            (live_dir / name).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove live label state %s: %s", name, exc)
