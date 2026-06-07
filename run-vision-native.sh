#!/usr/bin/env bash
# Run mras-vision natively on macOS so it can access the physical camera.
# Docker cannot pass through the webcam on macOS — this is the live-demo path.
#
# First run creates a venv and installs deps automatically.
#
# Usage:
#   ./run-vision-native.sh              # uses CAM_INDEX=0 (built-in webcam)
#   CAM_INDEX=1 ./run-vision-native.sh  # external camera

set -euo pipefail

VISION_DIR="$(cd "$(dirname "$0")/../mras-vision" && pwd)"
VENV="$VISION_DIR/.venv"
ENV_FILE="$VISION_DIR/.env"

# ── venv bootstrap ────────────────────────────────────────────────────────────
if [[ ! -d "$VENV" ]]; then
  echo "Creating arm64 venv at $VENV ..."
  # /usr/bin/python3 is the only native arm64 build available (system Python 3.9).
  # Homebrew and pyenv Pythons are x86_64 builds running under Rosetta on this machine.
  arch -arm64e /usr/bin/python3 -m venv "$VENV"
fi

PIP="$VENV/bin/pip"
PYTHON="$VENV/bin/python"

if ! "$PYTHON" -c "import cv2" 2>/dev/null; then
  echo "Installing requirements (Apple Silicon TF stack first) ..."
  "$PIP" install --quiet --upgrade pip wheel
  "$PIP" install --quiet "setuptools<80"  # pkg_resources removed in setuptools>=80; mtcnn needs it
  "$PIP" install --quiet opencv-python
  # tf-keras required by retinaface (deepface dep) when tensorflow >= 2.16 (Keras 3 transition)
  "$PIP" install --quiet tf-keras
  "$PIP" install --quiet deepface
  "$PIP" install --quiet -r "$VISION_DIR/requirements.txt"
fi

# ── env ───────────────────────────────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
  echo "No .env found — copying from .env.example"
  cp "$VISION_DIR/.env.example" "$ENV_FILE"
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

export CAM_INDEX="${CAM_INDEX:-0}"

echo "Python: $("$PYTHON" -c 'import platform,sys; print(platform.machine(), sys.version)')"
echo "  postgres : ${DATABASE_URL}"
echo "  qdrant   : ${QDRANT_URL}"
echo "  composer : ${COMPOSER_URL}"
echo ""

# ── preflight import check ────────────────────────────────────────────────────
cd "$VISION_DIR"
"$PYTHON" - <<'PY'
mods = ["cv2", "tensorflow", "deepface", "main"]
for m in mods:
    print(f"  checking {m}...")
    __import__(m)
print("preflight ok")
PY

echo ""
echo "Starting mras-vision natively (CAM_INDEX=$CAM_INDEX, DEEPFACE_BACKEND=${DEEPFACE_BACKEND:-mps})"
exec "$VENV/bin/uvicorn" main:app --host 0.0.0.0 --port 8001
