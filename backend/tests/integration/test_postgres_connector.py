import asyncio
import json
import os
import secrets

import duckdb
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.data import ingest, restore
from app.core.data.connectors.pg_dsn import parse_dsn
from app.core.data.connectors.postgres import PostgresConnectionError, PostgresConnector
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.datasource_store import DatasourceStore
from app.core.memory.profile_memory import ProfileMemory
from app.main import app

DSN = os.environ.get("TEST_POSTGRES_DSN", "postgresql://sber:sber@localhost:5432/sber_demo")

SEED = [
    (
        "CREATE TABLE {schema}.orders (id integer PRIMARY KEY, amount numeric(10, 2), "
        "created_at timestamptz DEFAULT now())"
    ),
    "INSERT INTO {schema}.orders (id, amount) VALUES (1, 10.50), (2, 20.00), (3, 5.25)",
    'CREATE TABLE {schema}."Mixed Case" ("Some Column" text)',
    "INSERT INTO {schema}.\"Mixed Case\" VALUES ('a'), ('b')",
    "CREATE VIEW {schema}.big_orders AS SELECT * FROM {schema}.orders WHERE amount > 10",
]


def admin_connection() -> duckdb.DuckDBPyConnection:
    dsn = parse_dsn(DSN)
    con = duckdb.connect()
    con.execute("INSTALL postgres_scanner")
    con.execute("LOAD postgres_scanner")
    if dsn.password:
        con.execute(
            "CREATE TEMPORARY SECRET admin_secret (TYPE postgres, PASSWORD $password)",
            {"password": dsn.password},
        )
    escaped = dsn.connection_string().replace("'", "''")
    options = "TYPE postgres, SECRET admin_secret" if dsn.password else "TYPE postgres"
    con.execute(f"ATTACH '{escaped}' AS admin ({options})")
    return con


def run_admin(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    con.execute("CALL postgres_execute('admin', $sql)", {"sql": sql})


@pytest.fixture(scope="module")
def admin():
    try:
        con = admin_connection()
    except duckdb.Error as error:
        pytest.skip(f"PostgreSQL недоступен ({DSN.split('@')[-1]}): {type(error).__name__}")
    yield con
    con.close()


@pytest.fixture(scope="module")
def schema(admin: duckdb.DuckDBPyConnection):
    name = f"it_{secrets.token_hex(4)}"
    statements = [f"CREATE SCHEMA {name}"] + [sql.format(schema=name) for sql in SEED]
    for sql in statements:
        run_admin(admin, sql)
    yield name
    run_admin(admin, f"DROP SCHEMA {name} CASCADE")


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    yield manager
    manager.close()


def test_views_are_created_for_every_table(schema: str, db: DuckDBManager) -> None:
    views = PostgresConnector(DSN, schema=schema).connect(db)
    assert views == ["Mixed Case", "big_orders", "orders"]
    assert db.query("SELECT COUNT(*) AS total FROM orders") == [{"total": 3}]
    assert db.query('SELECT COUNT(*) AS total FROM "Mixed Case"') == [{"total": 2}]
    assert db.query("SELECT COUNT(*) AS total FROM big_orders") == [{"total": 2}]


def test_duckdb_dialect_works_over_postgres(schema: str, db: DuckDBManager) -> None:
    PostgresConnector(DSN, schema=schema).connect(db)
    rows = db.query(
        "SELECT date_trunc('day', created_at) IS NOT NULL AS has_day, "
        "SUM(amount)::DOUBLE AS total FROM orders GROUP BY ALL"
    )
    assert rows == [{"has_day": True, "total": 35.75}]


def test_source_is_read_only(schema: str, db: DuckDBManager) -> None:
    connector = PostgresConnector(DSN, schema=schema)
    connector.connect(db)
    with pytest.raises(duckdb.Error, match="read-only"):
        db.execute(f"INSERT INTO {connector.alias}.{schema}.orders (id) VALUES (99)")


def test_password_is_not_visible_from_sql(schema: str, db: DuckDBManager) -> None:
    if not parse_dsn(DSN).password:
        pytest.skip("Сервер без пароля: скрывать нечего")
    PostgresConnector(DSN, schema=schema).connect(db)
    paths = db.query("SELECT path FROM duckdb_databases() WHERE type = 'postgres'")
    secrets_rows = db.query("SELECT secret_string FROM duckdb_secrets()")
    assert paths and all("password" not in row["path"] for row in paths)
    assert secrets_rows and all("password=redacted" in row["secret_string"] for row in secrets_rows)


def test_max_tables_limits_real_schema(schema: str, db: DuckDBManager) -> None:
    assert PostgresConnector(DSN, schema=schema, max_tables=2).connect(db) == [
        "Mixed Case",
        "big_orders",
    ]


def test_missing_schema_gives_empty_list(schema: str, db: DuckDBManager) -> None:
    assert PostgresConnector(DSN, schema=f"{schema}_absent").connect(db) == []
    assert db.query("SELECT COUNT(*) AS total FROM duckdb_databases() WHERE type = 'postgres'") == [
        {"total": 0}
    ]


def test_failed_reconnect_keeps_working_source(schema: str, db: DuckDBManager) -> None:
    dsn = parse_dsn(DSN)
    if not dsn.password:
        pytest.skip("Сервер без пароля: неверный пароль нельзя проверить")
    PostgresConnector(DSN, schema=schema).connect(db)
    wrong = dsn.connection_string() + " password=definitely-wrong"
    with pytest.raises(PostgresConnectionError) as raised:
        PostgresConnector(wrong, schema=schema).connect(db)
    assert "definitely-wrong" not in str(raised.value)
    assert db.query("SELECT COUNT(*) AS total FROM orders") == [{"total": 3}]


def test_successful_reconnect_drops_views_of_removed_tables(
    admin: duckdb.DuckDBPyConnection, db: DuckDBManager
) -> None:
    name = f"it_{secrets.token_hex(4)}"
    try:
        run_admin(admin, f"CREATE SCHEMA {name}")
        run_admin(admin, f"CREATE TABLE {name}.kept (id int)")
        run_admin(admin, f"CREATE TABLE {name}.doomed (id int)")
        first = PostgresConnector(DSN, schema=name)
        assert first.connect(db) == ["doomed", "kept"]
        run_admin(admin, f"DROP TABLE {name}.doomed")
        second = PostgresConnector(DSN, schema=name)
        assert second.connect(db) == ["kept"]
        views = db.query("SELECT view_name FROM duckdb_views() WHERE temporary AND NOT internal")
        assert [row["view_name"] for row in views] == ["kept"]
        attached = db.query("SELECT database_name FROM duckdb_databases() WHERE type = 'postgres'")
        assert attached == [{"database_name": second.alias}]
        assert db.query("SELECT COUNT(*) AS total FROM kept") == [{"total": 0}]
    finally:
        run_admin(admin, f"DROP SCHEMA IF EXISTS {name} CASCADE")


def test_no_dangling_views_after_restart(schema: str, tmp_path) -> None:
    path = str(tmp_path / "storage" / "main.duckdb")
    first = DuckDBManager(path)
    PostgresConnector(DSN, schema=schema).connect(first)
    first.close()
    second = DuckDBManager(path)
    try:
        leftovers = second.query("SELECT view_name FROM duckdb_views() WHERE NOT internal")
        assert leftovers == []
        assert PostgresConnector(DSN, schema=schema).connect(second)
        assert second.query("SELECT COUNT(*) AS total FROM orders") == [{"total": 3}]
    finally:
        second.close()


def test_saved_source_is_restored_after_restart(
    schema: str, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = str(tmp_path / "storage" / "main.duckdb")
    first = DuckDBManager(path)
    app.dependency_overrides[get_db] = lambda: first
    try:
        response = TestClient(app).post(
            "/api/datasources/db", json={"kind": "postgres", "dsn": DSN, "schema": schema}
        )
    finally:
        app.dependency_overrides.clear()
        first.close()
    assert response.status_code == 200
    identity = response.json()["datasource_id"]
    views = response.json()["tables"]
    stored = DatasourceStore().get(identity)
    password = parse_dsn(DSN).password
    assert password is None or password not in stored.dsn
    if password:
        monkeypatch.setenv(f"POSTGRES_PASSWORD__{identity}", password)

    second = DuckDBManager(path)
    try:
        memory = ProfileMemory(second)
        profiled_at = memory.load_profile("orders").profiled_at
        assert second.columns("orders") == []
        assert asyncio.run(restore.restore_saved_sources(second)) == [identity]
        assert sorted(PostgresConnector(DSN, schema=schema).existing_names(second)) == sorted(
            view.lower() for view in views
        )
        assert second.query("SELECT COUNT(*) AS total FROM orders") == [{"total": 3}]
        assert second.columns("orders") == ["id", "amount", "created_at"]
        assert memory.load_profile("orders").profiled_at == profiled_at

        app.dependency_overrides[get_db] = lambda: second
        try:
            assert TestClient(app).delete(f"/api/datasources/{identity}").status_code == 204
        finally:
            app.dependency_overrides.clear()
        assert second.columns("orders") == []
        assert memory.load_profile("orders") is None
        assert DatasourceStore().get(identity) is None
        assert asyncio.run(restore.restore_saved_sources(second)) == []
    finally:
        second.close()


def test_api_attach_postgres(schema: str, db: DuckDBManager) -> None:
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).post(
            "/api/datasources/db", json={"kind": "postgres", "dsn": DSN, "schema": schema}
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "postgres"
    assert body["tables"] == ["Mixed Case", "big_orders", "orders"]
    assert body["datasource_id"].startswith("pg_")
    assert db.query("SELECT COUNT(*) AS total FROM orders") == [{"total": 3}]


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def complete_structured(self, system: str, prompt: str, schema):
        start = prompt.index("Колонки (JSON):\n") + len("Колонки (JSON):\n")
        columns = json.loads(prompt[start : prompt.index("\n\n", start)])
        self.calls.append([column["column"] for column in columns])
        return schema.model_validate(
            {
                "columns": [
                    {
                        "column": column["column"],
                        "title": f"Название {column['column']}",
                        "description": f"Документ: {column['column']}",
                    }
                    for column in columns
                ]
            }
        )


async def test_column_comments_drive_enrichment(
    admin: duckdb.DuckDBPyConnection, db: DuckDBManager
) -> None:
    name = f"it_{secrets.token_hex(4)}"
    short = "Сумма заказа, руб."
    long = "Статус заказа: 1 — новый, 2 — оплачен, 3 — отгружен, 4 — доставлен. " * 4
    try:
        run_admin(admin, f"CREATE SCHEMA {name}")
        run_admin(
            admin, f"CREATE TABLE {name}.commented (amount numeric(10, 2), status int, region text)"
        )
        run_admin(admin, f"INSERT INTO {name}.commented VALUES (10.5, 1, 'msk'), (20, 2, 'spb')")
        run_admin(admin, f"COMMENT ON COLUMN {name}.commented.amount IS '{short}'")
        run_admin(admin, f"COMMENT ON COLUMN {name}.commented.status IS '{long}'")
        db.execute("CREATE TEMP VIEW commented AS SELECT 1 AS taken")
        connector = PostgresConnector(DSN, schema=name)
        with db.lock:
            views = list(ingest.attach(connector, db))
        assert views == [f"{parse_dsn(DSN).params['dbname']}_commented"]
        assert ProfileMemory(db).load_profile(views[0]).row_count == 2
        comments = connector.column_comments(db, views[0])
        assert comments == {"amount": short, "status": long}

        llm = FakeLLM()
        described = await ingest.describe(connector, db, views, llm=llm)
        result = {row.column: row for row in described[views[0]]}
        assert llm.calls == []
        assert result["amount"].description == short
        assert result["status"].description == long
        assert {result[name].description_source for name in ("amount", "status")} == {"source"}
        assert result["region"].description is None

        described = await ingest.describe(connector, db, views, "region — город", llm)
        result = {row.column: row for row in described[views[0]]}
        assert llm.calls == [["amount", "status", "region"]]
        assert result["amount"].description == short
        assert result["amount"].title == "Название amount"
        assert result["region"].description == "Документ: region"

        again = FakeLLM()
        await ingest.describe(connector, db, views, "region — город", again)
        assert again.calls == []
    finally:
        run_admin(admin, f"DROP SCHEMA IF EXISTS {name} CASCADE")
