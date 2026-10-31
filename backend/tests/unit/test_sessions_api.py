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
from app.models.research_step import ResearchStep


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


def asked(client: TestClient, body: dict) -> dict:
    """Новая сессия с одним вопросом: в список попадают только сессии с ходами."""
    created = client.post("/api/sessions", json=body).json()
    SessionMemory(created["session_id"]).start_turn("Что передо мной?")
    return created


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
    memory = SessionMemory(body["session_id"], app.dependency_overrides[get_db]())
    assert memory.table == "sales"
    assert memory.build_context("")["columns"] == ["region", "revenue"]


def test_create_session_without_table(client) -> None:
    response = client.post("/api/sessions", json={"title": "Без таблицы"})
    assert response.status_code == 200
    body = response.json()
    assert body["table"] is None
    assert client.get(f"/api/sessions/{body['session_id']}").json()["title"] == "Без таблицы"


def test_create_session_with_unknown_table_is_404(client) -> None:
    assert client.post("/api/sessions", json={"table": "missing"}).status_code == 404


def test_patch_switches_table(client) -> None:
    session_id = client.post("/api/sessions", json={"table": "sales"}).json()["session_id"]
    response = client.patch(f"/api/sessions/{session_id}", json={"table": "users"})
    assert response.status_code == 200
    assert response.json()["table"] == "users"
    memory = SessionMemory(session_id, app.dependency_overrides[get_db]())
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


def test_delete_session_removes_its_dashboards(client) -> None:
    session_id = client.post("/api/sessions", json={"table": "sales"}).json()["session_id"]
    dash_res = client.post(
        "/api/dashboards",
        json={"session_id": session_id, "title": "Дашборд сессии", "tiles": []},
    )
    assert dash_res.status_code == 201
    dash_id = dash_res.json()["id"]

    assert client.get(f"/api/dashboards/{dash_id}").status_code == 200
    assert len(client.get(f"/api/dashboards?session_id={session_id}").json()) == 1

    client.delete(f"/api/sessions/{session_id}")

    assert client.get(f"/api/dashboards/{dash_id}").status_code == 404
    assert len(client.get(f"/api/dashboards?session_id={session_id}").json()) == 0


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
    first = asked(client, {"table": "sales"})["session_id"]
    second = asked(client, {"table": "users"})["session_id"]
    client.patch(f"/api/sessions/{first}", json={"table": "users"})
    response = new_client().get("/api/sessions")
    assert response.status_code == 200
    assert [s["session_id"] for s in response.json()] == [first, second]
    assert response.json()[0]["table"] == "users"


def test_list_sessions_is_limited(client, clock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sessions, "SESSION_LIST_LIMIT", 2)
    ids = [asked(client, {"table": "sales"})["session_id"] for _ in range(3)]
    assert [s["session_id"] for s in client.get("/api/sessions").json()] == [ids[2], ids[1]]


def test_list_sessions_empty(client) -> None:
    assert client.get("/api/sessions").json() == []


def test_list_sessions_skips_sessions_without_questions(client) -> None:
    client.post("/api/sessions", json={"table": "sales"})
    client.post("/api/sessions", json={"table": "sales", "title": "Продажи"})
    session_id = asked(client, {"table": "sales"})["session_id"]
    assert [s["session_id"] for s in client.get("/api/sessions").json()] == [session_id]


def test_create_session_over_temp_view(client) -> None:
    db = app.dependency_overrides[get_db]()
    db.execute("CREATE TEMP VIEW pg_orders AS SELECT 1 AS order_id")
    response = client.post("/api/sessions", json={"table": "pg_orders"})
    assert response.status_code == 200
    memory = SessionMemory(response.json()["session_id"], db)
    assert memory.build_context("")["columns"] == ["order_id"]


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
    created = asked(client, {"table": "sales", "title": "Продажи", "datasource_id": "csv_1"})
    assert client.get("/api/sessions").json() == [created]


def test_list_sessions_offset_reaches_older(client, clock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sessions, "SESSION_LIST_LIMIT", 2)
    ids = [asked(client, {"table": "sales"})["session_id"] for _ in range(3)]
    page = client.get("/api/sessions", params={"offset": 2}).json()
    assert [s["session_id"] for s in page] == [ids[0]]
    assert client.get("/api/sessions", params={"offset": -1}).status_code == 422


def test_patch_title_renames_session_after_restart(new_client) -> None:
    created = asked(new_client(), {"table": "sales", "title": "Продажи", "datasource_id": "csv_1"})
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


def test_memories_api_lists_adds_and_toggles(client) -> None:
    session_id = client.post("/api/sessions", json={"table": "sales"}).json()["session_id"]
    url = f"/api/sessions/{session_id}/memories"
    assert client.get(url).json() == []

    created = client.post(url, json={"text": "  выручка   без НДС "})
    assert created.status_code == 201
    item = created.json()
    assert (item["text"], item["source"], item["enabled"], item["turn_id"]) == (
        "выручка без НДС",
        "user",
        True,
        None,
    )

    off = client.patch(f"{url}/{item['memory_id']}", json={"enabled": False})
    assert off.json()["enabled"] is False
    # Выключенная запись в списке остаётся, но в контекст хода не идёт.
    assert [m["enabled"] for m in client.get(url).json()] == [False]
    assert SessionMemory(session_id).build_context("?")["memories"] == []

    # Тот же текст снова — без дубля, запись включается.
    again = client.post(url, json={"text": "Выручка без НДС"}).json()
    assert again["memory_id"] == item["memory_id"]
    assert again["enabled"] is True
    assert len(client.get(url).json()) == 1


def test_memories_api_rejects_unknown_session_memory_and_empty_text(client) -> None:
    session_id = client.post("/api/sessions", json={"table": "sales"}).json()["session_id"]
    url = f"/api/sessions/{session_id}/memories"
    assert client.get("/api/sessions/nope/memories").status_code == 404
    assert client.post("/api/sessions/nope/memories", json={"text": "x"}).status_code == 404
    assert client.patch(f"{url}/nope", json={"enabled": False}).status_code == 404
    assert client.post(url, json={"text": "   "}).status_code == 422
    assert client.patch(f"{url}/nope", json={}).status_code == 422


def session_with_result(client: TestClient, db_sql: str) -> tuple[str, str]:
    """Сессия, в ходе которой шаг построил temp_result_v7."""
    session_id = client.post("/api/sessions", json={"table": "sales"}).json()["session_id"]
    app.dependency_overrides[get_db]().execute(db_sql)
    memory = SessionMemory(session_id)
    memory.start_turn("Выручка по дням")
    memory.record_step(ResearchStep(step_id=0, tool="aggregate"), "temp_result_v7")
    memory.finish_turn("ok")
    return session_id, "temp_result_v7"


def test_step_result_is_read_by_pages(client) -> None:
    session_id, table = session_with_result(
        client,
        "CREATE TABLE temp_result_v7 AS SELECT i AS day, i * 10 AS revenue FROM range(5) t(i)",
    )
    response = client.get(f"/api/sessions/{session_id}/results/{table}?offset=3&limit=10")
    assert response.status_code == 200
    assert response.json() == {
        "columns": [{"name": "day", "type": "BIGINT"}, {"name": "revenue", "type": "BIGINT"}],
        "rows": [{"day": 3, "revenue": 30}, {"day": 4, "revenue": 40}],
        "total": 5,
    }


def test_step_result_of_other_session_is_404(client) -> None:
    _, table = session_with_result(client, "CREATE TABLE temp_result_v7 AS SELECT 1 AS a")
    other = client.post("/api/sessions", json={"table": "sales"}).json()["session_id"]
    assert client.get(f"/api/sessions/{other}/results/{table}").status_code == 404
    assert client.get(f"/api/sessions/nope/results/{table}").status_code == 404


def test_dropped_step_result_is_404(client) -> None:
    session_id, table = session_with_result(client, "SELECT 1")
    response = client.get(f"/api/sessions/{session_id}/results/{table}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Результат больше недоступен."


def test_unreadable_step_result_is_404(client, monkeypatch: pytest.MonkeyPatch) -> None:
    import duckdb

    session_id, table = session_with_result(client, "CREATE TABLE temp_result_v7 AS SELECT 1 AS a")

    def broken(*_: object) -> None:
        raise duckdb.IOException("файл базы недоступен")

    monkeypatch.setattr(DuckDBManager, "result_shape", broken)
    assert client.get(f"/api/sessions/{session_id}/results/{table}").status_code == 404


def test_step_result_name_must_be_a_step_result(client) -> None:
    session_id = client.post("/api/sessions", json={"table": "sales"}).json()["session_id"]
    assert client.get(f"/api/sessions/{session_id}/results/sales").status_code == 422
    assert (
        client.get(f"/api/sessions/{session_id}/results/temp_result_v1?limit=0").status_code == 422
    )
