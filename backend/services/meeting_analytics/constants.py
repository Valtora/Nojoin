"""Thresholds for the deterministic meeting-analytics tier.

Every number the analytics tier depends on lives here rather than at its call
site, for the same reason the speaker-identification thresholds are centralised
in ``backend/processing/embedding.py``: these are tuning decisions that have to
be changed together and reasoned about as a set, and a value inlined in a
comprehension is a value nobody finds again.

Each value carries its evidence. Where a number rests on published
measurement or on this project's own calibration runs, the comment says which;
where it remains a judgement call, the comment says that instead. The
calibration data and sources are collected in docs/ANALYTICS_EVIDENCE.md.
"""

from __future__ import annotations

# A turn is a run of consecutive utterances by one speaker. There is no
# canonical value in the literature: unit conventions run 50ms-500ms, but
# 28-31% of genuine within-speaker pauses exceed one second (Heldner & Edlund
# 2010, J. Phonetics 38) and face-to-face pauses run long, so an analytics
# turn merges further than a phonetic unit. Two seconds is a conservative
# choice above Jefferson's ~1s "standard maximum" tolerable silence; ground
# truth (AMI) shows same-speaker gaps densely distributed around this value,
# so turn counts move ~12% as the threshold moves 1s-3s. That sensitivity is
# a property of conversation, not a tuning failure, and is disclosed in the
# docs rather than hidden behind false precision.
TURN_GAP_MS = 2_000

# An utterance starting fractionally before the previous speaker stops is
# turn-boundary bleed, not simultaneous speech worth reporting. Diarisation
# boundaries are accurate to roughly this scale, and most benign terminal
# overlap is shorter (Switchboard transition overlaps: mode 96ms, median
# 205ms, mean 275ms -- Levinson & Torreira 2015), while only 12% of real
# overlap events in AMI ground truth are shallower than this.
OVERLAP_FLOOR_MS = 300

# Speaker-transition gaps below this are indistinguishable from measurement
# noise: +/-250ms is the disagreement between human annotators on where a turn
# boundary lies (the NIST DER collar convention), and this pipeline's
# timestamps quantise most transitions to exact adjacency. Transitions under
# the collar are therefore reported as immediate handovers -- a real and
# meaningful behaviour (the response-offset mode in conversation is 0-200ms;
# Stivers et al. 2009, PNAS) -- rather than being fed into a median whose
# precision the timestamps cannot support.
LATENCY_COLLAR_MS = 250

# A transition gap at or beyond this is a lapse in the conversation, not a
# response: conversation analysis treats silences beyond ~1s as lapses
# (Sacks, Schegloff & Jefferson 1974), and library calibration showed
# unbounded gaps letting a turn taken after a 30-second lull report as a
# 30-second "reply". Five seconds keeps slow considered replies while
# excluding resumptions; lapses are excluded and counted, never folded in.
LATENCY_LAPSE_MS = 5_000

# Timeline bucketing. A fixed bucket count makes short meetings unreadably
# granular and a fixed bucket width makes long ones unreadably wide, so the
# width is derived from duration and then floored. Presentation choices, not
# measurement claims.
TIMELINE_TARGET_BUCKETS = 40
TIMELINE_MIN_BUCKET_MS = 60_000

# Attribution-warning inputs. A cluster holding less than this share of speech
# is the signature of a speaker who was split, rather than of a participant who
# barely spoke. Calibration across 171 AMI meetings (4-5 real speakers each)
# found the smallest genuine speaker's share had median 15% and 5th percentile
# 5%, and no meeting at all with two speakers under 3% -- so two such clusters
# is a diarisation signature, not a quiet room.
LOW_SHARE_SPEAKER_THRESHOLD = 0.03

# Two or more such clusters is the point at which a split becomes more likely
# than a genuinely quiet room. One is common and unremarkable.
LOW_SHARE_SPEAKER_COUNT_TRIGGER = 2

# Overlapped speech above this share of total speech means the diariser was
# working in conditions where attribution is least reliable. Total
# simultaneous speech in conversation runs under ~5% of the speech stream
# (3.8% in Switchboard -- Levinson & Torreira 2015), so three times that is
# deep in the tail.
HIGH_OVERLAP_SHARE_THRESHOLD = 0.15

# Utterances shorter than this contribute to talk time but are excluded from
# turn-length statistics. 250ms is about one conversational syllable
# (Greenberg 2003): below it lies a fragment, not a turn. This is a fragment
# filter, not a backchannel filter -- backchannels average ~560ms and are
# deliberately kept.
MIN_UTTERANCE_MS_FOR_TURN_STATS = 250
