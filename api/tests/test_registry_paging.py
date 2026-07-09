"""Name-keyed keyset cursor for registry lists (pure, no DB).

Distinct from godview/systems' private decoder: splits on the LAST '|' (names
may contain pipes; uuids never do) and encodes NULL names as '' to match the
lists' COALESCE(name,'') sort key."""
import uuid

from src.registry.paging import decode_name_cursor, encode_name_cursor, page


def test_roundtrip():
    rid = uuid.uuid4()
    assert decode_name_cursor(encode_name_cursor("Bay 2", rid)) == ("Bay 2", rid)


def test_empty_cursor_decodes_to_nones():
    assert decode_name_cursor(None) == (None, None)
    assert decode_name_cursor("") == (None, None)


def test_name_containing_pipes_survives():
    rid = uuid.uuid4()
    assert decode_name_cursor(encode_name_cursor("North|Wall|A", rid)) == ("North|Wall|A", rid)


def test_null_name_encodes_as_empty_string():
    rid = uuid.uuid4()
    assert decode_name_cursor(encode_name_cursor(None, rid)) == ("", rid)


def test_page_slices_limit_and_encodes_next_from_last_kept_row():
    rid = [uuid.uuid4() for _ in range(3)]
    rows = [{"name": "A", "id": rid[0]}, {"name": "B", "id": rid[1]}, {"name": "C", "id": rid[2]}]
    items, next_cursor = page(rows, 2)          # rows fetched with LIMIT limit+1
    assert [i["name"] for i in items] == ["A", "B"]
    assert next_cursor == f"B|{rid[1]}"


def test_page_final_page_has_no_cursor():
    rows = [{"name": "A", "id": uuid.uuid4()}]
    items, next_cursor = page(rows, 2)
    assert len(items) == 1 and next_cursor is None
