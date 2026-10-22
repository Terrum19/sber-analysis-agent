import base64
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.data.connectors.clickhouse import ClickHouseConnectionError, ClickHouseConnector
from app.core.data.duckdb_manager import DuckDBManager
from app.main import app

DSN = os.environ.get("TEST_CLICKHOUSE_DSN", "clickhouse://sber:sber@localhost:9000/sber_demo")


def clickhouse_http(sql: str) -> str:
    parts = urllib.parse.urlsplit(DSN)
    http_port = 8123 if parts.port == 9000 else (parts.port or 8123)
    url = f"http://{parts.hostname}:{http_port}/"
    request = urllib.request.Request(url, data=sql.encode(), method="POST")
    credentials = f"{parts.username or 'default'}:{parts.password or ''}"
    request.add_header("Authorization", "Basic " + base64.b64encode(credentials.encode()).decode())
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read().decode()


@pytest.fixture(scope="module")
def table():
    try:
        clickhouse_http("SELECT 1")
    except (urllib.error.URLError, OSError) as error:
        pytest.skip(f"ClickHouse недоступен ({type(error).__name__})")
    database = urllib.parse.urlsplit(DSN).path.lstrip("/") or "default"
    name = f"it_orders_{secrets.token_hex(4)}"
    clickhouse_http(f"CREATE TABLE {database}.{name} (id UInt32, amount Float64) ENGINE = Memory")
    clickhouse_http(f"INSERT INTO {database}.{name} VALUES (1, 10.5), (2, 20.0), (3, 5.25)")
    yield name
    clickhouse_http(f"DROP TABLE IF EXISTS {database}.{name}")


@pytest.fixture
def empty_table():
    database = urllib.parse.urlsplit(DSN).path.lstrip("/") or "default"
    name = f"it_empty_{secrets.token_hex(4)}"
    clickhouse_http(f"CREATE TABLE {database}.{name} (id UInt32) ENGINE = Memory")
    yield name
    clickhouse_http(f"DROP TABLE IF EXISTS {database}.{name}")


@pytest.fixture
def cyrillic_table():
    database = urllib.parse.urlsplit(DSN).path.lstrip("/") or "default"
    name = f"it_cyrillic_{secrets.token_hex(4)}"
    clickhouse_http(
        f"CREATE TABLE {database}.{name} "
        '("Идентификатор" UInt32, "Сумма заказа" Float64) ENGINE = Memory'
    )
    clickhouse_http(
        f'INSERT INTO {database}.{name} ("Идентификатор", "Сумма заказа") VALUES (1, 12.5)'
    )
    yield name
    clickhouse_http(f"DROP TABLE IF EXISTS {database}.{name}")


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    yield manager
    manager.close()


def test_clickhouse_tables_become_views_and_lazy(table: str, db: DuckDBManager) -> None:
    views = ClickHouseConnector(DSN).connect(db)

    assert table in views
    view_rows = db.query(
        "SELECT view_name, sql FROM duckdb_views() WHERE temporary AND view_name = $name",
        {"name": table},
    )
    assert len(view_rows) == 1
    assert "AS SELECT * FROM" in view_rows[0]["sql"]

    local_tables = db.query(
        "SELECT database_name, schema_name FROM duckdb_tables() WHERE table_name = $name",
        {"name": table},
    )
    assert all(
        row["database_name"] != "temp" or row["schema_name"] != "main" for row in local_tables
    )

    assert db.query(f"SELECT COUNT(*) AS total FROM {table}") == [{"total": 3}]


def test_clickhouse_filtered_query_uses_remote_scan(table: str, db: DuckDBManager) -> None:
    views = ClickHouseConnector(DSN).connect(db)
    assert table in views

    rows = db.query(f"SELECT id, amount FROM {table} WHERE amount > 15")
    assert rows == [{"id": 2, "amount": 20.0}]

    explain_rows = db.query(f"EXPLAIN SELECT id, amount FROM {table} WHERE amount > 15")
    plan_text = " ".join(str(val) for r in explain_rows for val in r.values()).lower()
    assert "scan" in plan_text or table.lower() in plan_text


def test_clickhouse_supports_cyrillic_column_names(cyrillic_table: str, db: DuckDBManager) -> None:
    views = ClickHouseConnector(DSN).connect(db)

    assert cyrillic_table in views
    assert db.query(f'SELECT "Идентификатор", "Сумма заказа" FROM "{cyrillic_table}"') == [
        {"Идентификатор": 1, "Сумма заказа": 12.5}
    ]


def test_clickhouse_supports_existing_empty_table(empty_table: str, db: DuckDBManager) -> None:
    views = ClickHouseConnector(DSN).connect(db)

    assert empty_table in views
    assert db.query(f'SELECT COUNT(*) AS total FROM "{empty_table}"') == [{"total": 0}]


def test_clickhouse_does_not_import_tables_from_other_databases(
    table: str, db: DuckDBManager
) -> None:
    other_database = f"it_other_{secrets.token_hex(4)}"
    other_table = f"it_other_table_{secrets.token_hex(4)}"
    clickhouse_http(f"CREATE DATABASE {other_database}")
    clickhouse_http(f"CREATE TABLE {other_database}.{other_table} (id UInt32) ENGINE = Memory")
    try:
        views = ClickHouseConnector(DSN).connect(db)
        assert table in views
        assert other_table not in views
    finally:
        clickhouse_http(f"DROP DATABASE IF EXISTS {other_database}")


def test_clickhouse_wrong_password_is_readable_and_secret_free(
    table: str, db: DuckDBManager
) -> None:
    parts = urllib.parse.urlsplit(DSN)
    wrong_dsn = urllib.parse.urlunsplit(
        (
            "clickhouse",
            f"{parts.username or 'default'}:wrong_password@{parts.hostname}:{parts.port or 9000}",
            parts.path,
            "",
            "",
        )
    )

    with pytest.raises(ClickHouseConnectionError) as raised:
        ClickHouseConnector(wrong_dsn).connect(db)

    message = str(raised.value)
    assert "wrong_password" not in message
    assert "парол" in message.lower() or "учётн" in message.lower()


def test_api_attach_clickhouse(table: str, db: DuckDBManager) -> None:
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).post(
            "/api/datasources/db", json={"kind": "clickhouse", "dsn": DSN}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "clickhouse"
    assert table in body["tables"]
    assert body["datasource_id"].startswith("ch_")
    assert db.query(f"SELECT COUNT(*) AS total FROM {table}") == [{"total": 3}]


def test_clickhouse_column_comments(db: DuckDBManager) -> None:
    try:
        clickhouse_http("SELECT 1")
    except (urllib.error.URLError, OSError) as error:
        pytest.skip(f"ClickHouse недоступен ({type(error).__name__})")
    database = urllib.parse.urlsplit(DSN).path.lstrip("/") or "default"
    name = f"it_comments_{secrets.token_hex(4)}"
    clickhouse_http(
        f"CREATE TABLE {database}.{name} (id UInt32 COMMENT 'Код заказа', amount Float64 COMMENT 'Сумма') ENGINE = Memory"
    )
    try:
        connector = ClickHouseConnector(DSN)
        views = connector.connect(db)
        assert name in views
        comments = connector.column_comments(db, name)
        assert comments == {"id": "Код заказа", "amount": "Сумма"}
    finally:
        clickhouse_http(f"DROP TABLE IF EXISTS {database}.{name}")
