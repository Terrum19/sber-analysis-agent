import time
from pathlib import Path

import pytest

from app.core.data.connectors.csv import column_types, load_csv
from app.core.data.duckdb_manager import DuckDBManager

ROWS = 1_000_000
LIMIT_SECONDS = 5.0


@pytest.fixture
def csv_1m_path(tmp_path: Path):
    path = tmp_path / "bench_1m.csv"
    manager = DuckDBManager(":memory:")
    gen_sql = f"""
        COPY (
            SELECT
                range AS id,
                CASE WHEN range % 1000 = 0 THEN 'NULL'
                     WHEN range % 2000 = 0 THEN 'None'
                     ELSE CAST(range % 10000 AS VARCHAR) END AS val_int,
                CASE WHEN range % 500 = 0 THEN '1 234,56'
                     ELSE replace(CAST(round(random() * 1000, 2) AS VARCHAR), '.', ',') END AS val_double,
                'category_' || (range % 50) AS cat,
                CASE WHEN range % 3 = 0 THEN '007' ELSE '08' END AS code,
                CAST(DATE '2024-01-01' + CAST(range % 365 AS INTEGER) AS VARCHAR) AS dt,
                CAST(TIMESTAMP '2024-01-01 00:00:00' + range * INTERVAL 1 SECOND AS VARCHAR) AS ts,
                'note_' || range AS notes,
                CASE WHEN range % 4 = 0 THEN 'NA' WHEN range % 4 = 1 THEN 'US' ELSE 'DE' END AS country
            FROM range({ROWS})
        ) TO '{path}' (HEADER, DELIMITER ';')
    """
    manager.execute(gen_sql)
    manager.close()
    yield path
    if path.exists():
        path.unlink()


def test_csv_load_1m_within_limit(csv_1m_path: Path) -> None:
    db_base = DuckDBManager(":memory:")
    t_start = time.perf_counter()
    db_base.execute(f"CREATE TABLE base AS SELECT * FROM read_csv('{csv_1m_path}', delim=';')")
    t_base = time.perf_counter() - t_start
    db_base.close()

    db = DuckDBManager(":memory:")
    started = time.perf_counter()
    report = load_csv(db, "bench_table", csv_1m_path)
    elapsed = time.perf_counter() - started

    assert report.rows == ROWS
    assert elapsed <= LIMIT_SECONDS
    # Проверка, что накладные расходы на сканирование и ALTER TABLE не превышают разумный предел
    assert elapsed < t_base + 3.0

    types = dict(column_types(db, "bench_table"))
    assert types["val_int"] == "BIGINT"
    assert types["val_double"] == "DOUBLE"
    assert types["code"] == "VARCHAR"
    assert types["country"] == "VARCHAR"
    assert "NULL" in report.null_tokens
    db.close()
