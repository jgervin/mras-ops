from unittest.mock import AsyncMock
from scripts.purge_auto_embeddings import purge


async def test_purge_deletes_auto_ids_from_both_stores():
    db = AsyncMock()
    db.fetch = AsyncMock(return_value=[{"id": "a1"}, {"id": "a2"}])
    qdrant = AsyncMock()
    n = await purge(db, qdrant, "jason-uuid")
    assert n == 2
    # selected only source='auto'
    assert "source = 'auto'" in db.fetch.call_args.args[0]
    qdrant.delete.assert_awaited_once()
