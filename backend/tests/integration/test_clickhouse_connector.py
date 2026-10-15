import base64
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request

import pytest

from app.core.data.connectors.clickhouse import ClickHouseConnector
from app.core.data.duckdb_manager import DuckDBManager

DSN = os.environ.get("TEST_CLICKHOUSE_DSN", "clickhouse://sber:sber@localhost:8123/sber_demo")


def clickhouse_http(sql: str) -> str:
    parts = urllib.parse.urlsplit(DSN)
    url = f"http://{parts.hostname}:{parts.port or 8123}/"
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
def db():
    manager = DuckDBManager(":memory:")
    yield manager
    manager.close()


def test_clickhouse_tables_become_views(table: str, db: DuckDBManager) -> None:
    try:
        views = ClickHouseConnector(DSN).connect(db)
    except NotImplementedError:
        pytest.skip("ClickHouseConnector ещё не реализован (round-2)")
    assert table in views
    assert db.query(f"SELECT COUNT(*) AS total FROM {table}") == [{"total": 3}]
