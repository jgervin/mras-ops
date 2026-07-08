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

from src.godview.dashboard import get_dashboard
from src.projector.config import ProjectorConfig
from src.projector.status import get_projector_status

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
