from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.config import settings
from app.core.dashboards.models import Dashboard
from app.core.dashboards.recalculate import recalculate
from app.core.dashboards.store import DashboardStore
from app.core.data.duckdb_manager import DuckDBManager
from app.core.viz.vega_spec_builder import build_vega_spec
from app.main import app

RESULT_TABLE = "temp_result_v1"


@pytest.fixture
def db() -> DuckDBManager:
    manager = DuckDBManager(":memory:")
    manager.execute(
        f"CREATE TABLE {RESULT_TABLE} AS SELECT * FROM (VALUES ('msk', 10), ('spb', 5)) "
        "AS rows(region, revenue)"
    )
    yield manager
    manager.close()


@pytest.fixture
def client(db: DuckDBManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "sessions.sqlite"))
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def chart_tile(db: DuckDBManager) -> dict:
    records = db.to_records(RESULT_TABLE)
    spec = build_vega_spec(
        "bar",
        result_table=RESULT_TABLE,
        data=records,
        titles={"region": "Регион", "revenue": "Выручка"},
        title="Выручка по регионам",
    )
    return {
        "title": "Выручка по регионам",
        "spec": spec,
        "result_table": RESULT_TABLE,
        "snapshot_data": records,
        "snapshot_at": 1.0,
    }


def create(client: TestClient, session_id: str = "s1", tiles: list[dict] | None = None) -> dict:
    response = client.post(
        "/api/dashboards",
        json={"session_id": session_id, "title": "Продажи", "tiles": tiles or []},
    )
    assert response.status_code == 201
    return response.json()


def test_create_and_get(client: TestClient, db: DuckDBManager) -> None:
    created = create(client, tiles=[chart_tile(db)])
    assert created["title"] == "Продажи"
    assert created["template_id"] is None
    tile = created["tiles"][0]
    assert tile["id"]
    assert tile["stale"] is False
    assert tile["layout"] == {"x": 0, "y": 0, "w": 6, "h": 4}
    response = client.get(f"/api/dashboards/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


def test_create_requires_title(client: TestClient) -> None:
    response = client.post("/api/dashboards", json={"session_id": "s1"})
    assert response.status_code == 422


def test_list_with_and_without_session(client: TestClient) -> None:
    first = create(client, "s1")
    second = create(client, "s2")
    client.put(f"/api/dashboards/{first['id']}", json={"title": "Свежий"})
    everything = client.get("/api/dashboards").json()
    assert [summary["id"] for summary in everything] == [first["id"], second["id"]]
    assert set(everything[0]) == {
        "id",
        "session_id",
        "title",
        "tiles_count",
        "created_at",
        "updated_at",
    }
    only_second = client.get("/api/dashboards", params={"session_id": "s2"}).json()
    assert [summary["id"] for summary in only_second] == [second["id"]]


def test_update_title_and_tiles(client: TestClient, db: DuckDBManager) -> None:
    created = create(client, tiles=[chart_tile(db)])
    response = client.put(
        f"/api/dashboards/{created['id']}",
        json={"title": "Новое", "tiles": [{"title": "Плитка", "spec": {"mark": "text"}}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Новое"
    assert [tile["title"] for tile in body["tiles"]] == ["Плитка"]
    assert body["updated_at"] >= created["updated_at"]


def test_update_keeps_tiles_when_only_title_changes(client: TestClient, db: DuckDBManager) -> None:
    created = create(client, tiles=[chart_tile(db)])
    body = client.put(f"/api/dashboards/{created['id']}", json={"title": "Новое"}).json()
    assert [tile["id"] for tile in body["tiles"]] == [created["tiles"][0]["id"]]


def test_delete(client: TestClient) -> None:
    created = create(client)
    response = client.delete(f"/api/dashboards/{created['id']}")
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/api/dashboards/{created['id']}").status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/dashboards/missing", None),
        ("put", "/api/dashboards/missing", {"title": "Нет"}),
        ("delete", "/api/dashboards/missing", None),
        ("post", "/api/dashboards/missing/recalculate", None),
    ],
)
def test_unknown_dashboard_is_404(
    client: TestClient, method: str, path: str, body: dict | None
) -> None:
    response = client.request(method, path, json=body)
    assert response.status_code == 404
    assert response.json() == {"detail": "Дашборд не найден"}


def test_recalculate_reads_new_data(client: TestClient, db: DuckDBManager) -> None:
    created = create(client, tiles=[chart_tile(db), {"title": "Текст", "spec": {"mark": "text"}}])
    db.execute(f"UPDATE {RESULT_TABLE} SET revenue = revenue * 10")
    response = client.post(f"/api/dashboards/{created['id']}/recalculate")
    assert response.status_code == 200
    chart, text = response.json()["tiles"]
    assert chart["stale"] is False
    assert chart["snapshot_data"] == [
        {"region": "msk", "revenue": 100},
        {"region": "spb", "revenue": 50},
    ]
    assert chart["snapshot_at"] > 1.0
    assert chart["spec"]["data"]["values"] == chart["snapshot_data"]
    assert chart["spec"]["title"] == "Выручка по регионам"
    assert chart["spec"]["usermeta"]["result_table"] == RESULT_TABLE
    assert {column["title"] for column in chart["spec"]["usermeta"]["columns"]} == {
        "Регион",
        "Выручка",
    }
    assert text["spec"] == {"mark": "text"}
    assert text["snapshot_data"] is None
    saved = client.get(f"/api/dashboards/{created['id']}").json()
    assert saved["tiles"][0]["snapshot_data"] == chart["snapshot_data"]
    assert saved["updated_at"] >= created["updated_at"]


def test_recalculate_marks_dropped_table_stale(client: TestClient, db: DuckDBManager) -> None:
    created = create(client, tiles=[chart_tile(db)])
    db.execute(f"DROP TABLE {RESULT_TABLE}")
    response = client.post(f"/api/dashboards/{created['id']}/recalculate")
    assert response.status_code == 200
    tile = response.json()["tiles"][0]
    assert tile["stale"] is True
    assert tile["snapshot_data"] == created["tiles"][0]["snapshot_data"]
    assert tile["snapshot_at"] == 1.0
    assert tile["spec"] == created["tiles"][0]["spec"]


class VanishingStore(DashboardStore):
    def update(self, dashboard_id: str, title=None, tiles=None) -> None:
        self.delete(dashboard_id)


def test_recalculate_dashboard_deleted_meanwhile(db: DuckDBManager, tmp_path: Path) -> None:
    store = VanishingStore(path=str(tmp_path / "sessions.sqlite"))
    dashboard = Dashboard(session_id="s1", title="Продажи")
    store.create(dashboard)
    assert recalculate(dashboard.id, db, store) is None
    assert store.get(dashboard.id) is None


def test_recalculate_returns_to_the_requested_chart_type(
    client: TestClient, db: DuckDBManager
) -> None:
    db.execute("CREATE TABLE temp_result_v2 AS SELECT 5 AS x")
    spec = build_vega_spec(
        "scatter", result_table="temp_result_v2", data=db.to_records("temp_result_v2")
    )
    assert spec["usermeta"]["chart_type"] == "histogram"
    tile = {"title": "Связь", "spec": spec, "result_table": "temp_result_v2"}
    created = create(client, tiles=[tile])
    db.execute("CREATE OR REPLACE TABLE temp_result_v2 AS SELECT 5 AS x, 7 AS y")
    rebuilt = client.post(f"/api/dashboards/{created['id']}/recalculate").json()["tiles"][0]["spec"]
    assert rebuilt["usermeta"]["chart_type"] == "scatter"
    assert rebuilt["usermeta"]["requested_type"] == "scatter"


def test_recalculate_keeps_a_foreign_spec_and_refreshes_its_data(
    client: TestClient, db: DuckDBManager
) -> None:
    spec = {"mark": "arc", "encoding": {"theta": {"field": "revenue"}}, "data": {"values": []}}
    created = create(client, tiles=[{"title": "Доли", "spec": spec, "result_table": RESULT_TABLE}])
    rebuilt = client.post(f"/api/dashboards/{created['id']}/recalculate").json()["tiles"][0]
    assert rebuilt["spec"]["mark"] == "arc"
    assert rebuilt["spec"]["encoding"] == spec["encoding"]
    assert rebuilt["spec"]["data"]["values"] == [
        {"region": "msk", "revenue": 10},
        {"region": "spb", "revenue": 5},
    ]
    assert rebuilt["snapshot_data"] == rebuilt["spec"]["data"]["values"]


def test_recalculate_keeps_a_kpi_tile(client: TestClient, db: DuckDBManager) -> None:
    db.execute("CREATE TABLE temp_result_v3 AS SELECT 15 AS total")
    spec = build_vega_spec(
        "kpi", result_table="temp_result_v3", data=db.to_records("temp_result_v3")
    )
    tile = {"title": "Итого", "spec": spec, "result_table": "temp_result_v3", "is_kpi": True}
    created = create(client, tiles=[tile])
    db.execute("CREATE OR REPLACE TABLE temp_result_v3 AS SELECT 20 AS total")
    rebuilt = client.post(f"/api/dashboards/{created['id']}/recalculate").json()["tiles"][0]
    assert rebuilt["spec"]["usermeta"]["chart_type"] == "kpi"
    assert rebuilt["spec"]["data"]["values"] == [{"total": 20}]
    assert rebuilt["is_kpi"] is True
