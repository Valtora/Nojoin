# Meeting Analytics: Evidence and Calibration

This note records the evidence behind every figure on the Analytics tab: where
each threshold comes from, what was measured, and what remains a judgement
call. [ARCHITECTURE.md](ARCHITECTURE.md) describes how the tiers are built.

Each claim carries one of three labels:

- **Measured**: validated by this project against ground truth (the AMI
  Meeting Corpus's hand-aligned annotations, the PTDB-TUG laryngograph pitch
  database, and the maintainer's own meeting library).
- **Published**: resting on cited literature, not re-verified here.
- **Judgement**: a chosen value with no measurement behind it.

## The measurement chain and its limits

The deterministic tier derives from utterance timestamps produced by ASR
segmentation and diarisation over a single mixed audio stream. Two properties
of that chain bound every downstream figure:

- **Timestamps carry roughly a quarter-second of noise.** Whisper-family word
  timestamps recover only 52-60% of words within ±200ms on meeting audio even
  after forced alignment (Bain et al., Interspeech 2023), and ±250ms is the
  disagreement between human annotators on where a turn boundary lies, which
  is why diarisation scoring uses a 250ms collar. No figure derived from these
  timestamps is presented at finer resolution.
- **The transcript is linear.** The merge assigns each transcribed segment one
  speaker, so two people talking at once cannot appear in the transcript. In
  August 2026 the maintainer's library (17 real meetings, live captures and
  imports alike) held zero overlapping utterances. Transcript-derived overlap
  and interruption figures are therefore zero by construction, so overlapping
  speech is measured from the audio and the tab carries no interruption counts
  (see below).

## Deterministic-tier thresholds

The constants live in
[constants.py](../backend/services/meeting_analytics/constants.py).
Calibration data: 171 AMI meetings converted from the corpus's hand-aligned
transcriber segments and word timings (ground truth, with real overlap), and
17 meetings from the maintainer's library measured through the pipeline.

**`TURN_GAP_MS = 2000`**: published and measured. There is no canonical turn
gap. Unit conventions in the literature run 50-500ms, but 28-31% of genuine
within-speaker pauses exceed one second (Heldner & Edlund 2010) and
face-to-face pauses run long, so an analytics turn merges further than a
phonetic unit. Jefferson's ~1s "standard maximum" silence is the citable
alternative anchor. AMI same-speaker gaps cluster densely around 2s (median
1.9s), so turn counts move ~12% as the threshold moves between 1s and 3s.

**`LATENCY_COLLAR_MS = 250`**: measured and published. Transitions under the
collar count as **immediate handovers**, a figure of their own, and the
reply-time median covers only gaps the timestamps can resolve. Fast
transitions are behaviour: the cross-language response-offset mode is 0-200ms
(Stivers et al. 2009, PNAS), ~40% of transitions in dyadic conversation start
in overlap (Heldner & Edlund 2010), and any response under ~600ms was planned
during the incoming turn (Levinson & Torreira 2015). In this pipeline 54-64%
of library transitions fall in [0, 150ms), because the merge quantises
transitions toward exact adjacency, so the 150ms exclusion floor the collar
replaced discarded most of the data.

**`LATENCY_LAPSE_MS = 5000`**: published and measured; the exact value is
judgement. Conversation analysis treats silences beyond ~1s as lapses (Sacks,
Schegloff & Jefferson 1974), and unbounded gaps produced absurd output on real
data: an import whose p95 "reply" was 8.1 seconds, and a turn taken after a
half-minute lull scored as a half-minute reply. Five seconds keeps slow
considered replies and excludes resumptions. Lapses are counted, never folded
into the median.

**Reading reply time**: published. The one evidentially supported reading of
response delay is that gaps beyond ~600-700ms are statistically associated
with dispreferred responses (Kendrick & Torreira 2015; Bögels et al. 2015), per
response token, mostly in dyadic English conversation. The tab does not
present it as a per-person trait.

**`MIN_UTTERANCE_MS_FOR_TURN_STATS = 250`**: published. About one
conversational syllable (Greenberg 2003), above the ~90-120ms noise and
perception floors. Backchannels average ~560ms, so they stay in turn counts.

**Attribution-warning triggers**: measured, except where marked. Across 171
AMI meetings with 4-5 real speakers, the smallest genuine speaker's share had
median 15% and 5th percentile 5%, and no meeting had two speakers under 3%
(`LOW_SHARE_SPEAKER_THRESHOLD = 0.03`). Two sub-3% clusters
(`LOW_SHARE_SPEAKER_COUNT_TRIGGER = 2`) therefore signal a split speaker, not
a quiet room. `HIGH_OVERLAP_SHARE_THRESHOLD = 0.15` is published: three times
the ~5% of speech that is simultaneous in normal conversation (3.8% in
Switchboard; Levinson & Torreira 2015). It reads the transcript's overlap, so
it fires only on a transcript that holds overlapping utterances.

**Talk-time share**: published. The best-grounded figure on the tab: total
speaking time alone identifies the participant others perceive as most
dominant with 85% accuracy in four-person meetings, the best single cue known
(Jayagopi, Hung, Yeo & Gatica-Perez 2009, IEEE TASLP, on AMI).

## Why there are no interruption counts

Directional interruption counts were removed for three independent reasons:

1. **They could never fire.** No transcript in the library held overlap (see
   above), so every count was zero.
2. **The audio cannot attribute them.** Overlap detection misses ~30% of
   events even on meeting-grade audio; speaker attribution inside overlap has
   no published accuracy figure and the known heuristic adds measurable
   confusion (Bullock et al. 2020); and the flattering published error rates
   are oracle-conditioned.
3. **The category is unreliable.** Human annotators agree on what counts as an
   interruption at Fleiss kappa 0.31-0.35, and the best purpose-built
   classifier reaches ~61% F1 on realistically labelled data (Lebourdais et
   al., LREC-COLING 2024). Overlap includes supportive backchannels and
   cooperative completions, and no method reliably separates them from
   competition for the floor.

Instead the tab shows **measured overlapping speech**, detected from the audio
with the bundled `pyannote/segmentation-3.0` model
([audio_overlap.py](../backend/processing/audio_overlap.py)): total overlapped
time as a floor ("at least"), its share of the recording, and where it
clustered. It attributes nothing to a speaker, and the word "interruption"
does not appear.

**Validation**: measured. The product's procedure (the model over 10-second
windows at a 2.5s step, then a majority vote across the windows covering each
frame) was scored on five AMI meetings against overlap ground truth built from
the corpus's per-speaker word alignments:

| Condition | Precision | Recall | F1 (250ms collar) |
| --- | --- | --- | --- |
| Close mix (Mix-Headset) | 0.71-0.95 | 0.62-0.85 | 0.73-0.79 |
| Far-field single mic (Array1-01) | 0.74-0.94 | 0.62-0.85 | 0.72-0.82 |
| Far-field after 64kbps MP3 round-trip | 0.82-0.92 | 0.70-0.74 | 0.76-0.82 |

Predicted totals ran 74-88% of the true totals and never over, so the interface
says "at least". Far-field and MP3-degraded audio (the import case) cost almost
nothing, a case the published literature leaves open.

**Closing the underestimate** (October 2026, #370): measured. All 24
combinations of three activation thresholds (0.5, 0.4, 0.3), four vote
fractions (majority, a third, a quarter, any one window) and two steps (2.5s
as shipped, and 1.25s) were scored on the same ten files (five meetings, both
channels) at the 250ms collar. As shipped: precision 0.84 mean and 0.71
minimum, recall 0.72, totals 70-91% of truth.

- The activation threshold changes nothing, because the powerset head already
  decodes to one class per frame.
- Every setting that raises the totals drops worst-meeting precision below the
  shipped minimum. At 2.5s, "any one window" brings the mean total to 98% but
  precision to 0.74 mean and 0.47 minimum, and overshoots on three files (up
  to 123%). At 1.25s, a one-third vote gives the best F1 (0.772 against 0.768)
  with totals at 82% and minimum precision 0.68, for twice the inference.
- A flat calibration factor of about 1.27 would put files between 89% and 116%
  of truth, so the figure would stop being a floor and gain a 15% error either
  way.

The shortfall is the detector's own recall, which matches published figures
for this model family on AMI (0.66-0.71). The method, its version and the "at
least" framing stay.

## Delivery descriptors

Measured in [delivery_descriptors.py](../backend/processing/delivery_descriptors.py)
(`DELIVERY_METHOD_VERSION = 2`). The words beside each figure come from
[deliveryInsights.ts](../frontend/src/components/analytics/deliveryInsights.ts),
ported for exports to
[delivery_readings.py](../backend/services/exports/delivery_readings.py).

**Pace** is words per utterance duration, the median over utterances of 1.5s
and up. ASR segmentation places most silent pauses between utterances, so this
is closer to articulation rate than to elapsed-time speaking rate. Published
"conversation runs at X wpm" figures mean little without that distinction: the
same Switchboard corpus yields 164, 196 or 236 wpm depending on how the
denominator treats pauses and overlap (Yuan, Liberman & Cieri 2006).

**Pace bands**: published and measured. Each edge is pinned to a corpus
statistic. Folk public-speaking norms ("conversation at 120-150 wpm") sit
about two bands too low for this measure.

| Band | Reading | Anchors |
| --- | --- | --- |
| under 120 | deliberate | below the slowest whole Switchboard conversation (111); below task-dialogue P20 |
| 120-160 | measured | audiobook narration ~155; radio monologue 150-170 |
| 160-200 | conversational | turn-wise Switchboard 164; Fisher 193; conversational register floor ~190 (Tauroza & Allison 1990) |
| 200-240 | brisk | CallHome 214; conversation upper range 210-230 |
| over 240 | fast | above the silence-excluded Switchboard mean (236) and long-segment plateau (~240) |

Measured by the product's method, 680 AMI speakers had a median of 159 wpm
(non-native scenario meetings) and the maintainer's library a median of 184
(quartiles 172-204). Both land where the bands say they should. **The bands
are an English calibration**: speaking rates in words are not comparable
across languages (syllable rates span 5-8/s at roughly constant information
rate; Coupé et al. 2019, Science Advances), and the panel's note says so.

**Pitch estimator**: measured. YIN's cumulative-mean-normalised difference
with parabolic interpolation (de Cheveigné & Kawahara 2002), in numpy with no
model, then the De Looze & Hirst two-pass per-speaker range (0.75×Q1 to
1.5×Q3) over a 60-500Hz first pass. Method version 1 used a plain
autocorrelation peak-picker over a fixed 70-400Hz band. Plain autocorrelation's
gross errors (mostly octave errors, ~10% in de Cheveigné & Kawahara's
benchmark) barely move a median but inflate a spread statistic such as pitch
movement, and the fixed band clipped creak and expressive peaks. Validated on PTDB-TUG (20 speakers, laryngograph ground
truth, reference frames aligned to ours):

| | v1 | v2 (current) | v2, 64kbps MP3 |
| --- | --- | --- | --- |
| Gross pitch error | 4.01% | 2.07% | 2.18% |
| False-voiced frames (the harmful direction) | 4.59% | 0.97% | 1.10% |
| Per-speaker median-F0 bias, p50 / p95 (semitones) | 0.49 / 1.35 | 0.23 / 0.73 | 0.22 / 0.66 |
| Semitone-IQR relative error, p50 / p95 | 0.17 / 0.37 | 0.05 / 0.31 | 0.08 / 0.22 |

The voicing threshold (aperiodicity 0.15, against YIN's published 0.10) was
chosen by sweep on the same ground truth: it halved the median bias the
stricter value showed on creaky speakers while keeping false-voiced frames
near 1%. The residual p95 median bias sits in creaky speakers, where the
laryngograph reference counts creak frames that the estimator rejects as
unvoiced; part of that gap is a question of definition. The MP3 column
matches the published finding that F0 survives MP3 at podcast bitrates and
VoIP codecs (Fuchs & Maxwell 2016; Zhang et al. 2021).

**Pitch movement as semitone IQR**: published practice. It is the
GeMAPS-family standard measure of F0 variability (Eyben et al. 2016), and it
holds up where SD does not (Portnova et al. 2025). It is compared only within a
meeting and against a person's own history, never on an absolute scale:
semitone scaling removes most but not all voice-height and language effects.

**Pausing**: published. Same-speaker gaps under `TURN_GAP_MS` (0-2s) align
with the literature's pause categories (Campione & Véronis 2002; Heldner &
Edlund 2010). The count is a lower bound, because ASR bridges brief pauses and
treats filled pauses as words, and it is shown per minute of the speaker's own
speech so long contributions do not read as hesitant.

**Reading thresholds**: judgement. A figure is remarked on when it differs
from the meeting's median by 20% (pitch movement) or 30% (pause rate), or from
the person's own baseline by 15% (pace), 20% (pitch movement) or 30% (pause
rate).

**Loudness**: published, negative. Uncalibrated dBFS with unknown microphone
distance and conferencing gain control measures the recording chain more than
the person (Švec & Granqvist 2018); broadcasting normalises level away for the
same reason. Loudness is stored and returned over MCP with that caveat, has no
column on the tab, and carries no reading.

## Cross-meeting baselines

"Was this their usual pace?" is answered by comparing a person's figures with
the median of their own across the user's other measured meetings. Speaker
normalisation is one of the few adjustments the paralinguistics literature
supports well, and it needs no model. The rules, in
[baselines.py](../backend/services/meeting_analytics/baselines.py):

- Only figures from the current `DELIVERY_METHOD_VERSION` are compared.
  Figures from different versions look alike but are not comparable, as with
  voiceprints.
- A baseline needs at least three measured meetings for a speaker linked to
  the same person (`BASELINE_MIN_MEETINGS`, judgement).
- The meeting count is shown with the words, which stay descriptive ("faster
  than their usual across 4 meetings"), never affective.
- Every six hours a background sweep re-measures up to 25 recordings whose
  delivery figures predate the current method version, so a version bump needs
  no manual pass.

## The emotion-model decision

Nojoin uses no emotion model anywhere. The decision was re-examined against
the current literature and stands:

- **Cross-corpus collapse.** Models scoring 90%+ on acted-emotion corpora
  score 22-43% unweighted accuracy on every naturalistic conversational corpus
  tested (EmoBox benchmark, Interspeech 2024: MSP-Podcast 22.2%, MELD 31.5%,
  with the best of ten foundation models). Cross-corpus transfer degrades a
  further 35-50% relative (Schuller et al. 2010 through Chou et al. 2025), and
  it is worst on valence, the dimension a "how did this meeting feel" surface
  would need. There is no published evidence of reliable SER on real workplace
  meetings, and meeting speech sits in the low-arousal regime where even 2025
  benchmarks fail.
- **Audio valence mostly comes from the words.** On synthesised speech that
  carries the words without the prosody, valence prediction collapses to near
  zero (Wagner et al. 2023, IEEE TPAMI). Nojoin already reads sentiment from
  the words, with verified citations, which is more accurate and auditable.
- **Fairness fails per speaker.** SER models are measurably unfair to
  individual speakers even when group-fair, and biases flip direction across
  corpora, so they cannot be corrected in deployment. Cross-language
  degradation is well documented, but no controlled accent-stratified SER
  audit exists, so the docs say only that generalisation across speaker
  populations is unproven.
- **The law draws the same line.** EU AI Act Article 5(1)(f), in force since
  2 February 2025, prohibits AI systems that infer the emotions of natural
  persons in the workplace, binding providers who place such systems on the EU
  market. Voice is biometric data under the Act; inference from the audio
  signal is covered, and sentiment from transcript text is explicitly
  excluded. Nojoin's delivery descriptors claim nothing about feeling, its
  sentiment comes from the words with mandatory citations, and the two are
  never fused. A "deviation from personal baseline" feature framed as
  emotional state would still be in scope, so the baselines are worded as
  delivery, not affect.

## Reproducing the validation

Neither the ground truth nor the scoring scripts are in the repository. The
AMI annotations (CC BY 4.0) and PTDB-TUG (ODbL) are free downloads.

- **Overlap**: convert AMI per-speaker word alignments into overlap regions
  (words merged at ≤200ms gaps; regions where two or more speakers' intervals
  intersect); run the product's detection over the Mix-Headset and Array1-01
  channels; score frame-level precision and recall with and without a 250ms
  boundary collar; repeat after a 64kbps MP3 round trip. The aggregation sweep
  (`harness/sweep_osd.py`) reuses those masks and scoring, runs the model once
  per file and step, and evaluates every threshold combination from the stored
  activations.
- **Pitch**: run the product's frame chain over PTDB-TUG microphone recordings
  resampled to 16kHz; score gross pitch error and voicing against the
  laryngograph reference (10ms hop, aligned); report per-speaker median-F0
  bias and semitone-IQR error with the two-pass applied to both tracks, so
  estimator error is separated from whether creak frames count.

Re-run the matching procedure, and bump `DELIVERY_METHOD_VERSION` or
`AUDIO_OVERLAP_METHOD_VERSION`, whenever the pitch chain or the overlap
detection changes.

## Key sources

Turn-taking and pauses: Stivers et al. 2009 (PNAS 106); Heldner & Edlund 2010
(J. Phonetics 38); Levinson & Torreira 2015 (Front. Psychol. 6:731); Kendrick
& Torreira 2015 (Discourse Processes 52); Sacks, Schegloff & Jefferson 1974.
Dominance: Jayagopi et al. 2009 (IEEE TASLP 17); Gatica-Perez 2009 review.
Interruption: Lebourdais et al. 2024 (LREC-COLING); Schegloff 2000; Beattie
1981. Overlap detection: Bullock et al. 2020 (ICASSP); Bredin & Laurent 2021
(Interspeech); Plaquet & Bredin 2023 (Interspeech). Timestamps: Bain et al.
2023 (WhisperX, Interspeech). Speaking rate: Yuan, Liberman & Cieri 2006
(Interspeech); Tauroza & Allison 1990 (Applied Linguistics 11); Hayakawa et
al. 2018 (LREC); Coupé et al. 2019 (Science Advances 5). Pitch: de Cheveigné
& Kawahara 2002 (JASA 111); Boersma 1993; Mauch & Dixon 2014 (ICASSP);
Strömbergsson 2016 (Interspeech); Portnova et al. 2025 (JSLHR 68); De Looze &
Hirst two-pass; Eyben et al. 2016 (GeMAPS, IEEE TAC 7); Fuchs & Maxwell 2016
(Speech Prosody); Zhang et al. 2021 (JASA 149). Loudness: Švec & Granqvist
2018 (JSLHR 61). SER: Ma et al. 2024 (EmoBox, Interspeech); Wagner et al.
2023 (IEEE TPAMI); Schuller et al. 2010 (IEEE TAC); Chou et al. 2025.
Regulation: EU AI Act Article 5(1)(f) and the Commission guidelines of
4 February 2025.
