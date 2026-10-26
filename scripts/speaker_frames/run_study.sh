#!/usr/bin/env bash
# Screen study (#653): run the analysis on the stored frames, in a capped
# container with no network, the frames and the timeline mounted read-only.
# It writes summary.json into OUT_DIR and nothing else.
#
#   scripts/speaker_frames/run_study.sh FRAMES_DIR TIMELINE_JSONL OUT_DIR PREREG_SHA256
#
# FRAMES_DIR is live's data/speaker_frames. TIMELINE_JSONL is the output of
# export_timeline.sql. OUT_DIR must not exist yet or be empty.
# PREREG_SHA256 is the hash posted on #653. GATE_PARTS and STUDY_LOCK, both
# optional, are described below.
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
parts_dir=""
cleanup() {
  chmod 0755 "$out"
  if [ -n "$parts_dir" ]; then rm -rf "$parts_dir"; fi
}
trap cleanup EXIT

# GATE_PARTS=N runs the synthetic gate first as N separate containers, each
# writing one part's counts, so no single run is long: the whole gate, 30
# meetings of 20 minutes, takes some two hours on these 4 cores, so
# GATE_PARTS=10 keeps each part near 12 minutes. STUDY_LOCK=PATH takes that
# lock with flock around each container.
parts=${GATE_PARTS:-1}
lock=${STUDY_LOCK:-}

study() {
  local into=$1
  shift
  local cmd=(docker run --rm --name agent-653-study --network none
    --memory 3g --memory-swap 3g --cpus 4 --pids-limit 256 --oom-score-adj 500
    --user "$owner" --cap-drop ALL --security-opt no-new-privileges
    --read-only --tmpfs /tmp:size=16m
    -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1
    -v "$here":/analysis/speaker_frames:ro
    -v "$frames":/frames:ro
    -v "$timeline":/input/timeline.jsonl:ro
    -v "$into":/out
    "${mounts[@]}"
    -w /analysis --entrypoint python nojoin-dev-tests:local
    -B -m speaker_frames.run --prereg /analysis/speaker_frames/PREREG.md
    --prereg-sha256 "$sha" --out /out --workers 4 "$@")
  if [ -n "$lock" ]; then
    flock "$lock" "${cmd[@]}"
  else
    "${cmd[@]}"
  fi
}

mounts=()
gate_args=()
if [ "$parts" -gt 1 ]; then
  parts_dir=$(mktemp -d)
  for k in $(seq 1 "$parts"); do
    mkdir "$parts_dir/$k"
    chmod 0777 "$parts_dir/$k"
    study "$parts_dir/$k" --gate-part "$k/$parts"
    gate_args+=("/gate/$k/summary.json")
  done
  mounts=(-v "$parts_dir":/gate:ro)
  gate_args=(--gate-from "${gate_args[@]}")
fi

study "$out" --frames /frames --timeline /input/timeline.jsonl "${gate_args[@]}"

written=$(ls -A "$out")
if [ "$written" != "summary.json" ]; then
  echo "unexpected output in $out: $written" >&2
  exit 1
fi
echo "$out/summary.json"
