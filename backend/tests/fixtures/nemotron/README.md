# Native Nemotron packet regression fixture

Audio: AMI Project, **AMI Meeting Corpus**, meeting ES2004d, Mix-Headset,
first 21.13 seconds. The [AMI corpus](https://groups.inf.ed.ac.uk/ami/corpus/)
publishes its signals under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Credit the AMI Project when redistributing this excerpt. The change from the
published source is sample-exact truncation to the first 338,080 mono PCM16
samples at 16 kHz; there is no resampling or lossy encoding. Source WAV SHA256:
`3cfce3297ebb7faeb53c02727eea5e9f2c76e9ed4b62db3b03d73e66836e91f3`.

`manifest.json` records exact artefact bytes/digests, the official asset manifest,
runtime fingerprint and repeat gate. `window_schedule.json` records every raw
float32 window digest, flags, source sample start, frame span and true availability
sample cursor. Windows come from 10 complete 32,000-sample packets, an 18,080-sample
tail, then EOF. The 29 calls emit 2,113 frames. Availability is never moved
earlier to match the posterior frame endpoint.

`native_packets.npz` stores raw fp32 sigmoid posteriors, shape `(2113, 8)`.
`state_checkpoints.npz` stores the live fp32 prefixes of native embeds/probs/FIFO
after call 28: empty speaker-cache arrays and 252 FIFO encoder frames. Both npz
files use lossless compression. Reconstruction zero-fills unused capacity, which
native attention excludes using the valid counts. The final cache metadata records
the EOF overflow: 222 speaker-cache frames and 43 FIFO frames, uncompressed.
The ordinary native-state tests also reconstruct this retained checkpoint through
the production numeric codec. Longer local integration probes exercise populated
and compressed cache states; this short checked-in fixture does not contain them.

This is the shortest prefix of this source that exercises the first window,
a FIFO overflow and EOF. Each ordinary call adds nine encoder frames. After
28 calls the FIFO contains 252 frames; EOF begins at sample 322,304 and needs
13 more frames to exceed capacity 264. The smallest EOF output with 13 encoder
frames is 97 mel frames, requiring 15,776 samples: 322,304 + 15,776 = 338,080.
One sample less yields 96 mel frames, 12 encoder frames and exactly 264 FIFO
frames, with no overflow. With at most 27 ordinary calls, EOF can add at most
13 frames, still below capacity; a 29th ordinary call needs 339,344 samples.
Real GPU probes on both sides confirmed the boundary. Output channels 0 and 2
are active above 0.5 on 368 and 80 frames respectively. AMI annotations also
contain three speakers before the endpoint. Cache compression requires a longer
excerpt and is not claimed by this fixture.

The fixture is a consumed development regression example. It supplies no held-out
accuracy evidence. Ordinary CPU replay runs the actual PCM packet/window buffer,
asserts the recorded source-window digests/order/flags and synthetic native-cache
sequence token, then returns recorded posteriors. It does not compute mel features,
native cache compression, hybrid labels, canonical writes or the final union.

The explicit CUDA test uses `load_nemotron_model`, retaining its full verifier and
local safetensors policy. A scoped test-only inspector selects the directory named
by `NOJOIN_NEMOTRON_MODEL_DIR`; production has no directory override. It checks
every real posterior and the stored live checkpoint, reconstructs native state
after call 28, and continues inference through the EOF FIFO overflow. It checks
the final counts and flags separately. The runtime checks CPU/CUDA feature
equivalence at process-local model load for the pinned window geometries;
ordinary inference validates its inputs, probabilities and CPU cache storage.

Three recorded generation repeats on RTX 4070 Ti (Ada), Torch 2.14.1+cu130,
Transformers 5.20.2, fp32/SDPA, matmul TF32 off and cuDNN TF32 on produced maximum
posterior/live-cache drift **0/0**, including transient EOF arrays. Regenerating
under Transformers 5.21.0 on the same card produced byte-identical arrays. Other GPU
architectures differ in the last digits: a Turing card (no TF32) deviated by up to
2.6e-6, and the same Ada card with cuDNN TF32 off by up to 8.6e-5. The posterior
policy is `atol=4e-4, rtol=1e-5`, four times the larger deviation, rounded up. The
cache policy stays `atol=1e-4, rtol=1e-5` (largest deviation in the TF32-off probe:
3.0e-5). The recorded arrays are unchanged. Policy bytes are in
`tolerance-policy.json`, bound by `tolerance_policy_sha256`. These are distinct
from the frontend's `atol=2e-4, rtol=1e-5`. Shape, flags, clocks, stored-float16
activity decisions and raw-probability argmax decisions remain exact. Deviations
beyond this tolerance require investigation; a regression must not be hidden by
widening the policy.

Run the ordinary replay without weights, CUDA, network or ffmpeg:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  pytest -q backend/tests/test_nemotron_replay.py backend/tests/test_nemotron_inference.py
```

Run the independent inference gate inside an authorised throwaway GPU container
with network disabled, after qualifying no competing compute and holding the
local benchmark lock:

```sh
NOJOIN_RUN_NEMOTRON_INFERENCE=1 \
NOJOIN_NEMOTRON_MODEL_DIR=/local/exact/verified/snapshot \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  pytest -q backend/tests/test_nemotron_inference.py
```

Explicit regeneration uses the shipped `generate.py`, outside pytest. Supply the
full source WAV above and the exact three official assets; it verifies both. Use
the current worker image containing the pinned dependencies, mount the checkout
and inputs read-only, and mount an empty staging directory writable. After
checking GPU process activity (desktop activity is permitted, another compute job
is not), hold the lock for the entire command. For example, with absolute paths
and an already built worker image:

```bash
exec 9>>/tmp/nojoin-bench.lock
flock -n 9
docker run --rm --pull=never --network=none --device nvidia.com/gpu=all \
  --read-only --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$CHECKOUT,dst=/work,readonly" \
  --mount "type=bind,src=$SOURCE_DIRECTORY,dst=/source,readonly" \
  --mount "type=bind,src=$MODEL_DIRECTORY,dst=/model,readonly" \
  --mount "type=bind,src=$STAGING_DIRECTORY,dst=/out" \
  --workdir /work -e PYTHONPATH=/work -e PYTHONDONTWRITEBYTECODE=1 \
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -e TMPDIR=/out -e XDG_CACHE_HOME=/out/cache -e NUMBA_CACHE_DIR=/out/cache/numba \
  -e OMP_NUM_THREADS=4 -e MKL_NUM_THREADS=4 -e OPENBLAS_NUM_THREADS=4 \
  --entrypoint /opt/venv/bin/python "$WORKER_IMAGE" -B \
  backend/tests/fixtures/nemotron/generate.py \
  --source /source/ES2004d.Mix-Headset.wav --model-dir /model --output /out \
  --policy /work/backend/tests/fixtures/nemotron/tolerance-policy.json
```

The recorded build used image ID
`sha256:411944339d70d5d410bd4365623cce99fa3aa654c1263dc7c503103babe60074`
plus the retained read-only Transformers additions; the new worker image installs
those dependencies directly. Generator output is staged until three repeats pass.
Review hashes, window clocks, all scalar decisions and the independent inference
test before replacing committed data. The complete committed fixture directory,
including tooling and this README, must remain below **1.5 MB (1,500,000 B)**. Never regenerate
expectations from pytest or implicitly fetch audio/models.
