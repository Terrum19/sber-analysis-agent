import duckdb
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.data.connectors.factory import build_connector
from app.core.data.duckdb_manager import DuckDBManager
from app.main import app


def test_manager_creates_storage_directory(tmp_path) -> None:
    path = tmp_path / "storage" / "duckdb" / "main.duckdb"
    manager = DuckDBManager(str(path))
    try:
        assert path.parent.is_dir()
        assert manager.query("SELECT 42 AS answer") == [{"answer": 42}]
    finally:
        manager.close()
    assert path.is_file()


def test_postgres_scanner_is_installed_and_loads() -> None:
    con = duckdb.connect()
    con.execute("INSTALL postgres_scanner")
    con.execute("LOAD postgres_scanner")
    installed, loaded = con.execute(
        "SELECT installed, loaded FROM duckdb_extensions() WHERE extension_name = 'postgres_scanner'"
    ).fetchone()
    assert installed is True
    assert loaded is True
    con.close()


@pytest.mark.parametrize(
    ("kind", "dsn"),
    [
        ("postgres", "postgresql://user:secret@127.0.0.1:1/db"),
        ("clickhouse", "clickhouse://user:secret@127.0.0.1:1/db"),
    ],
)
def test_connectors_are_constructible(kind: str, dsn: str) -> None:
    assert build_connector(kind, dsn) is not None


@pytest.mark.parametrize(
    ("kind", "dsn", "status"),
    [
        ("postgres", "postgresql://user:secret@127.0.0.1:1/db", 502),
        ("clickhouse", "clickhouse://user:secret@127.0.0.1:1/db", 501),
    ],
)
def test_connectors_fail_without_unhandled_exceptions(kind: str, dsn: str, status: int) -> None:
    db = DuckDBManager(":memory:")
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.post("/api/datasources/db", json={"kind": kind, "dsn": dsn})
    finally:
        app.dependency_overrides.clear()
        db.close()
    assert response.status_code == status
    assert response.json()["detail"]
    assert "secret" not in response.text
