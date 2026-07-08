"""Per-camera detection readings aggregated from subject_observations (last 60s)."""
import uuid

import pytest

from src.godview.readings import readings_for_system

pytestmark = pytest.mark.usefixtures("godview_isolate")


async def _seed_system_with_camera(pool):
    org = uuid.uuid4()
    loc = uuid.uuid4()
    sysid = uuid.uuid4()
    cam = uuid.uuid4()
    await pool.execute(
        "INSERT INTO organizations (id,name,organization_type) VALUES ($1,'Org','advertiser')", org)
    await pool.execute(
        "INSERT INTO locations (id,name,location_type) VALUES ($1,'Loc','store')", loc)
    await pool.execute(
        "INSERT INTO systems (id,organization_id,location_id,name) VALUES ($1,$2,$3,'Sys')",
        sysid, org, loc)
    await pool.execute(
        "INSERT INTO cameras (id,system_id,name,screen_id) VALUES ($1,$2,'Cam','scr_t1')",
        cam, sysid)
    return sysid, cam


async def test_counts_recent_observations_and_averages_quality(projector_pool):
    sysid, cam = await _seed_system_with_camera(projector_pool)
    # two recent observations, quality 0.8 and 0.6 -> count 2, avg 0.7
    for q in (0.8, 0.6):
        await projector_pool.execute(
            "INSERT INTO subject_observations (camera_id,system_id,observed_at,detection_type,face_quality_score) "
            "VALUES ($1,$2, now(), 'face', $3)", cam, sysid, q)
    # one stale observation (2 minutes ago) must be excluded
    await projector_pool.execute(
        "INSERT INTO subject_observations (camera_id,system_id,observed_at,detection_type,face_quality_score) "
        "VALUES ($1,$2, now() - interval '120 seconds', 'face', 0.9)", cam, sysid)

    readings = await readings_for_system(projector_pool, sysid)
    r = readings[str(cam)]
    assert r["face_count"] == 2
    assert abs(r["confidence"] - 0.7) < 1e-6


async def test_camera_with_no_observations_reads_zero(projector_pool):
    sysid, cam = await _seed_system_with_camera(projector_pool)
    readings = await readings_for_system(projector_pool, sysid)
    assert readings[str(cam)] == {"face_count": 0, "confidence": 0.0}
