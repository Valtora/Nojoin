#!/usr/bin/env bash
# Screen study (#653): run the analysis on the stored frames, in a capped
# container with no network, the frames and the timeline mounted read-only.
# It writes summary.json into OUT_DIR and nothing else.
#
#   scripts/speaker_frames/run_study.sh FRAMES_DIR TIMELINE_JSONL OUT_DIR PREREG_SHA256
#
# FRAMES_DIR is live's data/speaker_frames. TIMELINE_JSONL is the output of
# export_timeline.sql. OUT_DIR must not exist yet or be empty.
# PREREG_SHA256 is the hash posted on #653.
set -euo pipefail

if [ "$#" -ne 4 ]; then
  echo "usage: $0 FRAMES_DIR TIMELINE_JSONL OUT_DIR PREREG_SHA256" >&2
  exit 2
fi
frames=$(realpath "$1")
timeline=$(realpath "$2")
out=$(realpath -m "$3")
sha=$4
here=$(cd "$(dirname "$0")" && pwd)

[ -d "$frames" ] || { echo "no frames folder at $frames" >&2; exit 2; }
[ -f "$timeline" ] || { echo "no timeline at $timeline" >&2; exit 2; }
mkdir -p "$out"
[ -z "$(ls -A "$out")" ] || { echo "$out is not empty" >&2; exit 2; }

# The frames are mode 0600, so read them as their owner. The output folder
# is opened to that owner for the run only.
owner=$(stat -c '%u:%g' "$frames")
chmod 0777 "$out"
trap 'chmod 0755 "$out"' EXIT

docker run --rm --name agent-653-study --network none \
  --memory 3g --memory-swap 3g --cpus 4 --pids-limit 256 --oom-score-adj 500 \
  --user "$owner" --cap-drop ALL --security-opt no-new-privileges \
  --read-only --tmpfs /tmp:size=16m \
  -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$here":/analysis/speaker_frames:ro \
  -v "$frames":/frames:ro \
  -v "$timeline":/input/timeline.jsonl:ro \
  -v "$out":/out \
  -w /analysis --entrypoint python nojoin-dev-tests:local \
  -B -m speaker_frames.run --frames /frames --timeline /input/timeline.jsonl \
  --prereg /analysis/speaker_frames/PREREG.md --prereg-sha256 "$sha" --out /out --workers 4

written=$(ls -A "$out")
if [ "$written" != "summary.json" ]; then
  echo "unexpected output in $out: $written" >&2
  exit 1
fi
echo "$out/summary.json"
