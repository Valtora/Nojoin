# Native Nemotron evidence for #710

Native Transformers is accepted as a separately evaluated diarisation engine.
**Numerical/output parity with NeMo fails.** Similar mapped AMI accuracy does not
mean identical probabilities, cache selections, channel activity, emission clocks
or endpoint lengths. This record preserves completed development studies; it is
not a new held-out experiment. The [causal live integration record](nemotron-live-710.md)
reports its separate dev acceptance and held-out implementation check, including
parliamentary regressions. Final-pass integration remains a separate gate.

The [machine-readable record](nemotron-710.json) contains all ten dev streams,
additive strict first-display counts and zero-collar DER components, original and
corrected comparator provenance, frame counts, threshold disagreement, compression
checkpoints and SHA256 hashes of the input/scorer/script/output artefacts. The
historical comparison is the actual retained NeMo output (Torch 2.11.0+cu129,
float16 probability storage, stochastic frontend dither). The corrected control is
separate NeMo eval-mode inference on the same Torch 2.14.1+cu130 worker as native HF,
with deterministic CUDA frontend and fp32 outputs. Initial CPU-frontend NeMo
diagnostics are superseded and excluded.

Historical NeMo source was `cf724ac337`; the corrected control used source
`68a12fc5e469614abd23b25ff9cfcb822aa0be93` plus its recorded compatibility patch.
Its copied package metadata still reports `3.1.0+cf724ac33`; that metadata string
does not identify the corrected source. The JSON retains both explicitly.

All streams use chronological 32,000-sample packets at 16 kHz, low-latency 9+4,
FIFO/cache 264 and update 222. HF and corrected-control inference are fp32.
Historical-compatible HF accuracy uses stored float16 probabilities; the record
also preserves HF fp32 sensitivity. Activity is literal probability `>0.5`.
Numerical comparisons fit no channel permutation; reference DER does. Strict
first display counts UNKNOWN as wrong, and DER includes overlap with zero collar.
Pools sum counts/seconds rather than average percentages.

| Dev stream | HF / control frames | max absolute Δp | p99 absolute Δp | threshold agreement % |
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

These are anonymous cluster/argmax scores on reference line windows. They measure
neither named-person recognition nor end-to-end ASR quality. The AMI microphones
are correlated observations of four meetings. Standalone eight-channel native
inference degrades on Lords; close small-meeting results must not erase that
limitation. The retained retrospective hybrid used future fallback labels and
does not establish the causal production mapper's accuracy. It has no defined
hybrid frame DER.

The first native raw window is 16,680 samples; the historical wrapper used 16,640.
Native later windows omit the historical extra left embedding. Changed posterior
scores can change cache frame selection after overflow/compression. Native
floor-rounded EOF differs from historical ceil/padded EOF, sometimes by one frame;
true emission clocks were measured without backdating. These contract differences
explain why source-weight equality cannot imply output equality.

The record's `source_parity` establishes equality of all **417 tensors** in the
official safetensors and retained conversion, with no missing keys or mismatches.
Processor configs and all five processor cases (first/later/EOF/short/offline)
match exactly. Complete loaded model configs differ only in `_name_or_path` and
saved `architectures` metadata; these differences are retained explicitly.
Official revision is `f667ed73aee57d40cc39428eb768b4fd87a0a29e`; weight SHA256 is
`c074d86335b3b794f8fa5edc25594558f128bdb3914d27806a3a5a2e44963cb6`.
The record and fixture bind the canonical three-file asset manifest digest and
Transformers 5.20.2. No converter or NeMo dependency enters the shipped runtime.

The [fixed PCM golden](../../backend/tests/fixtures/nemotron/README.md) uses the
production official-loader fp32/SDPA/CUDA frontend. Its first 21.13 seconds of
ES2004d cross FIFO overflow at EOF; this short fixture does not cover cache
compression. Three deterministic repeats
produced zero posterior/cache drift; pre-registered posterior and cache tolerances
and their policy hash are recorded in its manifest. CPU integration replay checks
the actual scheduling boundary; the separate opt-in CUDA regression computes
native features/cache/posteriors and continues from reconstructed checkpoints.
Ordinary upstream CI explicitly skips that model gate when opt-in/CUDA/local assets
are absent. The fixture is a development regression example, not held-out evidence.

The runtime checks CPU/CUDA feature equivalence when its process-local model
loads, then validates inputs, probabilities and CPU cache storage during
inference. Historical approximately 42 ms research packet timings are not a
latency guarantee for the complete live lane, its shadow engine, checkpointing,
ASR or queue contention.

Recompute the tables offline from shipped counts, without importing Torch,
Transformers or NeMo:

```sh
python scripts/evaluate_nemotron.py --recorded-only
```

For an audit of the explicitly supplied local retained `hf-parity` directory:

```sh
python scripts/evaluate_nemotron.py --retained-root /local/retained/hf-parity
```

That command verifies the stored source/output hashes before recomputing tables
from the retained counts. It does not rerun models or silently download missing
inputs. A fresh probability rescore additionally needs the original reference
regions/line windows and frozen scorers identified in JSON; those large research
inputs are not packaged here. The JSON names `runs-corrected`, `accuracy-corrected`
and the original provenance artefacts by digest, so the record remains useful
without the original workstation paths.

Provenance keys are portable identities (`hf-parity/`, `dev-inputs/`,
`dev-audio/`, `package-source/` and `source-archive/`), replacing workstation
absolute paths while preserving the original byte digests. The source-config
`_name_or_path` difference is represented as `<verified local snapshot>`.
Unnormalised research evidence remains local; no local scratch link is needed to
read or recompute this shipped record.

Later evidence must remain attributed correctly: live held-out gains were measured
with NeMo, while offline final held-out inference used native HF through the now
verified-equal conversion. The [default causal live replay](nemotron-live-710.md)
now has separate production evidence; final union integration remains a separate
acceptance gate. Existing final routing evidence also
reports ICSI confusion/line gains with false alarm +8.3 pp and DER +2.45 pp; that
limitation is not removed by this runtime commit. No held-out outcomes were retuned
or rerun for this record.
