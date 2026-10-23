"""Contract-тесты: ChatEvent схема."""

import pytest

from app.api.schemas.chat import ChatEvent, DashboardPayload
from app.core.dashboards.models import Dashboard, DashboardTile
from app.core.viz.vega_spec_builder import build_vega_spec


@pytest.mark.parametrize("event_type", ["text", "chart", "dashboard", "error", "done"])
def test_chat_event_all_types(event_type):
    event = ChatEvent(type=event_type, data={})
    assert event.type == event_type
    assert event.data == {}


def test_chat_event_with_data():
    event = ChatEvent(type="chart", data={"spec": {"mark": "bar"}, "caption": "Тест"})
    assert event.type == "chart"
    assert event.data["spec"]["mark"] == "bar"
    assert event.data["caption"] == "Тест"


def test_chat_event_serialization_compact():
    event = ChatEvent(type="text", data={"headline": "Тест"})
    json_str = event.model_dump_json()
    assert '"type":"text"' in json_str
    assert '"headline":"Тест"' in json_str


def test_dashboard_event_matches_payload_without_snapshot():
    records = [{"total": 1250.5}]
    tile = DashboardTile(
        title="Выручка",
        spec=build_vega_spec("kpi", "temp_result_v3", data=records, title="Выручка"),
        source_step_id=3,
        result_table="temp_result_v3",
        is_kpi=True,
        layout={"x": 0, "y": 0, "w": 3, "h": 2},
        snapshot_data=records,
        snapshot_at=1.0,
    )
    dashboard = Dashboard(session_id="s1", title="Продажи", tiles=[tile])
    event = ChatEvent(
        type="dashboard", data=DashboardPayload.from_dashboard(dashboard).model_dump()
    )
    payload = DashboardPayload.model_validate(event.model_dump()["data"])
    assert payload.dashboard_id == dashboard.id
    assert payload.title == "Продажи"
    sent = payload.tiles[0]
    assert sent.id == tile.id
    assert sent.snapshot_data is None
    assert sent.snapshot_at == 1.0
    assert sent.spec["data"]["values"] == records
    assert sent.spec["usermeta"]["chart_type"] == "kpi"
    assert sent.is_kpi is True
    assert tile.snapshot_data == records


def test_dashboard_payload_rejects_tiles_without_title():
    with pytest.raises(ValueError):
        DashboardPayload(dashboard_id="d1", title="Пусто", tiles=[{"spec": {}}])
