"""T2 — ProjectorConfig: PROJECTOR_* env knobs with sane defaults."""
from src.projector.config import ProjectorConfig, PROJECTOR_VER


def test_defaults_when_env_unset():
    cfg = ProjectorConfig.from_env(env={})
    assert cfg.poll_ms == 1000
    assert cfg.batch_size == 500
    assert cfg.lag_warn_s == 10
    assert cfg.lag_crit_s == 60
    assert cfg.settle_ms == 2000
    assert cfg.advisory_lock_key == 20260701
    assert cfg.projector_ver == PROJECTOR_VER
    assert cfg.database_url == "postgresql://mras:mras@localhost:5432/mras"


def test_env_overrides_win():
    cfg = ProjectorConfig.from_env(env={
        "PROJECTOR_POLL_MS": "250",
        "PROJECTOR_BATCH_SIZE": "50",
        "PROJECTOR_LAG_WARN_S": "5",
        "PROJECTOR_LAG_CRIT_S": "30",
        "PROJECTOR_SETTLE_MS": "1500",
        "PROJECTOR_ADVISORY_LOCK_KEY": "99",
        "DATABASE_URL": "postgresql://x/y",
    })
    assert cfg.poll_ms == 250
    assert cfg.batch_size == 50
    assert cfg.lag_warn_s == 5
    assert cfg.lag_crit_s == 30
    assert cfg.settle_ms == 1500
    assert cfg.advisory_lock_key == 99
    assert cfg.database_url == "postgresql://x/y"


def test_settle_ms_zero_is_honored():
    # Tests disable the settle window with SETTLE_MS=0; 0 must survive, not fall back to the default.
    cfg = ProjectorConfig.from_env(env={"PROJECTOR_SETTLE_MS": "0"})
    assert cfg.settle_ms == 0


def test_config_is_frozen():
    cfg = ProjectorConfig.from_env(env={})
    import dataclasses
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        cfg.poll_ms = 5
