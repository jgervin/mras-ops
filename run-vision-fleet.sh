#!/usr/bin/env bash
# TODO-8 Phase A: one native mras-vision process per ACTIVE cameras registry row.
#
# Each row must carry calibration->>'cam_index' (the local capture device this
# camera is plugged into); rows without it are skipped with a warning.
# Ports: 8001, 8011, 8021, ... macOS camera permission is PER PROCESS and must
# be granted from the owner's terminal on first run (same as today, xN).
#
# COMPUTE GUARDRAIL (spec decision 15): two-camera ceiling on the M3 until
# TODO-2. Use FRAME_SAMPLE_RATE_ATTENTION to run watcher cameras lighter.
#
# Per-camera logs: each run-vision-native.sh instance is backgrounded directly
# (not through a pipeline) so that `$!` captures the real vision/uvicorn
# process — this is what INT/TERM must kill on shutdown. Output goes to
# LOG_DIR/vision-<screen_id or cam_id>.log instead of a prefixed stdout stream;
# tail the logfile to follow a given camera. LOG_DIR defaults to logs/ under
# this repo (override with LOG_DIR=... to redirect elsewhere, e.g. /tmp).
set -euo pipefail

OPS_DIR="$(cd "$(dirname "$0")" && pwd)"
DATABASE_URL="${DATABASE_URL:-postgresql://mras:mras@localhost:5432/mras}"
LOG_DIR="${LOG_DIR:-$OPS_DIR/logs}"
mkdir -p "$LOG_DIR"

rows="$(psql "$DATABASE_URL" -At -F'|' -c \
  "SELECT id::text, COALESCE(screen_id,''), COALESCE(calibration->>'cam_index','')
     FROM cameras WHERE status = 'active' ORDER BY created_at")"

if [[ -z "$rows" ]]; then
  echo "no active cameras rows in the registry — nothing to launch" >&2
  exit 1
fi

pids=()
i=0
while IFS='|' read -r cam_id screen_id cam_index; do
  if [[ -z "$cam_index" ]]; then
    echo "SKIP $cam_id (screen_id=${screen_id:-?}): calibration.cam_index not set" >&2
    continue
  fi
  port=$((8001 + 10 * i))
  log_file="$LOG_DIR/vision-${screen_id:-$cam_id}.log"
  echo "launching camera $cam_id (screen_id=$screen_id cam_index=$cam_index port=$port) -> $log_file"
  CAMERA_ID="$cam_id" CAM_INDEX="$cam_index" VISION_PORT="$port" \
    "$OPS_DIR/run-vision-native.sh" >"$log_file" 2>&1 &
  pid=$!
  echo "  pid=$pid log=$log_file"
  pids+=("$pid")
  i=$((i + 1))
done <<< "$rows"

if [[ $i -eq 0 ]]; then
  echo "no launchable rows (every active camera is missing calibration.cam_index)" >&2
  exit 1
fi

trap 'kill "${pids[@]}" 2>/dev/null || true; wait || true' INT TERM
wait
