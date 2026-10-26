# Native Nemotron evaluation for #710

Native Transformers is accepted as a separately evaluated diarisation engine, but **its output does not match NeMo numerically**. Similar AMI accuracy hides differences in probabilities, speaker channels, cache contents, timing and length. These are completed studies. The [live record](nemotron-live-710.md) reports implementation checks and regressions; the [final pass](#final-speaker-pass) reduces pooled held-out error but worsens ICSI.

## Native Output Comparison

These ten development streams compare native Hugging Face (HF) inference with NeMo on the same worker. Counts, error durations, hashes and cache checkpoints are in the [JSON](nemotron-710.json).

| Development stream | HF / control frames | max absolute Δp | p99 absolute Δp | threshold agreement % |
|---|---:|---:|---:|---:|
| Mix-Headset EN2002a | 214270 / 214271 | 0.9958172 | 0.0653887 | 99.77791 |
| Mix-Headset ES2004d | 222229 / 222230 | 0.9874513 | 0.0296129 | 99.90303 |
| Mix-Headset IS1009d | 194450 / 194450 | 0.9736536 | 0.0580534 | 99.81338 |
| Mix-Headset TS3003d | 261820 / 261820 | 0.9967787 | 0.0327292 | 99.88632 |
| Array1-01 EN2002a | 214270 / 214271 | 0.9828223 | 0.0972304 | 99.64169 |
| Array1-01 ES2004d | 222229 / 222230 | 0.9474784 | 0.0898682 | 99.75611 |
| Array1-01 IS1009d | 194466 / 194467 | 0.9480852 | 0.1199794 | 99.56978 |
| Array1-01 TS3003d | 261820 / 261820 | 0.8654275 | 0.0677830 | 99.78406 |
| Lords | 900000 / 900001 | 0.9995532 | 0.9789420 | 87.54300 |
| London | 1089909 / 1089910 | 0.9955772 | 0.4632799 | 98.67606 |

| Cohort | Historical NeMo first / DER % | Corrected NeMo first / DER % | Native HF first / DER % |
|---|---:|---:|---:|
| AMI headset | 78.300 / 26.979 | 78.099 / 26.948 | 78.065 / 26.872 |
| AMI far-field | 76.352 / 28.839 | 76.352 / 28.565 | 76.285 / 28.416 |
| Lords | 28.273 / 76.060 | 29.318 / 74.320 | 25.015 / 82.921 |
| London | 78.888 / 27.283 | 80.084 / 25.967 | 80.859 / 25.782 |

These scores identify anonymous speakers on reference line windows, not people or ASR quality. AMI's microphones are correlated views of four meetings. Standalone native inference worsens Lords. The earlier combined live engine used future clustering labels, so it cannot establish production accuracy and has no combined frame DER.

### Method

Packets contain 32,000 samples at 16 kHz; streaming settings are 9+4, FIFO/cache 264, update 222. HF and corrected NeMo use fp32; historical-compatible HF scores use stored float16 probabilities, with fp32 sensitivity in JSON. Δp is the probability difference; p99 is its 99th percentile. Activity requires probability `>0.5`. Numerical comparisons do not remap channels; DER does, includes overlap and allows zero boundary tolerance (zero collar). UNKNOWN is wrong; pools add counts and seconds before division.

Historical NeMo used Torch 2.11.0+cu129, float16 storage and random frontend dither. Corrected NeMo uses evaluation mode, deterministic CUDA features and Torch 2.14.1+cu130, as does HF; earlier CPU-frontend diagnostics are excluded. Historical source is `cf724ac337`; corrected source is `68a12fc5e469614abd23b25ff9cfcb822aa0be93` plus the recorded patch, despite copied metadata `3.1.0+cf724ac33`.

Native starts with 16,680 samples versus 16,640 historically, omits a later left embedding, and rounds end-of-file down rather than padding up, sometimes differing by one frame. Changed probabilities can change cache selection. Availability times are measured when output arrives, without backdating.

All **417 tensors**, processor configurations and five processor cases (first, later, end-of-file, short, offline) match the official assets. Loaded model configurations differ only in `_name_or_path` and `architectures`. Revision: `f667ed73aee57d40cc39428eb768b4fd87a0a29e`; weight SHA256: `c074d86335b3b794f8fa5edc25594558f128bdb3914d27806a3a5a2e44963cb6`. JSON and fixture bind the three-file manifest and Transformers 5.20.2. The shipped runtime needs neither conversion nor NeMo.

The [regression fixture](../../backend/tests/fixtures/nemotron/README.md) uses the production loader and fp32/SDPA/CUDA features. Its 21.13 seconds of ES2004d cover FIFO overflow at end-of-file, but not cache compression. Three repeats had zero probability/cache drift; the manifest fixes tolerances and their policy hash. CPU replay checks scheduling; opt-in CUDA tests compute features, probabilities and cache, then resume saved checkpoints. CI skips inference without opt-in, CUDA or local assets. This is development evidence.

The runtime checks CPU/CUDA feature agreement at load and validates inputs, probabilities and CPU cache during inference. The approximately **42 ms live timing measures Nemotron alone**, excluding production validation, clustering, checkpointing, ASR and queue delays; it does not measure the whole lane.

Recompute shipped counts offline without ML dependencies:

```sh
python scripts/evaluate_nemotron.py --recorded-only
```

To verify retained source/output hashes and recompute their counts:

```sh
python scripts/evaluate_nemotron.py --retained-root /local/retained/hf-parity
```

Neither command runs models or downloads inputs. Rescoring probabilities needs original reference regions, line windows and frozen scorers, identified by digest but not packaged. Portable keys (`hf-parity/`, `dev-inputs/`, `dev-audio/`, `package-source/`, `source-archive/`) replace workstation paths without changing hashes; `_name_or_path` becomes `<verified local snapshot>`. JSON identifies `runs-corrected`, `accuracy-corrected` and original provenance artefacts. Historical live held-out gains used NeMo; production live uses HF. Final held-out inference used converted HF weights that match the official tensors.

## Final Speaker Pass

The final pass combines community-1 and offline Nemotron unless **Nemotron ever uses its eighth speaker channel**, in which case it keeps community-1 unchanged. It uses complete audio, independently of live labels, without reference speaker counts, scoring-region cropping or line decisions.

Offline and original live-based routing agree on all **108 held-out streams** and all **120 paired streams**, including development and parliamentary data. All small held-out streams select the union; all five large meetings keep community-1. The 108 streams represent 94 meetings; paired microphones are correlated and counted separately. **These are previously examined held-out data, so these figures check the implementation**, with no retuning or new small held-out inference.

### Live: First-display Line Accuracy

| Held-out cohort | Streams / reference lines | Today's engine % | This PR % |
|---|---:|---:|---:|
| CHiME-6 far | 4 / 16341 | 12.325 | 55.272 |
| CHiME-6 near | 4 / 16341 | 18.481 | 61.906 |
| NOTSOFAR far | 80 / 14363 | 38.230 | 73.091 |
| LOTUSDIS far | 10 / 2747 | 52.093 | 78.194 |
| LOTUSDIS near | 10 / 2747 | 78.231 | 84.565 |
| All small held-out | 108 / 52539 | 26.847 | 64.937 |
| Commons (9 September 2026) | 1 / 1253 | 94.014 | 94.094 |
| Lords (2 September 2026) | 1 / 1376 | 94.985 | 94.767 |

First-display line accuracy is the percentage of reference speech lines assigned the correct anonymous speaker when first shown; UNKNOWN counts as wrong.

Four small meetings and held-out Lords lose lines at first display despite the pooled gain; Commons also loses one line by its last live display. See the [live regression table](nemotron-live-710.md#held-out-implementation-check).

### Final: Diarisation Error

Each cell shows **community-1 → union**, using the offline route.

| Held-out cohort | Streams | DER % | Speaker confusion % | Missed speech % | False alarm % |
|---|---:|---:|---:|---:|---:|
| CHiME-6 far | 4 | 52.485 → 40.393 | 10.830 → 6.332 | 39.697 → 31.913 | 1.959 → 2.148 |
| CHiME-6 near | 4 | 45.824 → 39.949 | 11.427 → 9.320 | 32.033 → 28.023 | 2.364 → 2.606 |
| NOTSOFAR far | 80 | 28.082 → 14.795 | 11.334 → 2.542 | 10.307 → 7.233 | 6.442 → 5.020 |
| LOTUSDIS far | 10 | 45.309 → 32.099 | 11.204 → 5.308 | 32.840 → 26.414 | 1.265 → 0.377 |
| LOTUSDIS near | 10 | 27.318 → 21.414 | 3.860 → 1.014 | 22.871 → 20.034 | 0.587 → 0.366 |
| CHiME-6 pooled | 8 | 49.155 → 40.171 | 11.128 → 7.826 | 35.865 → 29.968 | 2.161 → 2.377 |
| LOTUSDIS pooled | 20 | 36.313 → 26.757 | 7.532 → 3.161 | 27.855 → 23.224 | 0.926 → 0.372 |
| All small held-out | 108 | 41.958 → 31.827 | 10.613 → 5.794 | 28.327 → 23.323 | 3.018 → 2.711 |

DER adds speaker confusion, missed speech and false alarm as percentages of reference speaker time; it includes overlap and uses zero collar within the original scored regions (UEM).

**The live and final lanes use different metrics and are not compared with each other.** Final processing assigns speakers per word and can split lines, so whole-line majority accuracy does not describe its transcript output. All four predeclared quality requirements held at each corpus aggregate and on **87/108** individual streams. Lords and London retain community-1, with historical development DER of 16.081% and 8.213%.

**ICSI regresses:** across ten development streams, confusion falls 5.747% → 2.250%, but false alarm rises **25.658% → 33.936% (+8.278 percentage points)** and DER worsens **35.777% → 38.227% (+2.450 points)**. Both microphone groups regress. These recordings had no original live routing evidence; extending offline processing to imports adds this measured cost. Across all 26 small development streams, false alarm rises 3.280 points despite lower overall DER.

### Method

Offline inference uses the CPU frontend and 340+40 settings. Routing tests `float16(probability) > 0.5` on channel index 7; exactly 0.5 is inactive. Native intervals use the original float32 `>0.5` threshold and retain overlap and every positive fragment. Routing selects complete cached study results; the tables preserve those frozen scores.

For audit, the earlier whole-window majority scorer gave community/union percentages of CHiME-6 far 45.352/52.665, near 49.642/50.566; NOTSOFAR 48.263/58.289; LOTUSDIS far 64.361/76.993, near 80.379/80.561; pooled 50.307/56.280 (26431/52539 and 29569/52539 correct). ICSI scored 67.569/70.362; unchanged Lords and London scored 97.050 and 95.989 on 1627 and 1421 lines. These historical scores do not measure the word-by-word transcript. Live's small-meeting total is 34117/52539; development Lords first/last live scores are 96.189/97.972 and London 96.341/96.833. The [live method](nemotron-live-710.md#method) explains the historical end-of-file visibility convention and its sensitivity check.

### Production Wrapper Checks

Cached replay matched all **28** development streams (16 AMI, 10 ICSI, Lords, London): 24 unions and four unchanged community results. Union intervals matched the study and saved RTTMs within **1e-9 seconds**; line scores matched exactly and observed error/reference-duration differences were zero against a 1e-6-second bound. Four subtraction fragments, at most 2.274e-13 seconds each, cannot be represented as pyannote segments. Comparison rounds endpoints to nine decimal places; production has no duration filter. Merging same-label source intervals changed no union-selected rows.

Fresh native runs on three representative small streams (original AMI, additional AMI, ICSI) and all five large meetings exactly matched cached float32 arrays, shapes and thresholds in both precisions. All large meetings kept community-1. These checks reused cached community results except for the full Lords run below. Additional AMI without cached final/union results were excluded; no small held-out model was rerun.

Fresh Lords community output differs from its historical cache: today's CUDA call precedes phantom filtering; the older CPU baseline included that filtering. Both have 51 labels. Scoring the fresh millisecond RTTM gives 97.173% whole-line accuracy and 13.141% DER versus historical 97.050% and 16.081%. The wrapper returned the fresh community object unchanged; this is baseline drift, separate from the frozen tables.

### Sequential Memory And Time

A full **9000.003-second** Lords development meeting took **194.391 seconds**: about 183.3 for community-1 and 10.4 for native loading/inference, plus audio reading and release. Native exactly matched the cached **900001 × 8** float32 matrix with no threshold disagreements; its eighth channel kept the fresh community object unchanged.

| Same-process measurement | Allocated MiB | Reserved MiB |
|---|---:|---:|
| Community-1 peak | 1628.822 | 2008 |
| Community-1 complete, before release | 39.129 | 2008 |
| After community release, before native load | 8.125 | 20 |
| Native load and inference peak | 1487.630 | 1576 |
| After native release | 8.125 | 20 |

Weak references confirmed each model was collected after use, with community released before native loading. The maximum stage peak was 1628.822 MiB allocated / 2008 MiB reserved; sequential peaks do not add together. Device usage fell 4780 → 2792 MiB at community release. Allocator figures exclude CUDA context, desktop and other idle allocations. The run used an NVIDIA GeForce RTX 4070 Ti, fp32/SDPA, Transformers 5.20.2, Torch 2.14.1+cu130 and four CPU threads in a disposable container under the shared benchmark lock, with no competing model work. One meeting establishes release order and an observed cost, not minimum capacity or a timing guarantee.
