"""Очистка CSV (D-08, #103): во сколько раз загрузка медленнее голого read_csv.

База — read_csv в таблицу без очистки: колонки с литералами пропусков остаются
VARCHAR. Литералы стоят в первых строках, поэтому сниффер видит их и обе
загрузки проходят с первой стратегии.

Очистка — ещё два прохода по таблице (виды значений и пересборка с типами),
поэтому «+30 % к read_csv» из #103 недостижимо; команда приняла предел в разах.
На 1 млн строк × 9 колонок и 150 тыс. × 105 колонок сейчас около 4,5×.
"""

import logging
import time
from pathlib import Path

import pytest

from app.core.data.connectors.csv import column_types, load_csv
from app.core.data.duckdb_manager import DuckDBManager

MAX_SLOWDOWN = 6.0
RUNS = 3


def generate(path: Path, select: str, rows: int) -> Path:
    manager = DuckDBManager(":memory:")
    manager.execute(
        f"COPY (SELECT {select} FROM range({rows}) ORDER BY range) TO '{path}' (HEADER, DELIMITER ';')"
    )
    manager.close()
    return path


def best_time(action) -> float:
    timings = []
    for _ in range(RUNS):
        started = time.perf_counter()
        action()
        timings.append(time.perf_counter() - started)
    return min(timings)


def read_plain(path: Path) -> None:
    db = DuckDBManager(":memory:")
    db.execute(f"CREATE TABLE base AS SELECT * FROM read_csv('{path}', delim=';')")
    db.close()


def load_clean(path: Path) -> None:
    db = DuckDBManager(":memory:")
    load_csv(db, "bench", path)
    db.close()


@pytest.fixture(autouse=True)
def quiet_loader():
    logging.disable(logging.INFO)
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture
def mixed_1m(tmp_path: Path) -> Path:
    select = """
        range AS id,
        CASE WHEN range % 1000 = 0 THEN 'NULL'
             WHEN range % 1000 = 1 THEN 'None'
             ELSE CAST(range % 10000 AS VARCHAR) END AS val_int,
        CASE WHEN range % 500 = 0 THEN '1 234,56'
             ELSE replace(CAST(round(random() * 1000, 2) AS VARCHAR), '.', ',') END AS val_double,
        'category_' || (range % 50) AS cat,
        CASE WHEN range % 3 = 0 THEN '007' ELSE '08' END AS code,
        CASE WHEN range % 1000 = 2 THEN 'NULL'
             ELSE CAST(DATE '2024-01-01' + CAST(range % 365 AS INTEGER) AS VARCHAR) END AS dt,
        CAST(TIMESTAMP '2024-01-01 00:00:00' + range * INTERVAL 1 SECOND AS VARCHAR) AS ts,
        'note_' || range AS notes,
        CASE WHEN range % 4 = 0 THEN 'NA' WHEN range % 4 = 1 THEN 'US' ELSE 'DE' END AS country
    """
    return generate(tmp_path / "mixed_1m.csv", select, 1_000_000)


@pytest.fixture
def survey_105(tmp_path: Path) -> Path:
    """Форма openPsych (#103): 104 числовые колонки с литералами NULL и страна."""
    answers = ", ".join(
        f"CASE WHEN range % 1000 = 0 THEN 'NULL' ELSE CAST((range * {index + 7}) % 5 + 1 AS VARCHAR) END "
        f"AS q{index}"
        for index in range(104)
    )
    select = f"{answers}, CASE WHEN range % 4 = 0 THEN 'NA' ELSE 'US' END AS country"
    return generate(tmp_path / "survey_105.csv", select, 150_000)


def test_mixed_columns_1m_rows(mixed_1m: Path) -> None:
    base = best_time(lambda: read_plain(mixed_1m))
    elapsed = best_time(lambda: load_clean(mixed_1m))

    db = DuckDBManager(":memory:")
    report = load_csv(db, "bench", mixed_1m)
    types = dict(column_types(db, "bench"))
    db.close()
    assert report.rows == 1_000_000
    assert types["val_int"] == "BIGINT"
    assert types["val_double"] == "DOUBLE"
    assert types["dt"] == "DATE"
    assert types["code"] == "VARCHAR"
    assert types["country"] == "VARCHAR"
    assert report.null_tokens == {"NULL": 2000, "None": 1000}
    assert elapsed <= base * MAX_SLOWDOWN, f"{elapsed:.2f} с против {base:.2f} с без очистки"


def test_wide_survey_105_columns(survey_105: Path) -> None:
    base = best_time(lambda: read_plain(survey_105))
    elapsed = best_time(lambda: load_clean(survey_105))

    db = DuckDBManager(":memory:")
    report = load_csv(db, "bench", survey_105)
    db.close()
    assert len(report.retyped) == 104
    assert report.null_tokens == {"NULL": 104 * 150}
    assert elapsed <= base * MAX_SLOWDOWN, f"{elapsed:.2f} с против {base:.2f} с без очистки"
