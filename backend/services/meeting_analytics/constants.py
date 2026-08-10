"""Thresholds for the deterministic meeting-analytics tier.

Every number the analytics tier depends on lives here rather than at its call
site, for the same reason the speaker-identification thresholds are centralised
in ``backend/processing/embedding.py``: these are tuning decisions that have to
be changed together and reasoned about as a set, and a value inlined in a
comprehension is a value nobody finds again.
"""

from __future__ import annotations

# A turn is a run of consecutive utterances by one speaker. Two seconds is the
# boundary between someone pausing mid-thought and someone finishing: below it
# the ASR's own segmentation dominates, above it a genuine handover is usually
# available to another speaker even when nobody takes it.
TURN_GAP_MS = 2_000

# An utterance starting fractionally before the previous speaker stops is
# turn-boundary bleed, not an interruption. Diarisation boundaries are accurate
# to roughly this scale, so anything under it measures the diariser rather than
# the conversation.
OVERLAP_FLOOR_MS = 300

# Response gaps below this are the VAD closing a region, not a human deciding
# to answer. Samples under the floor are excluded from the median rather than
# clamped to it, because clamping invents a population at exactly the floor.
LATENCY_FLOOR_MS = 150

# Timeline bucketing. A fixed bucket count makes short meetings unreadably
# granular and a fixed bucket width makes long ones unreadably wide, so the
# width is derived from duration and then floored.
TIMELINE_TARGET_BUCKETS = 40
TIMELINE_MIN_BUCKET_MS = 60_000

# Attribution-warning inputs. A cluster holding less than this share of speech
# is the signature of a speaker who was split, rather than of a participant who
# barely spoke -- a real quiet participant still holds contiguous turns, where a
# split fragment holds scattered seconds.
LOW_SHARE_SPEAKER_THRESHOLD = 0.03

# Two or more such clusters is the point at which a split becomes more likely
# than a genuinely quiet room. One is common and unremarkable.
LOW_SHARE_SPEAKER_COUNT_TRIGGER = 2

# Overlapped speech above this share of total speech means the diariser was
# working in conditions where attribution is least reliable.
HIGH_OVERLAP_SHARE_THRESHOLD = 0.15

# Utterances shorter than this contribute to talk time but are excluded from
# turn-length statistics, where a stray "mm" would drag every median down.
MIN_UTTERANCE_MS_FOR_TURN_STATS = 250
