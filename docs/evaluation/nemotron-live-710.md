# Native live-label evaluation

Live labels pass the development thresholds, but **four small meetings and held-out Lords lose lines at first display**. These are previously examined held-out data, so these figures check the implementation. One fixed replay followed development acceptance; there was no retuning or new GPU inference.

## Development

First-display acceptance thresholds and results: original AMI pooled **76.675008% → 4595/5954 = 77.175008%**; Lords **95.189305% → 1565/1627 = 96.189305%**; London **95.059113% → 1369/1421 = 96.340605%**. Every development stream stays within one percentage point of classic.

| Cohort | Streams | Reference lines | Today's first / last % | This PR first / last % | This PR UNKNOWN first / last | Native / clustering streams |
|---|---:|---:|---:|---:|---:|---:|
| additional AMI far-field | 4 | 2499 | 52.581 / 51.781 | 80.672 / 80.672 | 0 / 0 | 4 / 0 |
| additional AMI headset | 4 | 2499 | 59.464 / 58.784 | 81.793 / 81.793 | 0 / 0 | 4 / 0 |
| original AMI far-field | 4 | 2977 | 41.552 / 40.477 | 76.285 / 76.285 | 1 / 1 | 4 / 0 |
| original AMI headset | 4 | 2977 | 59.120 / 59.893 | 78.065 / 78.065 | 1 / 1 | 4 / 0 |
| train-contaminated AMI far-field | 2 | 1636 | 28.301 / 28.301 | 82.702 / 82.702 | 0 / 0 | 2 / 0 |
| train-contaminated AMI headset | 2 | 1636 | 40.465 / 43.949 | 84.474 / 84.474 | 0 / 0 | 2 / 0 |
| ICSI far-field | 5 | 5299 | 54.293 / 54.444 | 74.240 / 74.656 | 119 / 68 | 2 / 3 |
| ICSI headset | 5 | 5299 | 63.842 / 65.163 | 79.109 / 79.430 | 93 / 59 | 4 / 1 |
| London | 1 | 1421 | 96.059 / 96.692 | 96.341 / 96.833 | 10 / 7 | 0 / 1 |
| Lords | 1 | 1627 | 96.189 / 97.972 | 96.189 / 97.972 | 12 / 3 | 0 / 1 |

## Held-out implementation check

| Cohort | Streams | Reference lines | Today's first / last % | This PR first / last % | This PR UNKNOWN first / last | Native / clustering streams |
|---|---:|---:|---:|---:|---:|---:|
| chime6-far | 4 | 16341 | 12.325 / 13.071 | 55.272 / 55.272 | 0 / 0 | 4 / 0 |
| chime6-near | 4 | 16341 | 18.481 / 19.883 | 61.906 / 61.906 | 0 / 0 | 4 / 0 |
| commons-2026-09-09 | 1 | 1253 | 94.014 / 96.808 | 94.094 / 96.728 | 12 / 2 | 0 / 1 |
| lords-2026-09-02 | 1 | 1376 | 94.985 / 96.003 | 94.767 / 95.858 | 6 / 1 | 0 / 1 |
| lotusdis-far | 10 | 2747 | 52.093 / 52.676 | 78.194 / 78.194 | 0 / 0 | 10 / 0 |
| lotusdis-near | 10 | 2747 | 78.231 / 78.813 | 84.565 / 84.565 | 0 / 0 | 10 / 0 |
| notsofar-far | 80 | 14363 | 38.230 / 38.794 | 73.091 / 73.091 | 3 / 3 | 80 / 0 |

Accuracy counts correctly labelled reference lines, with UNKNOWN wrong; “first / last” means successive live displays, not offline final processing. The last column counts streams ending on native labels or clustering. Five streams lose first-display accuracy and six lose last-display accuracy:

| Regressing stream | Reference lines | First change (percentage points) | Last change (percentage points) |
|---|---:|---:|---:|
| NOTSOFAR MTG_32105 | 72 | −12.500 | −15.278 |
| NOTSOFAR MTG_32256 | 56 | −1.786 | −1.786 |
| LOTUSDIS S010_T038 near | 127 | −0.787 | −0.787 |
| LOTUSDIS S016_T008 near | 252 | −1.190 | −1.587 |
| Commons (9 September 2026) | 1253 | +0.080 | −0.080 |
| Lords (2 September 2026) | 1376 | −0.218 | −0.145 |

Lords loses one net first-display line before switching to clustering and two afterwards; its last-display loss is two before switching. Commons gains one first-display line overall but loses one last-display line before switching. No loss remains on parliamentary lines crossing the switch. Later labels affect the global identity mapping, so these phases alone do not establish acoustic cause. The [JSON](nemotron-live-710.json) retains gross gains/losses and every development delta.

## Clear Turns Of At Most One Second

| Clear ≤1 s cohort | n | Today's first / last % | This PR first / last % |
|---|---:|---:|---:|
| chime6-far | 1033 | 6.389 / 8.809 | 70.474 / 70.474 |
| chime6-near | 1033 | 11.229 / 14.134 | 78.800 / 78.800 |
| lotusdis-far | 62 | 1.613 / 1.613 | 24.194 / 24.194 |
| lotusdis-near | 62 | 9.677 / 14.516 | 75.806 / 75.806 |
| notsofar-far | 626 | 16.454 / 17.891 | 83.866 / 83.866 |

LOTUSDIS includes eight turns with no reference line window as UNKNOWN within 62. These scores use a global one-to-one speaker map. Earlier NeMo scores used a different, per-speaker mapping: CHiME-6 far 6.00 → 72.41%, NOTSOFAR far 18.08 → 84.46%, LOTUSDIS near 11.11 → 85.19%. Historical NOTSOFAR denominators differed (603/605), and its combined engine completed only 77/80 streams; these are historical comparisons, not new tests.

## Method

Replay uses reference line windows and the time model output became available. Native inference is not flushed at end-of-file. The primary historical convention releases remaining reference windows then; the JSON also reports a sensitivity check leaving unseen suffix windows UNKNOWN with unchanged denominators. This affects six development and 39 held-out windows. All 27,913 development windows remain (27,870 scoreable lines; 43 parliamentary rows ineligible), as do 55,169 held-out windows (55,168 scoreable lines). Audio totals are 26.162 and 37.543 hours, counting paired microphones separately; none ends beyond the audio or is dropped. Twelve optional AMI confirmation streams are excluded from the historical native comparison.

For original AMI headset, far-field, Lords and London respectively, today's first-display scores are 59.120/41.552/96.189/96.059%; native HF alone 78.065/76.285/25.015/80.859%; the earlier combined engine 78.065/76.285/96.374/96.341%; production 78.065/76.285/96.189/96.341%. The earlier combination used future clustering labels and prediction times; production fixes the mapping from overlap available when output arrives. Historical scores used float16 storage; production sums raw fp32 probabilities.

The slot-recycling fix reuses raw-fp32 native results and cached clustering outputs. Ten original development streams use retained research inference; 22 additional development and 110 held-out streams use fresh verified production-loader inference. Clustering caches are checked for time grids, configuration and window counts; current window hashes do not independently verify their historical audio. Training-contaminated AMI is separate; ICSI word references omit some laughter, breathing and noise. These anonymous-speaker scores measure neither named-person recognition nor ASR quality; no combined frame hypothesis or DER is defined. **Live timing measures Nemotron alone, not the whole lane** ([timing scope](nemotron-710.md#method)).

Paced audio, backlogs, overlapping clips and atomic restarts agree exactly on both large development fixtures, including audio hashes, output availability, switch and mapping. Five native checkpoints cover FIFO-only, populated and compressed caches; array round-trips are exact and resumed next-packet probability drift is zero. Clustering label histories match classic on both large fixtures and a 161-cluster recycling stress case. Only the combined engine owns display numbers; a reused clustering slot gets a fresh number. All five new tests fail under deliberate mutations. JSON retains counts, unknowns, sensitivity results, comparisons and code fingerprints.
