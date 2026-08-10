# Meeting Analytics: Evidence and Calibration

This note records the evidence behind the Meeting Analytics surface: where
each threshold comes from, what was measured rather than assumed, which claims
are calibrated against ground truth, and which remain judgement calls. It
exists so that "how do you know that?" has a written answer for every figure
the tab shows. [ARCHITECTURE.md](ARCHITECTURE.md) describes how the tiers are
built; this describes why they say what they say.

Three kinds of statement appear below, and the difference matters:

- **Measured** — validated by this project against ground truth (the AMI
  Meeting Corpus's hand-aligned annotations, the PTDB-TUG laryngograph pitch
  database, and the maintainer's own meeting library).
- **Published** — resting on cited literature, not re-verified here.
- **Judgement** — a chosen value with a stated rationale and no measurement
  behind it. These are labelled as such rather than dressed up.

## The measurement chain and its limits

Everything in the deterministic tier derives from utterance timestamps
produced by ASR segmentation and diarisation over a single mixed audio
stream. Two properties of that chain bound what any downstream figure can
honestly claim:

- **Timestamps carry roughly a quarter-second of noise.** Whisper-family word
  timestamps recover only 52-60% of words within ±200ms on meeting audio even
  after forced alignment (Bain et al., Interspeech 2023), and ±250ms is the
  disagreement between *human annotators* on where a turn boundary lies — the
  reason diarisation scoring uses a 250ms collar. No figure derived from
  these timestamps is presented at finer resolution.
- **The transcript is linear.** The merge assigns each transcribed segment
  one speaker from one mixed stream, so two people talking at once is not
  representable in the transcript, whatever happened in the room. Measured
  across the maintainer's full production library — seventeen real meetings,
  live browser captures and imports alike — **zero** overlapping utterances
  exist. Every transcript-derived overlap or interruption figure is therefore
  zero by construction, on every recording class. This is why overlapping
  speech is measured from the audio instead, and why the surface carries no
  interruption counts at all (see below).

## Deterministic-tier thresholds

Calibration data: 171 AMI meetings converted from the corpus's hand-aligned
transcriber segments and word timings (ground truth, with real overlap), and
17 meetings from the maintainer's production library measured through the
actual pipeline. Distributions below are from those runs.

**`TURN_GAP_MS = 2000`** — published + measured. There is no canonical turn
gap: unit conventions in the literature run 50-500ms, but 28-31% of genuine
within-speaker pauses exceed one second (Heldner & Edlund 2010) and
face-to-face pauses run long, so an analytics turn merges further than a
phonetic unit. Jefferson's ~1s "standard maximum" silence is the citable
alternative anchor. Measured: AMI same-speaker gaps are densely distributed
around 2s (median 1.9s), so turn counts move ~12% as the threshold moves
1s-3s. That sensitivity is a property of conversation, not a tuning failure,
and is disclosed here rather than hidden.

**`LATENCY_COLLAR_MS = 250` (replacing a 150ms exclusion floor)** — measured +
published. The floor was wrong twice over. Sub-150ms responses are not
artefacts: the cross-language response-offset mode is 0-200ms (Stivers et al.
2009, PNAS), ~40% of transitions in dyadic conversation start in overlap
(Heldner & Edlund 2010), and any response under ~600ms was planned during the
incoming turn (Levinson & Torreira 2015) — fast handovers are behaviour, not
noise. And in this pipeline's own timings, 54-64% of real-library transitions
fall in [0,150ms) because the merge quantises transitions toward exact
adjacency, so the floor silently discarded the majority of the data. The
rework counts sub-collar transitions as **immediate handovers** — a figure of
their own — and reserves the reply-time median for gaps the timestamps can
actually resolve.

**`LATENCY_LAPSE_MS = 5000`** — published + measured; the exact value is
judgement. Conversation analysis treats silences beyond ~1s as lapses, and
unbounded gaps produced absurd output on real data (an import whose p95
"reply" sample was 8.1 seconds; a turn taken after a half-minute lull scored
as a half-minute reply). Five seconds keeps slow considered replies and
excludes resumptions; lapses are excluded and counted, never folded in.

**Interpretation bound for reply time** — published. The one evidentially
supported reading of response delay: gaps beyond ~600-700ms are statistically
associated with dispreferred responses (Kendrick & Torreira 2015; Bögels et
al. 2015), per response token, mostly in dyadic English conversation. The
surface deliberately stops short of rendering that as a per-person trait.

**`OVERLAP_FLOOR_MS = 300`** — published + measured. Benign turn-transition
overlap has mode 96ms / median 205ms / mean 275ms (Switchboard; Levinson &
Torreira 2015), while only 12% of real overlap events in AMI ground truth are
shallower than 300ms — so the floor removes boundary bleed while keeping 88%
of genuine overlap.

**`MIN_UTTERANCE_MS_FOR_TURN_STATS = 250`** — published anchors. About one
conversational syllable (Greenberg 2003); above the ~90-120ms noise and
perception floors. A fragment filter, not a backchannel filter — backchannels
average ~560ms and are deliberately kept in turn counts.

**Attribution-warning triggers** — measured. Across 171 AMI meetings with 4-5
real speakers, the smallest genuine speaker's share had median 15% and 5th
percentile 5%, and **no meeting at all** had two speakers under 3% share. Two
sub-3% clusters is therefore a diarisation-splitting signature, not a quiet
room, which is exactly what `LOW_SHARE_SPEAKER_COUNT_TRIGGER` assumes. The
15% high-overlap trigger sits three times above the ~5% of speech that is
simultaneous in normal conversation (published).

**Talk-time share** — published. The strongest-grounded figure on the
surface: total speaking time alone identifies the participant others perceive
as most dominant with 85% accuracy in four-person meetings, the best single
cue known (Jayagopi, Hung, Yeo & Gatica-Perez 2009, IEEE TASLP, on AMI).

## Why there are no interruption counts

The surface previously computed directional interruption counts from
overlapping utterances. Three independent findings retired them:

1. **They could never fire.** No transcript in the production library holds
   overlap (see above), so every count was structurally zero.
2. **The audio cannot defensibly attribute them.** Overlap *detection* misses
   ~30% of events even on meeting-grade audio; speaker attribution *inside*
   overlap has no published accuracy figure and the known heuristic adds
   measurable confusion (Bullock et al. 2020); and the flattering published
   error rates are oracle-conditioned.
3. **The category itself is unreliable.** Human annotators agree on what
   counts as an interruption at Fleiss kappa 0.31-0.35, and the best
   purpose-built classifier reaches ~61% F1 on realistically labelled data
   (Lebourdais et al., LREC-COLING 2024). Overlap includes supportive
   backchannels and cooperative completions; no method reliably separates
   them from competition for the floor.

What ships instead is **measured overlapping speech**, detected from the
audio with `pyannote/segmentation-3.0` (already a pipeline dependency —
no new model): total overlapped time as an explicit floor, its share of the
meeting, and where it clustered. No per-speaker attribution, and the word
"interruption" does not appear on the surface.

**Measured validation.** The exact procedure the product runs (model,
sliding-window aggregation, majority vote) was scored on five AMI meetings
against overlap ground truth built from the corpus's per-speaker word
alignments, on three channel conditions:

| Condition | Precision | Recall | F1 (250ms collar) |
| --- | --- | --- | --- |
| Close mix (Mix-Headset) | 0.71-0.95 | 0.62-0.85 | 0.73-0.79 |
| Far-field single mic (Array1-01) | 0.74-0.94 | 0.62-0.85 | 0.72-0.82 |
| Far-field after 64kbps MP3 round-trip | 0.82-0.92 | 0.70-0.74 | 0.76-0.82 |

Predicted overlap totals ran 74-88% of the true totals — a systematic
underestimate, never an overestimate, which is why the interface presents the
figure as "at least". Far-field and MP3-degraded audio (the import case) cost
essentially nothing, closing a gap the published literature leaves open.

## Delivery descriptors

**What the pace figure is.** Words per utterance duration, median over
utterances of 1.5s and up. ASR segmentation places most silent pauses
*between* utterances, so this is closer to articulation rate than to
elapsed-time speaking rate — and published "conversation runs at X wpm"
figures are meaningless without that distinction: the same Switchboard corpus
yields 164, 196, or 236 wpm depending purely on how the denominator treats
pauses and overlap (Yuan, Liberman & Cieri 2006).

**Pace bands** — published + measured. The previous bands ("conversation at
120-150 wpm") were a folk public-speaking norm, wrong for this measure by
about two bands. Each new edge is pinned to a corpus statistic:

| Band | Reading | Anchors |
| --- | --- | --- |
| under 120 | deliberate | below the slowest whole Switchboard conversation (111); below task-dialogue P20 |
| 120-160 | measured | audiobook narration ~155; radio monologue 150-170 |
| 160-200 | conversational | turn-wise Switchboard 164; Fisher 193; conversational register floor ~190 (Tauroza & Allison 1990) |
| 200-240 | brisk | CallHome 214; conversation upper range 210-230 |
| over 240 | fast | above the silence-excluded Switchboard mean (236) and long-segment plateau (~240) |

Cross-checked against our own data measured by the product's exact method:
680 AMI speakers (median 159 wpm — non-native scenario meetings) and the
production library (median 184, quartiles 172-204). Both populations land
where the bands say they should. **The bands are an English calibration**:
speaking rates in words are not comparable across languages (syllable rates
span 5-8/s at roughly constant information rate — Coupé et al. 2019, Science
Advances), and the panel's footnote says so.

**Pitch estimator (method version 2)** — measured. Version 1 was a plain
autocorrelation peak-picker; its ~10% gross-error class (mostly octave
errors) barely moves a median but inflates a spread statistic, which is
exactly what the pitch-movement figure is. Version 2 implements YIN's
cumulative-mean-normalised difference with parabolic interpolation (de
Cheveigné & Kawahara 2002) — still pure numpy, still no model — plus the De
Looze & Hirst two-pass per-speaker range (0.75×Q1 to 1.5×Q3) over a wide
60-500Hz first pass, replacing a fixed 70-400Hz band that clipped creak and
expressive peaks. Validated on PTDB-TUG (20 speakers, laryngograph ground
truth, reference frames aligned to ours):

| | v1 (shipped) | v2 | v2, 64kbps MP3 |
| --- | --- | --- | --- |
| Gross pitch error | 4.01% | 2.07% | 2.18% |
| False-voiced frames (the harmful direction) | 4.59% | 0.97% | 1.10% |
| Per-speaker median-F0 bias, p50 / p95 (semitones) | 0.49 / 1.35 | 0.23 / 0.73 | 0.22 / 0.66 |
| Semitone-IQR relative error, p50 / p95 | 0.17 / 0.37 | 0.05 / 0.31 | 0.08 / 0.22 |

The voicing threshold (aperiodicity 0.15) was chosen by sweep on the same
ground truth. The residual p95 median bias concentrates in creaky speakers,
where the laryngograph reference counts creak frames the estimator's voicing
rejects — partly a definitional boundary rather than an error, recorded here
rather than tuned away. Codec immunity matches the published finding that F0
survives MP3 at podcast bitrates and VoIP codecs (Fuchs & Maxwell 2016;
Zhang et al. 2021).

**Pitch movement as semitone IQR** — published practice: it is the
GeMAPS-family standard measure of F0 variability (Eyben et al. 2016), robust
where SD is fragile (Portnova et al. 2025). Compared only within a meeting
and against a person's own history, never on an absolute scale: semitone
scaling removes most but not all voice-height and language effects.

**Pausing** — published. The 0-2s within-turn window aligns with the
literature's pause categories (Campione & Véronis 2002; Heldner & Edlund
2010); the count is a lower bound (ASR bridges brief pauses and treats filled
pauses as words) and is shown as a rate so long contributions do not read as
hesitant. The within-meeting and baseline deviation bands (`0.2`/`0.3`/`0.15`)
are judgement, disclosed as such.

**Loudness** — published, negative. Uncalibrated dBFS with unknown microphone
distance and conferencing gain control measures the recording chain more than
the person (Švec & Granqvist 2018); broadcasting's answer to the same problem
is to normalise level away entirely. The figures stay stored and available
over MCP with that caveat attached, are not surfaced as a delivery figure in
the interface, and carry no reading.

## Cross-meeting baselines

"Was this their usual pace?" is answered by comparing a person's figures with
the median of their own across the user's other measured meetings — the
defensible, model-free form of within-speaker comparison (speaker
normalisation is one of the few robustly supported adjustments in the
paralinguistics literature). Constraints, each load-bearing: only figures
from the same `DELIVERY_METHOD_VERSION` are compared (cross-version numbers
resemble each other but are not comparable, exactly as with voiceprints); a
person needs at least three measured meetings before "their usual" means
anything; the meeting count is always displayed with the words; and the
wording stays descriptive — "faster than their usual across 4 meetings" —
never affective. A method-version bump triggers a bounded background
re-measure sweep (mirroring the voiceprint-rebuild precedent) so the library
converges to comparability without a manual pass.

## The emotion-model decision

The published decision — no emotion model anywhere in Nojoin — was
re-examined against the current literature and stands, on stronger grounds
than originally stated:

- **Cross-corpus collapse.** Models scoring 90%+ on acted-emotion corpora
  score 22-43% unweighted accuracy on every naturalistic conversational
  corpus tested (EmoBox benchmark, Interspeech 2024: MSP-Podcast 22.2%, MELD
  31.5% — with the best of ten foundation models). Cross-corpus transfer
  degrades a further 35-50% relative (Schuller et al. 2010 through Chou et
  al. 2025), and it is worst on valence — the dimension a "how did this
  meeting feel" surface would need. There is no published evidence of
  reliable SER on real workplace meetings at all, and meeting speech sits in
  the low-arousal regime where even 2025 benchmarks fail.
- **Audio valence is largely smuggled text.** The transformer-era gains on
  valence come from implicit linguistic content: on synthesised speech
  carrying the words without the prosody, valence prediction collapses to
  near zero (Wagner et al. 2023, IEEE TPAMI). Nojoin already reads sentiment
  from the words, with verified citations — the more accurate and more
  auditable pathway.
- **Fairness fails at exactly the wrong granularity.** SER models are
  measurably unfair to *individual speakers* even when group-fair, and biases
  flip direction across corpora, so they cannot be corrected in deployment.
  One correction to the previously published rationale: the specific claim
  about *accents* was stronger than the literature supports — cross-language
  degradation is well documented, but no controlled accent-stratified SER
  audit exists. The honest phrasing, now used in the docs, is that
  generalisation across speaker populations is unproven.
- **The law now draws the same line.** EU AI Act Article 5(1)(f) prohibits AI
  systems that infer emotions of natural persons in the workplace, in force
  since 2 February 2025, binding providers who place such systems on the EU
  market. Voice is biometric data under the Act; inference from the audio
  signal is covered, and sentiment from transcript text is explicitly
  excluded. Nojoin's architecture — measured delivery descriptors that claim
  nothing about feeling, word-derived sentiment with mandatory citations, the
  two never fused — is precisely the compliant shape, and a "deviation from
  personal baseline" feature *framed as emotional state* would still be in
  scope, which is why the baselines above are worded as delivery, not affect.

## Reproducing the validation

Ground-truth material is not committed to the repository (the AMI annotations
are CC BY 4.0 and PTDB-TUG is ODbL; both are free downloads). The procedure:

- **Overlap**: convert AMI per-speaker word alignments into overlap regions
  (words merged at ≤200ms gaps; regions where two or more speakers'
  intervals intersect); run the product's detection over the Mix-Headset and
  Array1-01 channels; score frame-level precision/recall with and without a
  250ms boundary collar; repeat after a 64kbps MP3 round-trip.
- **Pitch**: run the product's exact frame chain over PTDB-TUG microphone
  recordings resampled to 16kHz; score gross pitch error and voicing against
  the laryngograph reference (10ms hop, aligned); report per-speaker
  median-F0 bias and semitone-IQR error with the two-pass applied to both
  tracks, so estimator error is isolated from the definitional question of
  whether creak frames count.

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
