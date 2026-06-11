#!/usr/bin/env bash
# Compose a random base video x a random READY custom Remotion component,
# personalized with NAME, via the composer's /preview. Prints the mp4 URL
# (clip also lands in /Users/jn/code/mras-ops/output/).
#
# Usage:
#   ./compose-random.sh            # name defaults to Jason
#   ./compose-random.sh "Maria"
#
# Requires the compose stack running (composer :8002, ops-api :8080).
set -euo pipefail

export COMPOSE_NAME="${1:-Jason}"
export OPS_API_URL="${OPS_API_URL:-http://localhost:8080}"
export COMPOSER_URL="${COMPOSER_URL:-http://localhost:8002}"

PY="${VISION_PY:-/Users/jn/code/mras-vision/.venv/bin/python}"

exec "$PY" << 'PY'
import json
import os
import random
import sys

import httpx

name = os.environ["COMPOSE_NAME"]
ops, composer = os.environ["OPS_API_URL"], os.environ["COMPOSER_URL"]

components = [
    c for c in httpx.get(f"{ops}/components", timeout=30).json()
    if c.get("status") == "ready"
]
if not components:
    sys.exit("no ready components — upload one via the Authoring UI first")

bases = httpx.get(f"{composer}/playlist", timeout=30).json()["videos"]
if not bases:
    sys.exit("no base videos in the pool")

component = random.choice(components)
base_url = random.choice(bases)
base_video = "/assets/" + base_url.rsplit("/", 1)[-1]  # container path

# Personalize the first string-typed prop in the component's schema (the
# convention every example component follows for its text/name field).
schema_props = (component.get("props_schema") or {}).get("properties", {})
text_prop = next(
    (k for k, v in schema_props.items() if v.get("type") == "string"), None
)
props = {text_prop: name} if text_prop else {}

print(f"composing: {component['slug']} x {base_video} for {name!r}", file=sys.stderr)
r = httpx.post(
    f"{composer}/preview",
    json={"component_id": component["id"], "props": props, "base_video": base_video},
    timeout=180,
)
body = r.json()
if r.status_code != 200 or "error" in body:
    sys.exit(f"compose failed: {r.status_code} {json.dumps(body)}")
print(body["url"])
PY
