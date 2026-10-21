"""API сессий: создание только над подключённой таблицей и смена таблицы."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.session_memory import SessionMemory
from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE sales AS SELECT 'msk' AS region, 10.0 AS revenue")
    db.execute("CREATE TABLE users AS SELECT 25 AS age")
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db)
    db.close()


def test_create_session_binds_table_and_columns(client) -> None:
    response = client.post("/api/sessions", json={"table": "sales"})
    assert response.status_code == 200
    body = response.json()
    assert body["table"] == "sales"
    memory = SessionMemory(body["session_id"])
    assert memory.table == "sales"
    assert memory.build_context("")["columns"] == ["region", "revenue"]


def test_create_session_requires_table(client) -> None:
    assert client.post("/api/sessions", json={}).status_code == 422


def test_create_session_with_unknown_table_is_404(client) -> None:
    assert client.post("/api/sessions", json={"table": "missing"}).status_code == 404


def test_patch_switches_table(client) -> None:
    session_id = client.post("/api/sessions", json={"table": "sales"}).json()["session_id"]
    response = client.patch(f"/api/sessions/{session_id}", json={"table": "users"})
    assert response.status_code == 200
    assert response.json()["table"] == "users"
    memory = SessionMemory(session_id)
    assert memory.table == "users"
    assert memory.build_context("")["columns"] == ["age"]
    assert client.get(f"/api/sessions/{session_id}").json()["table"] == "users"


def test_patch_unknown_session_is_404(client) -> None:
    assert client.patch("/api/sessions/nope", json={"table": "users"}).status_code == 404


def test_patch_unknown_table_is_404(client) -> None:
    session_id = client.post("/api/sessions", json={"table": "sales"}).json()["session_id"]
    response = client.patch(f"/api/sessions/{session_id}", json={"table": "missing"})
    assert response.status_code == 404
    assert SessionMemory(session_id).table == "sales"


def test_get_unknown_session_is_404(client) -> None:
    assert client.get("/api/sessions/nope").status_code == 404
