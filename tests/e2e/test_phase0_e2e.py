"""
Phase 0 end-to-end test. Requires docker compose up.

    cd mras-ops
    pip install -r tests/e2e/requirements.txt
    pytest tests/e2e/test_phase0_e2e.py -v -s
"""
import asyncio
import csv
import io
import time
import uuid
from pathlib import Path

import httpx
import pytest

VISION = "http://localhost:8001"
COMPOSER = "http://localhost:8002"
TIMEOUT = 30.0
ASSEMBLE_BUDGET = 15.0
FIXTURE = Path(__file__).parent / "fixtures" / "test_face.jpg"
PERSON_NAME = "E2EPerson"


async def _wait_healthy(http: httpx.AsyncClient) -> None:
    for url in [f"{VISION}/health", f"{COMPOSER}/health"]:
        for _ in range(20):
            try:
                r = await http.get(url, timeout=2.0)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            await asyncio.sleep(1)
        else:
            pytest.fail(f"Service not healthy: {url}")


async def test_enroll_and_standard_trigger():
    """Enroll a face; fire a new-visitor trigger; verify standard response."""
    assert FIXTURE.exists(), (
        f"Missing test fixture: {FIXTURE}\n"
        "Add a JPEG with a single clear face to tests/e2e/fixtures/test_face.jpg"
    )
    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        await _wait_healthy(http)

        csv_buf = io.StringIO()
        csv.writer(csv_buf).writerows([["name", "photo"], [PERSON_NAME, "test_face.jpg"]])
        resp = await http.post(
            f"{VISION}/enroll",
            files={
                "csv_file": ("enroll.csv", csv_buf.getvalue().encode(), "text/csv"),
                "photos": ("test_face.jpg", FIXTURE.read_bytes(), "image/jpeg"),
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("enrolled") == 1 or body.get("updated") == 1, body

        resp = await http.post(
            f"{COMPOSER}/trigger",
            json={
                "trigger_id": str(uuid.uuid4()),
                "uuid": None,
                "confidence": 0.0,
                "is_new_visitor": True,
                "scene_context": {},
                "screen_id": "screen_e2e",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "standard"


async def test_personalized_trigger_assembles_video_within_budget():
    """Known-UUID trigger → assembled video accessible within ASSEMBLE_BUDGET seconds."""
    assert FIXTURE.exists(), f"Missing fixture: {FIXTURE}"

    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        await _wait_healthy(http)

        resp = await http.get(f"{VISION}/identity", params={"name": PERSON_NAME})
        if resp.status_code == 404:
            pytest.skip(f"{PERSON_NAME} not enrolled — run test_enroll_and_standard_trigger first")
        person_uuid = resp.json()["uuid"]

        trigger_id = str(uuid.uuid4())
        t0 = time.monotonic()

        resp = await http.post(
            f"{COMPOSER}/trigger",
            json={
                "trigger_id": trigger_id,
                "uuid": person_uuid,
                "confidence": 0.90,
                "is_new_visitor": False,
                "scene_context": {},
                "screen_id": "screen_e2e",
            },
        )
        assert resp.status_code == 200
        result = resp.json()
        elapsed = time.monotonic() - t0

        print(f"\n  status={result['status']}  elapsed={elapsed:.2f}s")

        if result["status"] == "tts_failed":
            pytest.skip("TTS providers unavailable — check ELEVENLABS_API_KEY in .env")

        assert result["status"] == "ok", result

        video_resp = await http.get(f"{COMPOSER}/media/{trigger_id}.mp4", timeout=5.0)
        assert video_resp.status_code == 200, f"Video not found at /media/{trigger_id}.mp4"
        assert elapsed < ASSEMBLE_BUDGET, (
            f"Assembly took {elapsed:.2f}s, budget is {ASSEMBLE_BUDGET}s"
        )
        print(f"  video={len(video_resp.content)} bytes  latency={elapsed:.2f}s ✓")
