import importlib.util
import time
from pathlib import Path

import pytest

from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.profiling import profile_table

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "run_profile_benchmark.py"
ROWS = 1_000_000
COLUMNS = 50
LIMIT_SECONDS = 10.0


def load_script():
    spec = importlib.util.spec_from_file_location("run_profile_benchmark", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def db():
    manager = DuckDBManager(":memory:")
    manager.execute("CREATE TABLE wide AS " + load_script().synthetic_sql(ROWS, COLUMNS))
    yield manager
    manager.close()


def test_wide_table_profile_within_limit(db) -> None:
    started = time.perf_counter()
    profile = profile_table(db, "wide")
    elapsed = time.perf_counter() - started
    assert profile.row_count == ROWS
    assert profile.column_count == COLUMNS
    assert elapsed <= LIMIT_SECONDS
