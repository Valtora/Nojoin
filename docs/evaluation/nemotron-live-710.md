# Native live-label production replay

Development acceptance passed the bars declared before scoring. The held-out implementation check includes regressions reported below. This is an oracle-line emission-clock replay. Native EOF is not flushed; the primary study releases remaining oracle lines at EOF, with a separately labelled natural-visibility sensitivity.

| Dev gate | Boundary | Production |
|---|---:|---:|
| Original AMI pooled first strict | 76.675008% | 4595/5954 = 77.175008% |
| Lords first strict | 95.189305% | 1565/1627 = 96.189305% |
| London first strict | 95.059113% | 1369/1421 = 96.340605% |

Paced, backlog, overlapping clips and atomic checkpoint restarts agree exactly on both large dev fixtures, including acoustic window digests, emission receipts, switch and translation.

## Development

| Cohort | Streams | Reference lines | Current first / final strict % | Production first / final strict % | Production UNKNOWN first / final | Native / hand-back |
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

| Cohort | Streams | Reference lines | Current first / final strict % | Production first / final strict % | Production UNKNOWN first / final | Native / hand-back |
|---|---:|---:|---:|---:|---:|---:|
| chime6-far | 4 | 16341 | 12.325 / 13.071 | 55.272 / 55.272 | 0 / 0 | 4 / 0 |
| chime6-near | 4 | 16341 | 18.481 / 19.883 | 61.906 / 61.906 | 0 / 0 | 4 / 0 |
| commons-2026-09-09 | 1 | 1253 | 94.014 / 96.808 | 94.094 / 96.728 | 12 / 2 | 0 / 1 |
| lords-2026-09-02 | 1 | 1376 | 94.985 / 96.003 | 94.767 / 95.858 | 6 / 1 | 0 / 1 |
| lotusdis-far | 10 | 2747 | 52.093 / 52.676 | 78.194 / 78.194 | 0 / 0 | 10 / 0 |
| lotusdis-near | 10 | 2747 | 78.231 / 78.813 | 84.565 / 84.565 | 0 / 0 | 10 / 0 |
| notsofar-far | 80 | 14363 | 38.230 / 38.794 | 73.091 / 73.091 | 3 / 3 | 80 / 0 |

Implementation check of a bug fix on held-out data examined before; one frozen rerun after dev passed; no retuning. The optional twelve AMI confirmation streams are excluded from the historical native comparator cohort.

First strict regresses on 5 individual streams and final strict on 6; their aggregate scores and deltas are retained in the JSON. No held-out retuning was performed.

| Regressing stream | Reference lines | First delta pp | Final delta pp |
|---|---:|---:|---:|
| notsofar-MTG_32105 | 72 | -12.500 | -15.278 |
| notsofar-MTG_32256 | 56 | -1.786 | -1.786 |
| lotusdis-S010_T038-near | 127 | -0.787 | -0.787 |
| lotusdis-S016_T008-near | 252 | -1.190 | -1.587 |
| commons-2026-09-09 | 1253 | +0.080 | -0.080 |
| lords-2026-09-02 | 1376 | -0.218 | -0.145 |

| Clear ≤1 s cohort | n | Current first / final strict % | Production first / final strict % |
|---|---:|---:|---:|
| chime6-far | 1033 | 6.389 / 8.809 | 70.474 / 70.474 |
| chime6-near | 1033 | 11.229 / 14.134 | 78.800 / 78.800 |
| lotusdis-far | 62 | 1.613 / 1.613 | 24.194 / 24.194 |
| lotusdis-near | 62 | 9.677 / 14.516 | 75.806 / 75.806 |
| notsofar-far | 626 | 16.454 / 17.891 | 83.866 / 83.866 |

LOTUSDIS retains eight clear turns without an oracle line window as UNKNOWN in its denominator of 62. These short-turn scores and denominators are distinct from historical home-label M2.

## Retained dev comparison

| Cohort | Current first strict % | Native HF first strict % | Retrospective HF hybrid first strict % | Causal production first strict % |
|---|---:|---:|---:|---:|
| AMI headset | 59.120 | 78.065 | 78.065 | 78.065 |
| AMI far-field | 41.552 | 76.285 | 76.285 | 76.285 |
| Lords | 96.189 | 25.015 | 96.374 | 96.189 |
| London | 96.059 | 80.859 | 96.341 | 96.341 |

The retained hybrid maps future final fallback labels and switches on prediction time; production freezes causal overlap at the emission receipt. The retained comparison headline used float16-stored probabilities; production sums raw fp32.

All 27,913 dev oracle windows are retained; 27,870 are scoreable reference lines (43 parliamentary rows are ineligible for reference-line scoring). The held-out inventory retains 55,169 oracle windows and 55,168 scoreable reference lines. Full audio denominators are 26.162 dev hours and 37.543 held-out hours, counting paired views separately. Neither inventory has a window beyond the PCM end or a dropped window. The historical forced EOF visibility convention affects six dev and 39 held-out windows. The JSON reports natural-visibility sensitivity with these unseen suffix windows UNKNOWN and the same denominators.

Historical NeMo home-label M2 clear ≤1 s first scores were CHiME-6 far 6.00→72.41%, NOTSOFAR far 18.08→84.46%, and LOTUSDIS near 11.11→85.19%. They use a different identity mapping from the new strict short-turn table and are shown as historical evidence only.

## Scope and limits

- This slot fix reuses verified raw-fp32 native inference and cached shadow inputs; no new GPU inference.
- No hybrid frame hypothesis or hybrid DER; anonymous mapped identities, not named-person recognition or ASR quality.
- Clear <=1s scores use score_short clear turns with the global one-to-one line identity map; distinct from historical home-label M2.
- Primary release of oracle lines at EOF is a historical study convention; natural-visibility sensitivity retains the same denominators.
- Original ten dev probabilities are retained raw-fp32 research HF outputs; 22 data2 and 110 held-out streams use fresh verified official-loader inference.
- Shadow model outputs are historical caches with exact t-grid/config/window-count validation; current window digests do not independently revalidate historical inference PCM.
- AMI train-contaminated dev streams are reported separately; ICSI word references omit some laugh/breath/noise.
- Historical NOTSOFAR home-label M2 denominators differed 603/605 and historical hybrid completed 77/80; these are not repaired new hypothesis tests.
- Every dev stream remains within classic minus 1 percentage point; all per-stream deltas are retained.

The [accompanying JSON](nemotron-live-710.json) contains aggregate counts, unknowns, every dev fixture delta, natural-visibility sensitivity, research comparators and code fingerprints. Five real native checkpoints cover FIFO-only, populated cache and compressed cache; array round-trips are exact and resumed next-packet probability drift is zero.

## Slot recycling invariant

Shadow and plain classic full label histories agree on both large dev streams and the 161-cluster synthetic recycling stress. Display IDs live only in the hybrid layer; a recycled numeric slot receives a fresh ID. All five new tests fail on recorded deliberate mutations.

Remaining first-strict net losses comprise four native-only small streams and held-out Lords (one net line before hand-back, two after). Commons first strict now gains one line overall; its remaining final net loss is one line before hand-back. Held-out Lords' final net loss is two lines before hand-back. No parliamentary crossing-line loss remains. Global scoring ownership can be affected by later labels, so phase alone does not establish acoustic cause. The accompanying JSON retains aggregate gross losses and gains for every required control.
