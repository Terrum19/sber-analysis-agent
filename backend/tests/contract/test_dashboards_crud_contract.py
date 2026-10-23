"""Contract-тесты: полный жизненный цикл CRUD дашбордов и пересчёт снимка."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_dashboards_full_crud_and_recalculate_lifecycle(client: TestClient) -> None:
    # 1. GET /api/dashboards без session_id возвращает список со статусом 200
    res = client.get("/api/dashboards")
    assert res.status_code == 200
    assert isinstance(res.json(), list)

    # 2. POST /api/dashboards создает дашборд со статусом 201 и возвращает id
    create_payload = {
        "session_id": "session-contract-1",
        "title": "Финансовый обзор",
        "tiles": [
            {
                "title": "Выручка по месяцам",
                "spec": {"mark": "bar"},
                "is_kpi": False,
                "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
                "snapshot_data": [{"month": "2026-01", "revenue": 1000}],
            },
            {
                "title": "Чистая прибыль",
                "spec": {},
                "is_kpi": True,
                "layout": {"x": 6, "y": 0, "w": 6, "h": 4},
                "snapshot_data": {"value": "25.4M ₽", "label": "Прибыль"},
            },
        ],
    }
    create_res = client.post("/api/dashboards", json=create_payload)
    assert create_res.status_code == 201
    created_data = create_res.json()
    assert "id" in created_data
    dashboard_id = created_data["id"]
    assert dashboard_id
    assert created_data["title"] == "Финансовый обзор"
    assert len(created_data["tiles"]) == 2

    # 3. GET /api/dashboards с фильтром по session_id и без фильтра
    list_all = client.get("/api/dashboards").json()
    assert any(d["id"] == dashboard_id for d in list_all)

    list_session = client.get("/api/dashboards", params={"session_id": "session-contract-1"}).json()
    assert any(d["id"] == dashboard_id for d in list_session)

    list_other = client.get("/api/dashboards", params={"session_id": "unknown-session"}).json()
    assert not any(d["id"] == dashboard_id for d in list_other)

    # 4. GET /api/dashboards/{dashboard_id} возвращает 200 и данные дашборда
    get_res = client.get(f"/api/dashboards/{dashboard_id}")
    assert get_res.status_code == 200
    got_data = get_res.json()
    assert got_data["id"] == dashboard_id
    assert got_data["title"] == "Финансовый обзор"

    # 5. PUT /api/dashboards/{dashboard_id} обновляет заголовок и плитки со статусом 200
    update_payload = {
        "title": "Обновленный финансовый обзор",
        "tiles": [
            {
                "id": got_data["tiles"][0]["id"],
                "title": "Выручка по месяцам (обновлено)",
                "spec": {"mark": "line"},
                "layout": {"x": 0, "y": 0, "w": 12, "h": 5},
            }
        ],
    }
    update_res = client.put(f"/api/dashboards/{dashboard_id}", json=update_payload)
    assert update_res.status_code == 200
    updated_data = update_res.json()
    assert updated_data["id"] == dashboard_id
    assert updated_data["title"] == "Обновленный финансовый обзор"
    assert len(updated_data["tiles"]) == 1
    assert updated_data["tiles"][0]["title"] == "Выручка по месяцам (обновлено)"

    # 6. POST /api/dashboards/{dashboard_id}/recalculate пересчитывает снимок со статусом 200
    recalc_res = client.post(f"/api/dashboards/{dashboard_id}/recalculate")
    assert recalc_res.status_code == 200
    recalc_data = recalc_res.json()
    assert recalc_data["id"] == dashboard_id
    assert "updated_at" in recalc_data

    # 7. DELETE /api/dashboards/{dashboard_id} удаляет дашборд со статусом 200
    del_res = client.delete(f"/api/dashboards/{dashboard_id}")
    assert del_res.status_code == 200
    assert del_res.json().get("ok") is True

    # Проверяем, что дашборд удалён
    assert client.get(f"/api/dashboards/{dashboard_id}").status_code == 404
    assert not any(d["id"] == dashboard_id for d in client.get("/api/dashboards").json())


def test_dashboards_crud_not_found_handling(client: TestClient) -> None:
    non_existent_id = "non-existent-id-999"

    assert client.get(f"/api/dashboards/{non_existent_id}").status_code == 404
    assert (
        client.put(f"/api/dashboards/{non_existent_id}", json={"title": "Test"}).status_code == 404
    )
    assert client.delete(f"/api/dashboards/{non_existent_id}").status_code == 404
    assert client.post(f"/api/dashboards/{non_existent_id}/recalculate").status_code == 404


def test_dashboards_recalculate_with_duckdb(client: TestClient) -> None:
    db = getattr(app.state, "db", None)
    assert db is not None

    table_name = "test_recalc_sales_v1"
    db.execute(f"CREATE TABLE IF NOT EXISTS {table_name} (category VARCHAR, revenue INTEGER)")
    db.execute(f"DELETE FROM {table_name}")
    db.execute(f"INSERT INTO {table_name} VALUES ('Electronics', 5000)")

    create_payload = {
        "session_id": "session-recalc-duckdb",
        "title": "Дашборд с пересчетом",
        "tiles": [
            {
                "title": "Выручка по категориям",
                "result_table": table_name,
                "spec": {
                    "mark": "bar",
                    "data": {"values": []},
                    "usermeta": {
                        "chart_type": "bar",
                        "columns": [
                            {"field": "category", "title": "Категория", "type": "nominal"},
                            {"field": "revenue", "title": "Выручка", "type": "quantitative"},
                        ],
                    },
                },
                "is_kpi": False,
                "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
                "snapshot_data": None,
            }
        ],
    }

    create_res = client.post("/api/dashboards", json=create_payload)
    assert create_res.status_code == 201
    dash_id = create_res.json()["id"]

    # 1. Запуск recalculate
    recalc_res = client.post(f"/api/dashboards/{dash_id}/recalculate")
    assert recalc_res.status_code == 200
    recalc_data = recalc_res.json()

    tile = recalc_data["tiles"][0]
    assert tile["snapshot_data"] == [{"category": "Electronics", "revenue": 5000}]
    assert tile["snapshot_at"] is not None
    assert tile["spec"]["data"]["values"] == [{"category": "Electronics", "revenue": 5000}]

    # 2. Добавляем новые данные в DuckDB и снова пересчитываем
    db.execute(f"INSERT INTO {table_name} VALUES ('Books', 1500)")
    recalc_res2 = client.post(f"/api/dashboards/{dash_id}/recalculate")
    assert recalc_res2.status_code == 200
    recalc_data2 = recalc_res2.json()

    tile2 = recalc_data2["tiles"][0]
    assert len(tile2["snapshot_data"]) == 2
    assert tile2["snapshot_data"] == [
        {"category": "Electronics", "revenue": 5000},
        {"category": "Books", "revenue": 1500},
    ]
    assert tile2["spec"]["data"]["values"] == tile2["snapshot_data"]

    # Проверяем, что изменения сохранены в SQLite
    get_res = client.get(f"/api/dashboards/{dash_id}").json()
    assert len(get_res["tiles"][0]["snapshot_data"]) == 2
    assert get_res["tiles"][0]["spec"]["data"]["values"] == tile2["snapshot_data"]

    # Чистим
    client.delete(f"/api/dashboards/{dash_id}")
    db.execute(f"DROP TABLE IF EXISTS {table_name}")


def test_dashboards_nested_snapshot_data_preservation(client: TestClient) -> None:
    create_payload = {
        "session_id": "session-nested-kpi",
        "title": "KPI с вложенной структурой",
        "tiles": [
            {
                "title": "Вложенный KPI",
                "spec": {},
                "is_kpi": True,
                "snapshot_data": {
                    "records": [{"month": "2026-01", "val": 100}],
                    "meta": {"summary": "Рост", "delta": 15.5},
                },
            }
        ],
    }

    create_res = client.post("/api/dashboards", json=create_payload)
    assert create_res.status_code == 201
    dash_id = create_res.json()["id"]

    get_res = client.get(f"/api/dashboards/{dash_id}").json()
    tile = get_res["tiles"][0]
    # Убеждаемся, что snapshot_data осталась структурой, а не превратилась в строку "{...}"
    assert isinstance(tile["snapshot_data"], dict)
    assert isinstance(tile["snapshot_data"]["records"], list)
    assert tile["snapshot_data"]["records"] == [{"month": "2026-01", "val": 100}]
    assert isinstance(tile["snapshot_data"]["meta"], dict)
    assert tile["snapshot_data"]["meta"]["delta"] == 15.5

    client.delete(f"/api/dashboards/{dash_id}")


def test_dashboards_default_title_and_empty_tiles(client: TestClient) -> None:
    # 1. Создание без title и tiles
    res = client.post("/api/dashboards", json={"session_id": "sess-default"})
    assert res.status_code == 201
    data = res.json()
    assert data["title"] == "Новый дашборд"
    assert data["tiles"] == []

    # 2. Создание с пустым title
    res2 = client.post("/api/dashboards", json={"session_id": "sess-default", "title": "   "})
    assert res2.status_code == 201
    assert res2.json()["title"] == "Новый дашборд"

    client.delete(f"/api/dashboards/{data['id']}")
    client.delete(f"/api/dashboards/{res2.json()['id']}")


def test_dashboards_partial_updates(client: TestClient) -> None:
    create_res = client.post(
        "/api/dashboards",
        json={
            "session_id": "sess-partial",
            "title": "Исходный",
            "tiles": [{"title": "Плитка 1", "spec": {}}],
        },
    )
    dash_id = create_res.json()["id"]

    # 1. Обновляем только title: плитки сохраняются
    update_res = client.put(f"/api/dashboards/{dash_id}", json={"title": "Новое название"})
    assert update_res.status_code == 200
    assert update_res.json()["title"] == "Новое название"
    assert len(update_res.json()["tiles"]) == 1
    assert update_res.json()["tiles"][0]["title"] == "Плитка 1"

    # 2. Обновляем только плитки: название сохраняется
    update_res2 = client.put(
        f"/api/dashboards/{dash_id}",
        json={"tiles": [{"title": "Замененная плитка", "spec": {"mark": "line"}}]},
    )
    assert update_res2.status_code == 200
    assert update_res2.json()["title"] == "Новое название"
    assert len(update_res2.json()["tiles"]) == 1
    assert update_res2.json()["tiles"][0]["title"] == "Замененная плитка"

    client.delete(f"/api/dashboards/{dash_id}")
