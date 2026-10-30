from pathlib import Path

import pytest

from core.cogs.system import format_duration, latency_ms
from core.db import affected, clean_dsn
from core.migrate import discover


def test_clean_dsn_strips_libpq_params_and_keeps_others():
    dsn = "postgresql://u:p@ep-x-pooler.us-west-2.aws.neon.tech/db?sslmode=require&channel_binding=require&application_name=x"
    assert clean_dsn(dsn) == "postgresql://u:p@ep-x-pooler.us-west-2.aws.neon.tech/db?application_name=x"


def test_clean_dsn_normalises_scheme():
    assert clean_dsn("postgres://u@h/db").startswith("postgresql://")
    assert clean_dsn("postgresql+asyncpg://u@h/db").startswith("postgresql://")


def test_affected():
    assert affected("DELETE 3") == 3
    assert affected("") == 0


def test_project_migrations_are_valid_and_ordered():
    migrations = discover()
    assert migrations and migrations[0].version == "0001_core"
    assert [m.version for m in migrations] == sorted(m.version for m in migrations)


def test_discover_rejects_bad_names(tmp_path: Path):
    (tmp_path / "1_bad.sql").write_text("")
    with pytest.raises(ValueError):
        discover(tmp_path)


def test_discover_rejects_duplicate_numbers(tmp_path: Path):
    (tmp_path / "0001_a.sql").write_text("")
    (tmp_path / "0001_b.sql").write_text("")
    with pytest.raises(ValueError):
        discover(tmp_path)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(45, "45s"), (60, "1m"), (3_660, "1h 1m"), (90_061, "1d 1h")],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


def test_latency_ms():
    assert latency_ms(0.1234) == 123
    assert latency_ms(float("inf")) is None
    assert latency_ms(None) is None
