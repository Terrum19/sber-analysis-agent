import duckdb
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.data.duckdb_manager import DuckDBManager
from app.main import app


@pytest.fixture
def client():
    db = DuckDBManager(":memory:")
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()
    db.close()


def test_invalid_dsn_is_400(client: TestClient) -> None:
    response = client.post(
        "/api/datasources/db", json={"kind": "postgres", "dsn": "postgres://user:TopSecret@/db"}
    )
    assert response.status_code == 400
    assert "хост" in response.json()["detail"]
    assert "TopSecret" not in response.text


def test_unknown_kind_is_422(client: TestClient) -> None:
    response = client.post("/api/datasources/db", json={"kind": "mysql", "dsn": "x"})
    assert response.status_code == 422


def test_clickhouse_is_501(client: TestClient) -> None:
    response = client.post(
        "/api/datasources/db", json={"kind": "clickhouse", "dsn": "clickhouse://localhost"}
    )
    assert response.status_code == 501


def test_unreachable_postgres_is_502(client: TestClient) -> None:
    response = client.post(
        "/api/datasources/db",
        json={"kind": "postgres", "dsn": "postgresql://user:TopSecret@127.0.0.1:1/db"},
    )
    assert response.status_code in (502, 503)
    assert "TopSecret" not in response.text


def test_missing_extension_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def offline(self, sql: str, params: dict | None = None) -> None:
        raise duckdb.IOException("offline")

    monkeypatch.setattr(DuckDBManager, "execute", offline)
    response = client.post(
        "/api/datasources/db", json={"kind": "postgres", "dsn": "postgresql://u@localhost/db"}
    )
    assert response.status_code == 503
    assert "postgres_scanner" in response.json()["detail"]
