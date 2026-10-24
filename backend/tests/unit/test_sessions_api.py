"""API сессий: создание только над подключённой таблицей, смена таблицы,
переименование, список и чтение после перезапуска приложения."""

import itertools
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.routes import sessions
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory import session_memory
from app.core.memory.profile_memory import ProfileMemory
from app.core.memory.session_memory import SessionMemory
from app.main import app


@pytest.fixture
def new_client() -> Iterator[Callable[[], TestClient]]:
    """Новый экземпляр приложения: свой DuckDB, общий только SQLite сессий."""
    opened: list[DuckDBManager] = []

    def make() -> TestClient:
        db = DuckDBManager(":memory:")
        db.execute("CREATE TABLE sales AS SELECT 'msk' AS region, 10.0 AS revenue")
        db.execute("CREATE TABLE users AS SELECT 25 AS age")
        opened.append(db)
        app.dependency_overrides[get_db] = lambda: db
        return TestClient(app)

    yield make
    app.dependency_overrides.pop(get_db, None)
    for db in opened:
        db.close()


@pytest.fixture
def client(new_client) -> TestClient:
    return new_client()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """updated_at растёт на секунду с каждой записью: порядок не зависит от таймера."""
    ticks = itertools.count(1)
    monkeypatch.setattr(session_memory.time, "time", lambda: float(next(ticks)))


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


def test_delete_session_removes_session(client) -> None:
    session_id = client.post("/api/sessions", json={"table": "sales"}).json()["session_id"]
    assert client.get(f"/api/sessions/{session_id}").status_code == 200
    res = client.delete(f"/api/sessions/{session_id}")
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    assert client.get(f"/api/sessions/{session_id}").status_code == 404


def test_delete_session_not_found_is_404(client) -> None:
    assert client.delete("/api/sessions/nope").status_code == 404


def test_session_survives_new_app(new_client) -> None:
    body = {"table": "sales", "title": "Продажи", "datasource_id": "csv_1"}
    created = new_client().post("/api/sessions", json=body).json()
    assert created["created_at"]
    response = new_client().get(f"/api/sessions/{created['session_id']}")
    assert response.status_code == 200
    assert response.json() == created


def test_patch_keeps_title_after_restart(new_client) -> None:
    body = {"table": "sales", "title": "Продажи", "datasource_id": "csv_1"}
    created = new_client().post("/api/sessions", json=body).json()
    response = new_client().patch(f"/api/sessions/{created['session_id']}", json={"table": "users"})
    assert response.json()["title"] == "Продажи"
    assert response.json()["created_at"] == created["created_at"]


def test_patch_to_other_table_clears_datasource_id(client) -> None:
    body = {"table": "sales", "datasource_id": "csv_1"}
    session_id = client.post("/api/sessions", json=body).json()["session_id"]
    same = client.patch(f"/api/sessions/{session_id}", json={"table": "sales"})
    assert same.json()["datasource_id"] == "csv_1"
    other = client.patch(f"/api/sessions/{session_id}", json={"table": "users"})
    assert other.json()["datasource_id"] is None
    assert client.get(f"/api/sessions/{session_id}").json()["datasource_id"] is None


def test_list_sessions_recent_first_after_restart(new_client, clock) -> None:
    client = new_client()
    first = client.post("/api/sessions", json={"table": "sales"}).json()["session_id"]
    second = client.post("/api/sessions", json={"table": "users"}).json()["session_id"]
    client.patch(f"/api/sessions/{first}", json={"table": "users"})
    response = new_client().get("/api/sessions")
    assert response.status_code == 200
    assert [s["session_id"] for s in response.json()] == [first, second]
    assert response.json()[0]["table"] == "users"


def test_list_sessions_is_limited(client, clock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sessions, "SESSION_LIST_LIMIT", 2)
    ids = [
        client.post("/api/sessions", json={"table": "sales"}).json()["session_id"] for _ in range(3)
    ]
    assert [s["session_id"] for s in client.get("/api/sessions").json()] == [ids[2], ids[1]]


def test_list_sessions_empty(client) -> None:
    assert client.get("/api/sessions").json() == []


def test_create_session_over_temp_view(client) -> None:
    db = app.dependency_overrides[get_db]()
    db.execute("CREATE TEMP VIEW pg_orders AS SELECT 1 AS order_id")
    response = client.post("/api/sessions", json={"table": "pg_orders"})
    assert response.status_code == 200
    assert SessionMemory(response.json()["session_id"]).build_context("")["columns"] == ["order_id"]


def test_tables_lists_user_tables_only(client) -> None:
    db = app.dependency_overrides[get_db]()
    db.execute("CREATE TEMP VIEW pg_orders AS SELECT 1 AS order_id")
    db.save_step_result("sales", "SELECT * FROM sales")
    ProfileMemory(db)
    response = client.get("/api/datasources/tables")
    assert response.status_code == 200
    assert response.json() == ["pg_orders", "sales", "users"]


def test_patch_same_table_keeps_pending_recovery(client) -> None:
    session_id = client.post("/api/sessions", json={"table": "sales"}).json()["session_id"]
    SessionMemory(session_id).set_pending_recovery({"step_id": 1})
    client.patch(f"/api/sessions/{session_id}", json={"table": "sales"})
    assert SessionMemory(session_id).get_pending_recovery() == {"step_id": 1}


def test_list_sessions_returns_same_fields_as_get(client) -> None:
    body = {"table": "sales", "title": "Продажи", "datasource_id": "csv_1"}
    created = client.post("/api/sessions", json=body).json()
    assert client.get("/api/sessions").json() == [created]


def test_list_sessions_offset_reaches_older(client, clock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sessions, "SESSION_LIST_LIMIT", 2)
    ids = [
        client.post("/api/sessions", json={"table": "sales"}).json()["session_id"] for _ in range(3)
    ]
    page = client.get("/api/sessions", params={"offset": 2}).json()
    assert [s["session_id"] for s in page] == [ids[0]]
    assert client.get("/api/sessions", params={"offset": -1}).status_code == 422


def test_patch_title_renames_session_after_restart(new_client) -> None:
    body = {"table": "sales", "title": "Продажи", "datasource_id": "csv_1"}
    created = new_client().post("/api/sessions", json=body).json()
    session_id = created["session_id"]
    response = new_client().patch(f"/api/sessions/{session_id}", json={"title": "  Выручка  "})
    assert response.status_code == 200
    assert response.json() == {**created, "title": "Выручка"}
    fresh = new_client()
    assert fresh.get(f"/api/sessions/{session_id}").json()["title"] == "Выручка"
    assert fresh.get("/api/sessions").json()[0]["title"] == "Выручка"


def test_patch_title_keeps_table_and_steps(client) -> None:
    session_id = client.post("/api/sessions", json={"table": "sales"}).json()["session_id"]
    SessionMemory(session_id).set_pending_recovery({"step_id": 1})
    response = client.patch(f"/api/sessions/{session_id}", json={"title": "Новое имя"})
    assert response.json()["table"] == "sales"
    assert SessionMemory(session_id).get_pending_recovery() == {"step_id": 1}


def test_patch_title_and_table_together(client) -> None:
    session_id = client.post("/api/sessions", json={"table": "sales"}).json()["session_id"]
    response = client.patch(
        f"/api/sessions/{session_id}", json={"table": "users", "title": "Возраст"}
    )
    assert response.status_code == 200
    assert response.json()["table"] == "users"
    assert response.json()["title"] == "Возраст"


def test_patch_unknown_table_keeps_title(client) -> None:
    body = {"table": "sales", "title": "Продажи"}
    session_id = client.post("/api/sessions", json=body).json()["session_id"]
    response = client.patch(f"/api/sessions/{session_id}", json={"table": "missing", "title": "X"})
    assert response.status_code == 404
    assert SessionMemory(session_id).title == "Продажи"


@pytest.mark.parametrize(
    "body", [{}, {"title": None}, {"table": None, "title": None}, {"title": "   "}]
)
def test_patch_without_table_or_title_is_422(client, body) -> None:
    session_id = client.post("/api/sessions", json={"table": "sales"}).json()["session_id"]
    assert client.patch(f"/api/sessions/{session_id}", json=body).status_code == 422


def test_patch_title_unknown_session_is_404(client) -> None:
    assert client.patch("/api/sessions/nope", json={"title": "X"}).status_code == 404
