"""The install-wide switch for moving reply starts to the person replying.

Kept free of processing imports so the config code can read it.
"""

# config.json key; admin-only, like live speaker labels.
SENTENCE_START_CORRECTION_KEY = "sentence_start_correction"
SENTENCE_START_CORRECTION_DEFAULT = True


def correction_enabled(setting: object) -> bool:
    """Whether the ``sentence_start_correction`` setting leaves the correction
    on. It is on unless set to False; off, both passes are as without it."""
    return setting is not False
