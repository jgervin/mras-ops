"""Lifecycle-transition validation (spec D3). Pure functions, no DB.

The matrix (normative table in the plan's Global Constraints):
  device_status  — active/degraded/offline freely interchange; anything may
      retire; retired is terminal (allowed set is empty).
  lifecycle_status — planned may only activate or retire; active/inactive/
      degraded/offline freely interchange; anything may retire; retired terminal.
Same-status writes are no-ops (idempotent PATCH — preserves the shipped
PATCH /cameras contract). Used by camera+display PATCH in P2; the lifecycle
kind is defined now so P3/P4 container writes reuse this module unchanged.
"""

_DEVICE_LIVE = ("active", "degraded", "offline")
_LIFECYCLE_LIVE = ("active", "inactive", "degraded", "offline")


class TransitionError(Exception):
    """Invalid lifecycle transition -> route maps to 409 (spec D3 / Plan-B E3)."""

    def __init__(self, current: str, allowed):
        self.current = current
        self.allowed = list(allowed)
        super().__init__(f"invalid transition from {current!r}")


def allowed_transitions(current: str, kind: str = "device") -> tuple:
    live = _DEVICE_LIVE if kind == "device" else _LIFECYCLE_LIVE
    if current == "retired":
        return ()
    if current == "planned":                    # lifecycle_status only
        return ("active", "retired")
    return tuple(s for s in live if s != current) + ("retired",)


def check_transition(current: str, new: str, kind: str = "device") -> None:
    if new == current:
        return                                  # idempotent no-op
    if new not in allowed_transitions(current, kind):
        raise TransitionError(current, allowed_transitions(current, kind))
