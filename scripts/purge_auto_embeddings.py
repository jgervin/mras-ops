"""Reversibility: remove auto-augmented gallery embeddings for an identity.
Usage: python -m scripts.purge_auto_embeddings <identity_uuid> [since_iso8601]"""
from __future__ import annotations

import asyncio
import os
import sys


async def purge(db, qdrant, identity_uuid: str, since: str | None = None) -> int:
    q = "SELECT id::text FROM identity_embeddings WHERE identity_uuid = $1 AND source = 'auto'"
    args = [identity_uuid]
    if since:
        q += " AND created_at >= $2"
        args.append(since)
    rows = await db.fetch(q, *args)
    ids = [r["id"] for r in rows]
    if not ids:
        return 0
    await db.execute("DELETE FROM identity_embeddings WHERE id = ANY($1::uuid[])", ids)
    from qdrant_client.http.models import PointIdsList
    await qdrant.delete(
        collection_name=os.getenv("QDRANT_COLLECTION", "mras_embeddings"),
        points_selector=PointIdsList(points=ids))
    return len(ids)


async def _main():
    import asyncpg
    from qdrant_client import AsyncQdrantClient
    uuid = sys.argv[1]
    since = sys.argv[2] if len(sys.argv) > 2 else None
    db = await asyncpg.create_pool(os.getenv("DATABASE_URL", "postgresql://mras:mras@localhost:5432/mras"))
    qdrant = AsyncQdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"))
    n = await purge(db, qdrant, uuid, since)
    print(f"purged {n} auto embeddings for {uuid}")
    await db.close()


if __name__ == "__main__":
    asyncio.run(_main())
