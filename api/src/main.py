import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.godview.ad_runs import get_ad_runs, get_ad_run_filters, get_ad_run
from src.godview.dashboard import get_dashboard
from src.godview.events import get_events
from src.godview.systems import get_systems, get_system
from src.cameras import CameraPatch, patch_camera
from src.projector.config import ProjectorConfig
from src.projector.status import get_projector_status
from src.registry.adopt import AdoptBody, adopt_display
from src.registry.devices import (CameraCreate, DisplayCreate, DisplayPatch, create_camera,
                                  create_display, patch_display)
from src.registry.lifecycle import TransitionError
from src.registry.reads import (get_audit, get_detail, list_cameras, list_displays,
                                list_locations, list_organizations, list_screen_groups,
                                list_systems, list_unresolved)
from src.registry.writes import SemanticError

_db: asyncpg.Pool | None = None
_SIDECAR_URL = os.getenv("OVERLAY_SIDECAR_URL", "http://mras-overlays:3000")
# Thresholds read once at import/startup — changing them requires a service restart.
_PROJECTOR_CFG = ProjectorConfig.from_env()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db
    _db = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    yield
    await _db.close()


app = FastAPI(title="mras-ops", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["*"]
)


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------

@app.post("/components")
async def upload_component(name: str = Form(...), file: UploadFile = File(...)):
    try:
        source = (await file.read()).decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="component file must be UTF-8 text")
    async with httpx.AsyncClient(timeout=120) as http:
        r = await http.post(f"{_SIDECAR_URL}/components", json={"name": name, "source": source})
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"overlay sidecar error {r.status_code}: {r.text[:200]}")
    body = r.json()
    row = await _db.fetchrow(
        "INSERT INTO components (name, slug, status, error, props_schema) "
        "VALUES ($1,$2,$3,$4,$5::jsonb) "
        "ON CONFLICT (slug) DO UPDATE SET status=EXCLUDED.status, "
        "error=EXCLUDED.error, props_schema=EXCLUDED.props_schema "
        "RETURNING id",
        name,
        body["slug"],
        body["status"],
        body.get("error"),
        json.dumps(body.get("propsSchema") or {}),
    )
    # Return the DB UUID as `id` (not the sidecar's composition id) — it's what the ads FK
    # and the composer's /preview lookup use, so the frontend can preview right after upload.
    return {
        "id": str(row["id"]),
        "slug": body["slug"],
        "status": body["status"],
        "error": body.get("error"),
        "props_schema": body.get("propsSchema"),
    }


@app.get("/components")
async def list_components():
    rows = await _db.fetch(
        "SELECT id,name,slug,status,error,props_schema,created_at FROM components ORDER BY created_at DESC"
    )
    result = []
    for row in rows:
        d = dict(row)
        if isinstance(d.get("props_schema"), str):
            d["props_schema"] = json.loads(d["props_schema"])
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Ads
# ---------------------------------------------------------------------------

class AdIn(BaseModel):
    name: str
    base_video: str
    component_id: str
    default_props: dict = {}
    personalized_field: str = "text"
    is_active: bool = False


@app.post("/ads")
async def create_ad(ad: AdIn):
    row = await _db.fetchrow(
        "INSERT INTO ads (name, base_video, component_id, default_props, personalized_field, is_active) "
        "VALUES ($1,$2,$3::uuid,$4::jsonb,$5,$6) RETURNING *",
        ad.name,
        ad.base_video,
        ad.component_id,
        json.dumps(ad.default_props),
        ad.personalized_field,
        ad.is_active,
    )
    result = dict(row)
    if isinstance(result.get("default_props"), str):
        result["default_props"] = json.loads(result["default_props"])
    return result


@app.get("/ads")
async def list_ads():
    rows = await _db.fetch(
        "SELECT id,name,base_video,component_id,default_props,personalized_field,is_active,created_at FROM ads ORDER BY created_at DESC"
    )
    result = []
    for row in rows:
        d = dict(row)
        if isinstance(d.get("default_props"), str):
            d["default_props"] = json.loads(d["default_props"])
        result.append(d)
    return result


@app.patch("/ads/{ad_id}")
async def update_ad(ad_id: str, body: dict[str, Any]):
    await _db.execute(
        "UPDATE ads SET is_active=$1 WHERE id=$2::uuid",
        bool(body.get("is_active", False)),
        ad_id,
    )
    return {"status": "ok"}


@app.delete("/ads/{ad_id}")
async def delete_ad(ad_id: str):
    try:
        uuid.UUID(ad_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid id")
    result = await _db.execute("DELETE FROM ads WHERE id=$1::uuid", ad_id)
    if str(result).endswith(" 0"):
        raise HTTPException(status_code=404, detail="not found")
    return {"status": "deleted"}


@app.delete("/components/{component_id}")
async def delete_component(component_id: str):
    try:
        uuid.UUID(component_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid id")
    try:
        result = await _db.execute("DELETE FROM components WHERE id=$1::uuid", component_id)
    except asyncpg.ForeignKeyViolationError:
        # ads.component_id REFERENCES components(id) with no ON DELETE — refuse rather than
        # orphan or silently cascade live ads.
        raise HTTPException(
            status_code=409,
            detail="component is used by existing ads — delete those ads first",
        )
    if str(result).endswith(" 0"):
        raise HTTPException(status_code=404, detail="not found")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Cameras (admin registry — TODO-8 Phase C; auth deliberately absent, spec §8)
# ---------------------------------------------------------------------------

@app.patch("/cameras/{camera_id}")
async def update_camera(camera_id: str, patch: CameraPatch):
    cam_uuid = _uuid_or_400(camera_id)
    fields = patch.model_dump(exclude_unset=True)   # unset != null: null-out is a real op (ungroup)
    if not fields:
        raise HTTPException(status_code=400, detail="no updatable fields provided")
    try:
        async with _db.acquire() as conn:
            row = await patch_camera(conn, cam_uuid, fields)
    except TransitionError as exc:
        raise HTTPException(status_code=409, detail={
            "error": "invalid_transition", "from": exc.current, "allowed": exc.allowed})
    except SemanticError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if row is None:
        raise HTTPException(status_code=404, detail="camera not found")
    return row


# ---------------------------------------------------------------------------
# Registry — Fleet P1 reads (spec 2026-07-08 fleet-management, D9)
# ---------------------------------------------------------------------------

def _uuid_or_400(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid id")


def _clamp(limit: int) -> int:
    return max(1, min(limit, 100))


@app.get("/organizations")
async def registry_organizations(cursor: str | None = None, limit: int = 50):
    async with _db.acquire() as conn:
        return await list_organizations(conn, cursor=cursor, limit=_clamp(limit))


@app.get("/locations")
async def registry_locations(parent_location_id: str = "root",
                             cursor: str | None = None, limit: int = 50):
    parent = None if parent_location_id == "root" else _uuid_or_400(parent_location_id)
    async with _db.acquire() as conn:
        return await list_locations(conn, parent_id=parent, cursor=cursor, limit=_clamp(limit))


@app.get("/systems")
async def registry_systems(location_id: str, cursor: str | None = None, limit: int = 50):
    async with _db.acquire() as conn:
        return await list_systems(conn, location_id=_uuid_or_400(location_id),
                                  cursor=cursor, limit=_clamp(limit))


@app.get("/screen-groups")
async def registry_screen_groups(system_id: str, cursor: str | None = None, limit: int = 50):
    async with _db.acquire() as conn:
        return await list_screen_groups(conn, system_id=_uuid_or_400(system_id),
                                        cursor=cursor, limit=_clamp(limit))


def _device_scope(system_id: str | None, screen_group_id: str | None):
    if (system_id is None) == (screen_group_id is None):     # both or neither
        raise HTTPException(status_code=422,
                            detail="provide exactly one of system_id or screen_group_id")
    return (_uuid_or_400(system_id) if system_id else None,
            _uuid_or_400(screen_group_id) if screen_group_id else None)


@app.get("/cameras")
async def registry_cameras(system_id: str | None = None, screen_group_id: str | None = None,
                           cursor: str | None = None, limit: int = 50):
    sid, gid = _device_scope(system_id, screen_group_id)
    async with _db.acquire() as conn:
        return await list_cameras(conn, system_id=sid, screen_group_id=gid,
                                  cursor=cursor, limit=_clamp(limit))


@app.get("/displays")
async def registry_displays(system_id: str | None = None, screen_group_id: str | None = None,
                            cursor: str | None = None, limit: int = 50):
    sid, gid = _device_scope(system_id, screen_group_id)
    async with _db.acquire() as conn:
        return await list_displays(conn, system_id=sid, screen_group_id=gid,
                                   cursor=cursor, limit=_clamp(limit))


_DETAIL_ROUTES = (("organizations", "organization"), ("locations", "location"),
                  ("systems", "system"), ("screen-groups", "screen_group"),
                  ("cameras", "camera"), ("displays", "display"))

def _register_detail_routes():
    for path, object_type in _DETAIL_ROUTES:
        def make(object_type=object_type):
            async def detail(object_id: str):
                async with _db.acquire() as conn:
                    result = await get_detail(conn, object_type, _uuid_or_400(object_id))
                if result is None:
                    raise HTTPException(status_code=404, detail=f"{object_type} not found")
                return result
            return detail
        app.get(f"/{path}/{{object_id}}")(make())

_register_detail_routes()


@app.get("/registry/audit")
async def registry_audit(object_id: str, cursor: str | None = None, limit: int = 20):
    async with _db.acquire() as conn:
        return await get_audit(conn, object_id, cursor=cursor, limit=_clamp(limit))


@app.get("/unresolved-devices")
async def registry_unresolved(cursor: str | None = None, limit: int = 50):
    async with _db.acquire() as conn:
        return await list_unresolved(conn, cursor=cursor, limit=_clamp(limit))


# ---------------------------------------------------------------------------
# Registry — Fleet P2 device writes (spec 2026-07-08 fleet-management, D3/D6/D7/D8/D10)
# ---------------------------------------------------------------------------

@app.patch("/displays/{display_id}")
async def update_display(display_id: str, patch: DisplayPatch):
    disp_uuid = _uuid_or_400(display_id)
    fields = patch.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="no updatable fields provided")
    try:
        async with _db.acquire() as conn:
            row = await patch_display(conn, disp_uuid, fields)
    except TransitionError as exc:
        raise HTTPException(status_code=409, detail={
            "error": "invalid_transition", "from": exc.current, "allowed": exc.allowed})
    except SemanticError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if row is None:
        raise HTTPException(status_code=404, detail="display not found")
    return row


@app.post("/cameras", status_code=201)
async def register_camera(body: CameraCreate):
    try:
        async with _db.acquire() as conn:
            return await create_camera(conn, body)
    except SemanticError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="screen_id already registered")


@app.post("/displays", status_code=201)
async def register_display(body: DisplayCreate):
    try:
        async with _db.acquire() as conn:
            return await create_display(conn, body)
    except SemanticError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="screen_id already registered")


@app.post("/displays/adopt", status_code=201)
async def adopt_unresolved_display(body: AdoptBody):
    try:
        async with _db.acquire() as conn:
            row = await adopt_display(conn, body)
    except SemanticError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="screen_id already registered")
    if row is None:
        raise HTTPException(status_code=404, detail="unresolved device not found")
    return row


# ---------------------------------------------------------------------------
# Events stream + health (unchanged)
# ---------------------------------------------------------------------------

@app.get("/events/stream")
async def events_stream():
    async def generate():
        rows = await _db.fetch(
            "SELECT trigger_id, ts, service, event_type, status, payload "
            "FROM events ORDER BY ts DESC LIMIT 20"
        )
        for row in reversed(rows):
            yield f"data: {json.dumps(dict(row), default=str)}\n\n"

        last_ts = rows[0]["ts"] if rows else datetime.now(timezone.utc)
        while True:
            await asyncio.sleep(1)
            new_rows = await _db.fetch(
                "SELECT trigger_id, ts, service, event_type, status, payload "
                "FROM events WHERE ts > $1 ORDER BY ts ASC",
                last_ts,
            )
            for row in new_rows:
                yield f"data: {json.dumps(dict(row), default=str)}\n\n"
                last_ts = row["ts"]

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/god-view/dashboard")
async def god_view_dashboard():
    async with _db.acquire() as conn:
        return await get_dashboard(conn)


@app.get("/god-view/ad-runs")
async def god_view_ad_runs(status: str | None = None, system_id: str | None = None,
                           campaign_id: str | None = None, since: str | None = None,
                           cursor: str | None = None, limit: int = 50):
    limit = max(1, min(limit, 100))
    since_ts = datetime.fromisoformat(since) if since else None
    async with _db.acquire() as conn:
        return await get_ad_runs(conn, status=status, system_id=system_id,
                                 campaign_id=campaign_id, since=since_ts,
                                 cursor=cursor, limit=limit)


@app.get("/god-view/ad-runs/filters")
async def god_view_ad_run_filters():
    async with _db.acquire() as conn:
        return await get_ad_run_filters(conn)


@app.get("/god-view/ad-runs/{ad_run_id}")
async def god_view_ad_run(ad_run_id: str):
    async with _db.acquire() as conn:
        result = await get_ad_run(conn, ad_run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="ad_run not found")
    return result


@app.get("/god-view/systems")
async def god_view_systems(search: str | None = None, cursor: str | None = None, limit: int = 50):
    limit = max(1, min(limit, 100))
    async with _db.acquire() as conn:
        return await get_systems(conn, search=search, cursor=cursor, limit=limit)


@app.get("/god-view/systems/{system_id}")
async def god_view_system(system_id: str):
    async with _db.acquire() as conn:
        result = await get_system(conn, system_id)
    if result is None:
        raise HTTPException(status_code=404, detail="system not found")
    return result


@app.get("/god-view/events")
async def god_view_events(cursor: str | None = None, limit: int = 50):
    limit = max(1, min(limit, 100))
    async with _db.acquire() as conn:
        return await get_events(conn, cursor=cursor, limit=limit)


@app.get("/projector/status")
async def projector_status():
    """God View projector health: cursor, backlog, lag, and ok/warn/crit level."""
    async with _db.acquire() as conn:
        try:
            return await get_projector_status(conn, _PROJECTOR_CFG)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))


@app.get("/health")
def health():
    return {"status": "ok"}
