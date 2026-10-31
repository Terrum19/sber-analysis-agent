import datetime as dt
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.dashboards.generator import DashboardGenerator
from app.core.dashboards.models import Dashboard, DashboardTile
from app.core.dashboards.store import DashboardStore
from app.core.viz.vega_spec_builder import SPEC_ROW_LIMIT


@pytest.fixture
def store_path(tmp_path: Path) -> str:
    return str(tmp_path / "store" / "sessions.sqlite")


@pytest.fixture
def store(store_path: str) -> DashboardStore:
    return DashboardStore(path=store_path)


def make_dashboard(
    session_id: str = "s1", title: str = "Продажи", tiles_count: int = 2
) -> Dashboard:
    tiles = [
        DashboardTile(
            title=f"Плитка {index}",
            spec={"mark": "bar", "index": index},
            source_step_id=index,
            result_table=f"temp_result_v{index}",
            insight=f"Вывод {index}",
            is_kpi=index == 0,
            layout={"x": index, "y": 0, "w": 6, "h": 4},
            snapshot_data=[{"region": "msk", "value": index}],
            snapshot_at=100.0 + index,
        )
        for index in range(tiles_count)
    ]
    return Dashboard(session_id=session_id, title=title, template_id="kpi", tiles=tiles)


def tile_rows(store_path: str) -> int:
    connection = sqlite3.connect(store_path)
    try:
        return connection.execute("SELECT COUNT(*) FROM dashboard_tiles").fetchone()[0]
    finally:
        connection.close()


def test_create_and_get_round_trip(store: DashboardStore) -> None:
    dashboard = make_dashboard()
    assert store.create(dashboard) == dashboard.id
    assert store.get(dashboard.id) == dashboard


def test_get_unknown_returns_none(store: DashboardStore) -> None:
    assert store.get("missing") is None


def test_tiles_keep_their_order(store: DashboardStore) -> None:
    dashboard = make_dashboard(tiles_count=5)
    store.create(dashboard)
    saved = store.get(dashboard.id)
    assert [tile.id for tile in saved.tiles] == [tile.id for tile in dashboard.tiles]


def test_list_filters_by_session_and_counts_tiles(store: DashboardStore) -> None:
    first = make_dashboard("s1", tiles_count=2)
    second = make_dashboard("s2", tiles_count=3)
    store.create(first)
    store.create(second)
    summaries = store.list("s1")
    assert [summary.id for summary in summaries] == [first.id]
    assert summaries[0].tiles_count == 2
    assert {summary.id for summary in store.list()} == {first.id, second.id}


def test_list_is_sorted_by_update_time(store: DashboardStore) -> None:
    older = make_dashboard()
    newer = make_dashboard()
    store.create(older)
    store.create(newer)
    store.update(older.id, title="Обновлён")
    assert [summary.id for summary in store.list("s1")] == [older.id, newer.id]


def test_update_title_and_replace_tiles(store: DashboardStore, store_path: str) -> None:
    dashboard = make_dashboard(tiles_count=3)
    store.create(dashboard)
    replacement = [DashboardTile(title="Новая", spec={"mark": "line"})]
    updated = store.update(dashboard.id, title="Новый заголовок", tiles=replacement)
    assert updated.title == "Новый заголовок"
    assert [tile.id for tile in updated.tiles] == [replacement[0].id]
    assert updated.updated_at >= dashboard.updated_at
    assert tile_rows(store_path) == 1


def test_update_unknown_returns_none(store: DashboardStore) -> None:
    assert store.update("missing", title="Нет") is None


def test_delete_cascades_to_tiles(store: DashboardStore, store_path: str) -> None:
    kept = make_dashboard(tiles_count=1)
    removed = make_dashboard(tiles_count=3)
    store.create(kept)
    store.create(removed)
    assert store.delete(removed.id) is True
    assert store.get(removed.id) is None
    assert tile_rows(store_path) == 1
    assert store.delete(removed.id) is False


def test_delete_by_session(store: DashboardStore, store_path: str) -> None:
    d1 = make_dashboard(session_id="sess_a", tiles_count=2)
    d2 = make_dashboard(session_id="sess_a", tiles_count=3)
    d3 = make_dashboard(session_id="sess_b", tiles_count=1)
    store.create(d1)
    store.create(d2)
    store.create(d3)
    assert tile_rows(store_path) == 6

    deleted_ids = store.delete_by_session("sess_a")
    assert set(deleted_ids) == {d1.id, d2.id}
    assert store.get(d1.id) is None
    assert store.get(d2.id) is None
    assert store.get(d3.id) is not None
    assert tile_rows(store_path) == 1


def test_delete_by_sessions(store: DashboardStore, store_path: str) -> None:
    assert store.delete_by_sessions([]) == []
    assert store.delete_by_sessions(["non_existent"]) == []
    d1 = make_dashboard(session_id="sess_1", tiles_count=2)
    d2 = make_dashboard(session_id="sess_2", tiles_count=2)
    d3 = make_dashboard(session_id="sess_3", tiles_count=1)
    store.create(d1)
    store.create(d2)
    store.create(d3)

    assert set(store.delete_by_sessions(["sess_1", "sess_2"])) == {d1.id, d2.id}
    assert store.get(d3.id) is not None
    assert tile_rows(store_path) == 1


def test_prune_orphaned(store: DashboardStore, store_path: str) -> None:
    # До создания таблицы session_state prune_orphaned возвращает пустой список
    assert store.prune_orphaned() == []

    d1 = make_dashboard(session_id="existing_sess", tiles_count=1)
    d2 = make_dashboard(session_id="dead_sess", tiles_count=2)
    store.create(d1)
    store.create(d2)

    conn = sqlite3.connect(store_path)
    try:
        conn.execute("CREATE TABLE session_state (session_id TEXT PRIMARY KEY, state TEXT)")
        conn.execute("INSERT INTO session_state VALUES ('existing_sess', '{}')")
        conn.commit()
    finally:
        conn.close()

    pruned = store.prune_orphaned()
    assert pruned == [d2.id]
    assert store.get(d1.id) is not None
    assert store.get(d2.id) is None
    assert tile_rows(store_path) == 1


def test_update_snapshot(store: DashboardStore) -> None:
    dashboard = make_dashboard(tiles_count=1)
    store.create(dashboard)
    tile_id = dashboard.tiles[0].id
    records = [{"amount": Decimal("1.50"), "day": dt.date(2026, 10, 1)}]
    assert store.update_snapshot(tile_id, records) is True
    tile = store.get(dashboard.id).tiles[0]
    assert tile.snapshot_data == [{"amount": 1.5, "day": "2026-10-01"}]
    assert tile.snapshot_at > dashboard.tiles[0].snapshot_at
    assert store.update_snapshot("missing", records) is False


def test_update_snapshot_nested_dict(store: DashboardStore) -> None:
    dashboard = make_dashboard(tiles_count=1)
    store.create(dashboard)
    tile_id = dashboard.tiles[0].id
    data = {
        "value": Decimal("42.50"),
        "records": [{"sub": dt.date(2026, 5, 1), "cnt": 10}],
        "meta": {"label": "Тест"},
    }
    assert store.update_snapshot(tile_id, data) is True
    tile = store.get(dashboard.id).tiles[0]
    assert tile.snapshot_data == {
        "value": 42.5,
        "records": [{"sub": "2026-05-01", "cnt": 10}],
        "meta": {"label": "Тест"},
    }


def test_data_survives_a_new_store_instance(store_path: str) -> None:
    dashboard = make_dashboard()
    DashboardStore(path=store_path).create(dashboard)
    assert DashboardStore(path=store_path).get(dashboard.id) == dashboard


def test_store_shares_the_session_database(tmp_path: Path, monkeypatch) -> None:
    from app.config import settings

    path = tmp_path / "shared" / "sessions.sqlite"
    monkeypatch.setattr(settings, "sqlite_path", str(path))
    dashboard = make_dashboard()
    DashboardStore().create(dashboard)
    assert path.exists()
    assert DashboardStore(path=str(path)).get(dashboard.id) == dashboard


class RecordingDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def to_records(self, table: str, limit: int = 100) -> list[dict]:
        self.calls.append((table, limit))
        return [{"region": "msk", "revenue": Decimal("10.5"), "day": dt.date(2026, 10, 1)}]


async def test_generate_writes_snapshots(store: DashboardStore) -> None:
    recorder = RecordingDB()
    tiles = [
        DashboardTile(title="С таблицей", spec={"mark": "bar"}, result_table="temp_result_v3"),
        DashboardTile(title="Без таблицы", spec={"mark": "text"}),
    ]
    dashboard = await DashboardGenerator(db=recorder, store=store).generate(
        "s1", "Сводка", tiles, template_id="overview"
    )
    assert recorder.calls == [("temp_result_v3", SPEC_ROW_LIMIT)]
    saved = store.get(dashboard.id)
    assert saved.title == "Сводка"
    assert saved.template_id == "overview"
    assert saved.tiles[0].snapshot_data == [{"region": "msk", "revenue": 10.5, "day": "2026-10-01"}]
    assert saved.tiles[0].snapshot_at is not None
    assert saved.tiles[1].snapshot_data is None
    assert saved.tiles[1].snapshot_at is None


def test_tile_origin_round_trip(store: DashboardStore) -> None:
    dashboard = make_dashboard(tiles_count=1)
    dashboard.tiles[0] = dashboard.tiles[0].model_copy(
        update={
            "origin_tile_id": "orig",
            "source_session_id": "s9",
            "source_turn_id": "t9",
            "hidden": True,
        }
    )
    store.create(dashboard)
    assert store.get(dashboard.id) == dashboard


def test_old_store_gets_new_tile_columns(store_path: str) -> None:
    Path(store_path).parent.mkdir(parents=True)
    connection = sqlite3.connect(store_path)
    connection.executescript(
        "CREATE TABLE dashboards (id TEXT PRIMARY KEY, session_id TEXT, title TEXT,"
        " template_id TEXT, created_at REAL, updated_at REAL);"
        "CREATE TABLE dashboard_tiles (id TEXT PRIMARY KEY, dashboard_id TEXT,"
        " order_index INTEGER, title TEXT, source_step_id INTEGER, result_table TEXT,"
        " spec_json TEXT, insight TEXT, is_kpi INTEGER,"
        " layout_json TEXT, snapshot_json TEXT, snapshot_at REAL);"
        "INSERT INTO dashboards VALUES ('old', 's1', 'Старый', NULL, 1.0, 1.0);"
        "INSERT INTO dashboard_tiles VALUES ('t1', 'old', 0, 'Плитка', NULL, NULL,"
        ' \'{}\', NULL, 0, \'{"x": 0, "y": 0, "w": 6, "h": 4}\', NULL, NULL);'
    )
    connection.commit()
    connection.close()
    tile = DashboardStore(path=store_path).get("old").tiles[0]
    assert tile.origin_tile_id is None
    assert tile.source_session_id is None
    assert tile.hidden is False


def test_list_gives_origin_keys_of_visible_tiles(store: DashboardStore) -> None:
    dashboard = make_dashboard(tiles_count=3)
    dashboard.tiles[1].origin_tile_id = "orig"
    dashboard.tiles[2].hidden = True
    store.create(dashboard)
    summary = store.list()[0]
    assert summary.origin_keys == [dashboard.tiles[0].id, "orig"]
    assert summary.tiles_count == 2


async def test_generator_marks_session_and_turn(store: DashboardStore) -> None:
    tiles = make_dashboard(tiles_count=2).tiles
    dashboard = await DashboardGenerator(store=store).generate(
        "s7", "Продажи", tiles, turn_id="turn7"
    )
    saved = store.get(dashboard.id)
    assert {(tile.source_session_id, tile.source_turn_id) for tile in saved.tiles} == {
        ("s7", "turn7")
    }
