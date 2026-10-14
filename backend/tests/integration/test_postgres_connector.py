import os
import secrets

import duckdb
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.data.connectors.pg_dsn import parse_dsn
from app.core.data.connectors.postgres import PostgresConnectionError, PostgresConnector
from app.core.data.duckdb_manager import DuckDBManager
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


@pytest.fixture(scope="module")
def schema():
    try:
        con = admin_connection()
    except duckdb.Error as error:
        pytest.skip(f"PostgreSQL недоступен ({DSN.split('@')[-1]}): {type(error).__name__}")
    name = f"it_{secrets.token_hex(4)}"
    statements = [f"CREATE SCHEMA {name}"] + [sql.format(schema=name) for sql in SEED]
    for sql in statements:
        con.execute("CALL postgres_execute('admin', $sql)", {"sql": sql})
    yield name
    con.execute("CALL postgres_execute('admin', $sql)", {"sql": f"DROP SCHEMA {name} CASCADE"})
    con.close()


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
    password = parse_dsn(DSN).password
    PostgresConnector(DSN, schema=schema).connect(db)
    paths = db.query("SELECT path FROM duckdb_databases() WHERE type = 'postgres'")
    secrets_rows = db.query("SELECT secret_string FROM duckdb_secrets()")
    if password:
        assert all(password not in str(row) for row in paths + secrets_rows)


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
