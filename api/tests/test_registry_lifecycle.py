"""D3 transition matrix — pure, no DB. The matrix in the plan's Global
Constraints is normative; these tests enumerate it EXACTLY."""
import pytest

from src.registry.lifecycle import TransitionError, allowed_transitions, check_transition

DEVICE_MATRIX = {
    "active":   {"degraded", "offline", "retired"},
    "degraded": {"active", "offline", "retired"},
    "offline":  {"active", "degraded", "retired"},
    "retired":  set(),
}

LIFECYCLE_MATRIX = {
    "planned":  {"active", "retired"},
    "active":   {"inactive", "degraded", "offline", "retired"},
    "inactive": {"active", "degraded", "offline", "retired"},
    "degraded": {"active", "inactive", "offline", "retired"},
    "offline":  {"active", "inactive", "degraded", "retired"},
    "retired":  set(),
}


def test_device_matrix_exact():
    for current, want in DEVICE_MATRIX.items():
        assert set(allowed_transitions(current, "device")) == want, current


def test_lifecycle_matrix_exact():
    for current, want in LIFECYCLE_MATRIX.items():
        assert set(allowed_transitions(current, "lifecycle")) == want, current


def test_same_status_is_a_noop_even_for_retired():
    check_transition("retired", "retired")      # no raise (idempotent PATCH)
    check_transition("active", "active")


def test_valid_transitions_pass():
    check_transition("offline", "active")
    check_transition("active", "retired")
    check_transition("planned", "active", "lifecycle")


def test_leaving_retired_raises_with_empty_allowed_set():
    with pytest.raises(TransitionError) as exc:
        check_transition("retired", "active")
    assert exc.value.current == "retired"
    assert exc.value.allowed == []              # the 409 renders "allowed: []" (terminal)


def test_planned_cannot_go_sideways():
    with pytest.raises(TransitionError) as exc:
        check_transition("planned", "offline", "lifecycle")
    assert set(exc.value.allowed) == {"active", "retired"}
