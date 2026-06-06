import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

_db: asyncpg.Pool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db
    _db = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    yield
    await _db.close()


app = FastAPI(title="mras-ops", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"]
)


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


@app.get("/health")
def health():
    return {"status": "ok"}
