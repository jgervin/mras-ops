"""Adopt an unresolved (seen-but-unregistered) display (spec D11 — UniFi/Meraki
pattern). One transaction: create the display with the unresolved row's
screen_id pre-filled (staged offline, D7; identity row minted, D8), DELETE the
unresolved bookkeeping row, journal registry_admin action=adopt. DROPPABLE:
nothing else imports this module.

Implementation choice (per the plan's IMPLEMENTER NOTE): rather than writing
the create's registry_admin event and then rewriting its payload with a
second UPDATE, create_display accepts action/extra passthrough parameters so
the adopt event is written ONCE, directly with action="adopt" and the
adopted_from block — no __import__("json"), no double journal write.
"""
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict

from src.registry.devices import DisplayCreate, create_display
from src.registry.writes import SemanticError


class AdoptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")   # D11's exact body: no other fields
    unresolved_id: uuid.UUID
    system_id: uuid.UUID
    name: Optional[str] = None
    screen_group_id: Optional[uuid.UUID] = None


async def adopt_display(conn, body: AdoptBody):
    """None = unknown unresolved_id. Raises SemanticError (422); a duplicate
    screen_id (already-registered race) raises UniqueViolationError (route: 409)."""
    async with conn.transaction():
        unres = await conn.fetchrow(
            "SELECT id, screen_id, kind, seen_count FROM unresolved_devices "
            "WHERE id = $1 FOR UPDATE", body.unresolved_id)
        if unres is None:
            return None
        if unres["kind"] != "display":
            raise SemanticError("unresolved device is not a display")
        row = await create_display(
            conn, DisplayCreate(system_id=body.system_id, screen_id=unres["screen_id"],
                                name=body.name, screen_group_id=body.screen_group_id),
            action="adopt",
            extra={"adopted_from": {"unresolved_id": str(unres["id"]),
                                    "screen_id": unres["screen_id"],
                                    "seen_count": unres["seen_count"]}})
        await conn.execute("DELETE FROM unresolved_devices WHERE id = $1", body.unresolved_id)
    return row
