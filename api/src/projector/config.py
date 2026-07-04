"""T2 — Projector configuration.

Matches the repo's ad-hoc ``os.getenv(name, default)`` convention (no
pydantic-settings anywhere in mras-ops) but keeps every knob in one frozen
dataclass so the worker, resolver, and status API read the same source.
"""
import os
from dataclasses import dataclass

# Bumped by hand when the projection logic changes; stamped onto projector_state
# and skip-audit rows so a folded row can be traced to the code that wrote it.
PROJECTOR_VER = "godview-projector-0.1.0"

_DEFAULT_DATABASE_URL = "postgresql://mras:mras@localhost:5432/mras"


@dataclass(frozen=True)
class ProjectorConfig:
    database_url: str
    poll_ms: int
    batch_size: int
    lag_warn_s: int
    lag_crit_s: int
    settle_ms: int
    target_lookback_s: int
    advisory_lock_key: int
    projector_ver: str

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env

        def _int(name, default):
            return int(env.get(name, default))

        return cls(
            database_url=env.get("DATABASE_URL", _DEFAULT_DATABASE_URL),
            poll_ms=_int("PROJECTOR_POLL_MS", 1000),
            batch_size=_int("PROJECTOR_BATCH_SIZE", 500),
            lag_warn_s=_int("PROJECTOR_LAG_WARN_S", 10),
            lag_crit_s=_int("PROJECTOR_LAG_CRIT_S", 60),
            settle_ms=_int("PROJECTOR_SETTLE_MS", 2000),
            target_lookback_s=_int("PROJECTOR_TARGET_LOOKBACK_S", 900),
            advisory_lock_key=_int("PROJECTOR_ADVISORY_LOCK_KEY", 20260701),
            projector_ver=env.get("PROJECTOR_VER", PROJECTOR_VER),
        )
