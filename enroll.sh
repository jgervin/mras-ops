#!/usr/bin/env bash
# Enroll a person into MRAS face recognition (Qdrant embeddings + Postgres
# identity) via the native vision service's existing POST /enroll — which
# already handles multi-photo embedding averaging and duplicate-name merging.
#
# Usage:
#   ./enroll.sh "Maria Lopez" maria.jpg [maria2.jpg ...]
#   VISION_URL=http://otherhost:8001 ./enroll.sh "Name" photo.jpg
#
# Requires the vision service running (native: ./run-vision-native.sh).
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 \"Name\" photo.jpg [more photos...]" >&2
  exit 1
fi

NAME="$1"; shift
export VISION_URL="${VISION_URL:-http://localhost:8001}"
export ENROLL_NAME="$NAME"

# The vision venv is the one Python on this machine guaranteed to have httpx.
PY="${VISION_PY:-/Users/jn/code/mras-vision/.venv/bin/python}"

exec "$PY" - "$@" << 'PY'
import json
import os
import pathlib
import sys

import httpx

name = os.environ["ENROLL_NAME"]
photos = sys.argv[1:]
csv_rows = "name,photo\n" + "".join(
    f"{name},{pathlib.Path(p).name}\n" for p in photos
)
files = [("csv_file", ("enroll.csv", csv_rows.encode(), "text/csv"))]
for p in photos:
    path = pathlib.Path(p)
    if not path.exists():
        sys.exit(f"photo not found: {p}")
    files.append(("photos", (path.name, path.read_bytes(), "image/jpeg")))

url = os.environ["VISION_URL"]
try:
    r = httpx.post(url + "/enroll", files=files, timeout=120)
except httpx.ConnectError:
    sys.exit(
        f"ERROR: vision service is not running at {url}.\n"
        "Start it first:  cd /Users/jn/code/mras-ops && ./run-vision-native.sh\n"
        "(or the full stack: ./start-mras.sh)"
    )
print(r.status_code, json.dumps(r.json(), indent=2))
sys.exit(0 if r.status_code == 200 else 1)
PY
