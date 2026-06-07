#!/usr/bin/env bash
#
# start-mras.sh — bring up the whole MRAS / AdFace system with one command.
#
# Order:
#   1. Start Docker Desktop (if not already running) and wait for the daemon.
#   2. docker compose up -d --build  → postgres, qdrant, mras-composer, mras-ops-api,
#      mras-ops-frontend  (mras-vision is intentionally NOT in here — see below).
#   3. Wait for service health.
#   4. exec run-vision-native.sh  → mras-vision runs NATIVELY so it can use the webcam
#      (macOS cannot pass a camera into Docker). This stays in the foreground; Ctrl-C
#      stops vision. The Docker stack keeps running detached.
#
# Usage:
#   ./start-mras.sh                 # built-in webcam (CAM_INDEX=0)
#   CAM_INDEX=1 ./start-mras.sh     # external camera
#
set -euo pipefail

OPS_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$OPS_DIR"

say() { printf "\n\033[1;36m▶ %s\033[0m\n" "$1"; }
ok()  { printf "  \033[1;32m✓ %s\033[0m\n" "$1"; }
die() { printf "\n\033[1;31m✗ %s\033[0m\n" "$1" >&2; exit 1; }

wait_http() {  # name url max_tries
  local name="$1" url="$2" tries="${3:-60}"
  for ((i=1; i<=tries; i++)); do
    if curl -fsS -o /dev/null "$url" 2>/dev/null; then ok "$name healthy"; return 0; fi
    sleep 2
  done
  die "$name did not become healthy at $url"
}

# 1. Docker daemon -----------------------------------------------------------
say "Checking Docker"
if ! docker info >/dev/null 2>&1; then
  say "Starting Docker Desktop…"
  open -a Docker || die "Could not launch Docker Desktop"
  for ((i=1; i<=60; i++)); do
    if docker info >/dev/null 2>&1; then break; fi
    sleep 5
  done
  docker info >/dev/null 2>&1 || die "Docker daemon never came up"
fi
ok "Docker daemon running"

# 2. Compose stack -----------------------------------------------------------
say "Building & starting the Docker stack (postgres, qdrant, composer, ops-api, ops-frontend)"
docker compose up -d --build
ok "Containers started"

# 3. Health checks -----------------------------------------------------------
say "Waiting for services"
wait_http "qdrant"        "http://localhost:6333/readyz"    60 || \
  wait_http "qdrant"      "http://localhost:6333/"          1
wait_http "mras-composer" "http://localhost:8002/health"    60
wait_http "mras-ops-api"  "http://localhost:8080/health"    60
wait_http "ops-frontend"  "http://localhost:3000/"          60

cat <<'EOF'

  Dashboards:
    • Activity feed : http://localhost:3000
    • Composer      : http://localhost:8002/health
  Kiosk display (separate terminal):
    cd /Users/jn/code/mras-display && NODE_ENV=development npm run electron:dev

EOF

# 4. Native vision (foreground; needs camera permission) ---------------------
say "Starting mras-vision natively (camera). Grant camera permission if macOS asks. Ctrl-C to stop."
exec "$OPS_DIR/run-vision-native.sh"
