import uuid

import pytest

from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.profiling import ColumnStats, TableProfile, profile_table
from app.core.memory.profile_memory import ProfileMemory
from app.models.enrichment import ColumnEnrichment

TYPED_TABLE = """CREATE TABLE typed AS SELECT * FROM (VALUES
    (1, 1.5::DOUBLE, 12.25::DECIMAL(10, 2), 170141183460469231731687303715884105727::HUGEINT,
     'msk', DATE '2024-01-05', TIMESTAMP '2024-01-05 10:00:00',
     TIMESTAMPTZ '2024-01-05 10:00:00+03', TIMESTAMP_NS '2024-01-05 10:00:00.123456789',
     '7f1c2a7e-3a0f-4c55-9a8b-2a3c4d5e6f70'::UUID, true, 'a'::ENUM ('a', 'b'), INTERVAL 1 DAY),
    (2, 'nan'::DOUBLE, 1.5, 5, NULL, DATE '2024-02-05', TIMESTAMP '2024-02-05 10:00:00',
     TIMESTAMPTZ '2024-02-05 10:00:00+03', NULL,
     '00000000-0000-0000-0000-000000000001'::UUID, false, 'b', INTERVAL 2 DAY),
    (3, 100.0, 1.5, 5, 'msk', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)
) AS rows(id, ratio, price, huge, region, day, moment, moment_tz, moment_ns, token, flag, grade,
          span)"""


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE sales AS SELECT * FROM (VALUES "
        "(1.0, 'msk', DATE '2024-01-05'), (2.0, 'spb', DATE '2024-02-05'), "
        "(2.0, NULL, DATE '2024-03-05'), (3.0, 'msk', DATE '2024-04-05'), "
        "(100.0, 'msk', DATE '2024-05-05')) AS rows(amount, region, day)"
    )
    yield manager
    manager.close()


def enrichment(column: str, **fields) -> ColumnEnrichment:
    values = {
        "title": f"Название {column}",
        "description": f"описание {column}",
        "description_source": "source",
        "fingerprint": f"fp-{column}",
    }
    return ColumnEnrichment(column=column, **(values | fields))


def test_profile_survives_restart_with_all_types(tmp_path) -> None:
    path = str(tmp_path / "main.duckdb")
    first = DuckDBManager(path)
    first.execute(TYPED_TABLE)
    first.execute("CREATE TABLE empty_sales (amount DOUBLE, day DATE, token UUID)")
    memory = ProfileMemory(first)
    typed = profile_table(first, "typed", approx=False)
    empty = profile_table(first, "empty_sales", approx=False)
    memory.save_profile(typed)
    memory.save_profile(empty)
    first.close()

    second = DuckDBManager(path)
    try:
        memory = ProfileMemory(second)
        assert memory.load_profile("typed") == typed
        assert memory.load_profile("empty_sales") == empty
    finally:
        second.close()
    restored = {column.name: column for column in typed.columns}
    assert restored["day"].min_date.isoformat() == "2024-01-05"
    assert restored["moment_tz"].max is not None
    assert restored["huge"].max == 170141183460469231731687303715884105727
    assert all(isinstance(entry["value"], uuid.UUID) for entry in restored["token"].top_values)
    assert restored["region"].top_values == [{"value": "msk", "count": 2}]
    assert restored["span"].kind == "other"
    assert empty.row_count == 0
    assert all(column.top_values == [] for column in empty.columns)


def test_missing_passport() -> None:
    db = DuckDBManager(":memory:")
    memory = ProfileMemory(db)
    assert memory.load_profile("missing") is None
    assert memory.load_enrichment("missing") == []
    assert memory.load_glossary("missing") is None
    db.close()


def test_meta_schema_holds_passport(db) -> None:
    memory = ProfileMemory(db)
    profile = profile_table(db, "sales", approx=False)
    memory.save_profile(profile, glossary={"amount": "сумма"})
    tables = db.query(
        "SELECT table_name, row_count, column_count, glossary FROM _meta.table_passport"
    )
    assert tables == [
        {
            "table_name": "sales",
            "row_count": 5,
            "column_count": 3,
            "glossary": '{"amount": "сумма"}',
        }
    ]
    columns = db.query(
        "SELECT column_name, position, kind, min_value, top_values, lower_fence, upper_fence, "
        "outlier_count, title, fingerprint FROM _meta.column_passport ORDER BY position"
    )
    amount, region, day = columns
    assert amount["column_name"] == "amount"
    assert (amount["lower_fence"], amount["upper_fence"], amount["outlier_count"]) == (0.5, 4.5, 1)
    assert amount["title"] is None and amount["fingerprint"] is None
    assert region["top_values"] == '[{"value": "msk", "count": 3}, {"value": "spb", "count": 1}]'
    assert (region["lower_fence"], region["upper_fence"]) == (None, None)
    assert (day["position"], day["kind"], day["min_value"]) == (2, "temporal", '"2024-01-05"')


def test_upsert_keeps_enrichment_and_drops_removed_columns(db) -> None:
    memory = ProfileMemory(db)
    memory.save_profile(profile_table(db, "sales", approx=False))
    memory.save_enrichment("sales", [enrichment("amount"), enrichment("region")])
    db.execute("ALTER TABLE sales DROP COLUMN day")
    db.execute("ALTER TABLE sales ADD COLUMN channel VARCHAR DEFAULT 'web'")
    db.execute("INSERT INTO sales VALUES (4.0, 'kzn', 'app')")
    updated = profile_table(db, "sales", approx=False)
    memory.save_profile(updated)
    assert memory.load_profile("sales") == updated
    saved = {row.column: row for row in memory.load_enrichment("sales")}
    assert list(saved) == ["amount", "region", "channel"]
    assert saved["amount"] == enrichment("amount")
    assert saved["region"].title == "Название region"
    assert saved["channel"] == ColumnEnrichment(column="channel")
    assert db.query("SELECT row_count FROM _meta.table_passport") == [{"row_count": 6}]


def test_glossary_is_kept_unless_replaced(db) -> None:
    memory = ProfileMemory(db)
    profile = profile_table(db, "sales", approx=False)
    memory.save_profile(profile, glossary={"amount": "сумма"})
    memory.save_profile(profile)
    assert memory.load_glossary("sales") == {"amount": "сумма"}
    memory.save_profile(profile, glossary={})
    assert memory.load_glossary("sales") == {}


def test_save_enrichment_marks_time(db) -> None:
    memory = ProfileMemory(db)
    memory.save_profile(profile_table(db, "sales", approx=False))
    assert db.query("SELECT enriched_at IS NULL AS pending FROM _meta.table_passport") == [
        {"pending": True}
    ]
    row = enrichment("region", title=None, description=None, description_source="none")
    memory.save_enrichment("sales", [row])
    assert memory.load_enrichment("sales")[1] == row
    flags = db.query(
        "SELECT column_name, enriched_at IS NOT NULL AS done FROM _meta.column_passport "
        "ORDER BY position"
    )
    assert [flag["done"] for flag in flags] == [False, True, False]
    assert db.query("SELECT enriched_at IS NOT NULL AS done FROM _meta.table_passport") == [
        {"done": True}
    ]


def test_delete_removes_only_that_table(db) -> None:
    db.execute("CREATE TABLE other AS SELECT 1 AS id")
    memory = ProfileMemory(db)
    memory.save_profile(profile_table(db, "sales", approx=False))
    memory.save_profile(profile_table(db, "other", approx=False))
    memory.delete("sales")
    assert memory.load_profile("sales") is None
    assert memory.load_enrichment("sales") == []
    assert memory.load_profile("other").row_count == 1


def test_failed_save_is_rolled_back(db) -> None:
    memory = ProfileMemory(db)
    broken = TableProfile(
        table="broken",
        row_count=1,
        column_count=2,
        columns=[
            ColumnStats(name="ok", dtype="INTEGER", kind="numeric"),
            ColumnStats(name="bad", dtype="VARCHAR", kind="text", min=object()),
        ],
        profiled_at="2026-10-15T06:00:00+00:00",
    )
    with pytest.raises(TypeError):
        memory.save_profile(broken)
    assert memory.load_profile("broken") is None
    assert db.query("SELECT COUNT(*) AS total FROM _meta.column_passport") == [{"total": 0}]


def test_no_json_files_are_written(db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    memory = ProfileMemory(db)
    memory.save_profile(profile_table(db, "sales", approx=False))
    memory.save_enrichment("sales", [enrichment("amount")])
    assert list(tmp_path.iterdir()) == []


def test_old_passport_gets_title_column(tmp_path) -> None:
    path = str(tmp_path / "main.duckdb")
    old = DuckDBManager(path)
    old.execute("CREATE SCHEMA _meta")
    old.execute(
        "CREATE TABLE _meta.column_passport (table_name VARCHAR, column_name VARCHAR, "
        "description VARCHAR, aliases VARCHAR[] NOT NULL DEFAULT [])"
    )
    old.close()
    db = DuckDBManager(path)
    try:
        ProfileMemory(db)
        columns = db.query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = '_meta' AND table_name = 'column_passport'"
        )
        assert "title" in {row["column_name"] for row in columns}
    finally:
        db.close()


def test_quality_flags_and_empty_rows_survive_save(db) -> None:
    db.execute(
        "CREATE TABLE dirty AS SELECT * FROM (VALUES ('1', 'a'), ('NULL', 'b'), (NULL, NULL)) "
        "AS rows(score, note)"
    )
    memory = ProfileMemory(db)
    profile = profile_table(db, "dirty", approx=False)
    memory.save_profile(profile)
    loaded = memory.load_profile("dirty")
    assert loaded == profile
    assert loaded.empty_rows == 1
    assert loaded.column("score").flags == ["numeric_as_text", "null_tokens:1"]


def test_old_passport_gets_quality_columns(tmp_path) -> None:
    path = str(tmp_path / "main.duckdb")
    old = DuckDBManager(path)
    old.execute("CREATE SCHEMA _meta")
    old.execute(
        "CREATE TABLE _meta.table_passport (table_name VARCHAR PRIMARY KEY, "
        "row_count BIGINT NOT NULL, column_count INTEGER NOT NULL, glossary JSON, "
        "summary VARCHAR, profiled_at TIMESTAMPTZ NOT NULL, enriched_at TIMESTAMPTZ)"
    )
    old.execute(
        "INSERT INTO _meta.table_passport (table_name, row_count, column_count, profiled_at) "
        "VALUES ('sales', 0, 0, now())"
    )
    old.close()
    db = DuckDBManager(path)
    try:
        ProfileMemory(db)
        columns = db.query(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = '_meta'"
        )
        names = {(row["table_name"], row["column_name"]) for row in columns}
        assert ("table_passport", "empty_rows") in names
        assert ("column_passport", "flags") in names
        assert ProfileMemory(db).load_profile("sales").empty_rows == 0
    finally:
        db.close()
