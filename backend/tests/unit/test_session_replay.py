"""Рецепт сессии (#250): пересчёт графиков и плиток у получателя без LLM."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.agent.replay import Replayer
from app.core.agent.step_executor import StepExecutor, tool_kind
from app.core.dashboards.models import Dashboard, DashboardTile
from app.core.dashboards.store import DashboardStore
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.client import LLMClient
from app.core.memory import session_bundle
from app.core.memory.datasource_store import DatasourceRecord, DatasourceStore
from app.core.memory.session_memory import SessionMemory
from app.core.viz.vega_spec_builder import SPEC_ROW_LIMIT, build_vega_spec
from app.main import app
from app.models.research_step import ResearchStep

SALES = (
    "SELECT * FROM (VALUES ('msk', 10.5, 1), ('spb', 7.0, 2), ('msk', 4.5, 3), ('kzn', 2.0, 4))"
    " AS t(region, revenue, id)"
)
BY_REGION = ResearchStep(
    step_id=1,
    tool="group_by",
    arguments={"by": "region", "column": "revenue", "function": "SUM"},
    purpose="Выручка по регионам",
)
TOP = ResearchStep(
    step_id=2, tool="sort_limit", arguments={"order_by": "sum_revenue", "limit": 2}, purpose="Топ"
)


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """Пересчёт не должен звать LLM: любой вызов роняет тест."""

    async def forbidden(*args, **kwargs):
        raise AssertionError("LLM вызвана при пересчёте")

    monkeypatch.setattr(LLMClient, "complete", forbidden)


@pytest.fixture
def machines():
    """БД автора и получателя; SQLite сессий, дашбордов и источников у них общий."""
    author, recipient = DuckDBManager(":memory:"), DuckDBManager(":memory:")
    yield author, recipient
    author.close()
    recipient.close()


def csv_table(db: DuckDBManager, name: str, select: str = SALES) -> None:
    db.execute(f"CREATE TABLE {name} AS {select}")
    db.execute(f"COMMENT ON TABLE {name} IS 'csv:csv_abc123/sales.csv'")


def run_turn(
    memory: SessionMemory, db: DuckDBManager, question: str, steps: list, chart: str = "bar"
) -> tuple[dict, dict[int, str]]:
    """Ход автора, как его записывает оркестратор: шаги, график, итог."""
    memory.start_turn(question)
    executor = StepExecutor(db=db)
    source, tables = memory.table, {}
    current = source
    for step in steps:
        if step.step_id % 100 == 0:
            current = source
        table = asyncio.run(
            executor.execute(step, current, tables, source_table=source, history=memory)
        )
        tables[step.step_id] = table
        memory.record_step(step, table)
        if tool_kind(step) == "transform":
            current = table
    result = tables[steps[-1].step_id]
    spec = build_vega_spec(chart, result, data=db.to_records(result, limit=SPEC_ROW_LIMIT))
    memory.record_chart(chart, result, question)
    memory.finish_turn("ok", "ответ автора", result, db.columns(result))
    return spec, tables


def stripped(spec: dict) -> dict:
    """Спека, как её везёт рецепт: без точек."""
    return {key: value for key, value in spec.items() if key != "data"}


def values(spec: dict) -> list[dict]:
    return spec["data"]["values"]


def share(session_id: str, author: DuckDBManager, recipient: DuckDBManager):
    bundle = json.loads(json.dumps(session_bundle.build(session_id, author)))
    result = session_bundle.load(bundle, recipient)
    return bundle, result, SessionMemory(result.session_id, recipient)


def replay(memory: SessionMemory, db: DuckDBManager, specs: dict[str, dict]):
    return asyncio.run(Replayer(memory, db).run(specs))


def author_with_dashboard(db: DuckDBManager) -> tuple[SessionMemory, dict, dict[int, str]]:
    csv_table(db, "sales")
    memory = SessionMemory("author", db)
    memory.set_meta("Продажи", None)
    memory.set_table("sales")
    spec, tables = run_turn(memory, db, "Топ регионов по выручке", [BY_REGION, TOP])
    tile_spec = build_vega_spec("bar", tables[1], db=db, title="По регионам")
    DashboardStore().create(
        Dashboard(
            session_id="author",
            title="Продажи",
            tiles=[
                DashboardTile(
                    title="По регионам",
                    spec=tile_spec,
                    source_step_id=1,
                    result_table=tables[1],
                    snapshot_data=values(tile_spec),
                )
            ],
        )
    )
    return memory, spec, tables


def test_csv_recipe_replays_charts_and_tiles_on_same_data(machines) -> None:
    author, recipient = machines
    memory, spec, _ = author_with_dashboard(author)
    csv_table(recipient, "sales", "SELECT 'other' AS region, 1.0 AS revenue, 9 AS id")
    csv_table(recipient, "sales_1")

    bundle, result, imported = share("author", author, recipient)
    turn_id = memory.turns[0]["turn_id"]
    out, dashboards = replay(imported, recipient, {turn_id: stripped(spec)})

    assert bundle["turns"][0]["replay"] == {"turn_id": turn_id, "step_id": 2, "overview": False}
    assert bundle["dashboards"][0]["tiles"][0]["replay"]["step_id"] == 1
    assert result.table == "sales_1" and not out.failed
    chart = out.charts[turn_id]
    assert values(chart.spec) == values(spec)
    assert chart.spec["usermeta"]["chart_type"] == "bar"
    assert chart.spec["usermeta"]["result_table"] == chart.result_table
    tile = dashboards[0].tiles[0]
    authors = DashboardStore().get(DashboardStore().list("author")[0].id).tiles[0]
    assert tile.snapshot_data == authors.snapshot_data
    assert values(tile.spec) == values(authors.spec)
    assert recipient.columns(tile.result_table) == ["region", "sum_revenue"]
    saved = SessionMemory(result.session_id).turns[0]
    assert saved["result_table"] == saved["chart"]["result_table"] == chart.result_table


def test_tile_and_chart_on_one_chain_run_the_step_once(machines, monkeypatch) -> None:
    author, recipient = machines
    author_with_dashboard(author)
    csv_table(recipient, "sales")
    calls = []
    execute = StepExecutor.execute

    async def counted(self, step, *args, **kwargs):
        calls.append(step.step_id)
        return await execute(self, step, *args, **kwargs)

    monkeypatch.setattr(StepExecutor, "execute", counted)
    _, _, imported = share("author", author, recipient)
    replay(imported, recipient, {})

    assert calls == [1, 2]


def test_morph_turn_replays_from_the_turn_it_rebuilt(machines) -> None:
    author, recipient = machines
    csv_table(author, "sales")
    csv_table(recipient, "sales")
    memory = SessionMemory("author", author)
    memory.set_table("sales")
    spec, tables = run_turn(memory, author, "Выручка по регионам", [BY_REGION])
    first = memory.turns[0]["turn_id"]
    memory.start_turn("сделай таблицей", reply_to=first)
    memory.record_chart("table", tables[1], "Выручка")
    memory.finish_turn("ok", "График перестроен: table", tables[1])
    morph = build_vega_spec("table", tables[1], db=author)

    bundle, _, imported = share("author", author, recipient)
    second = memory.turns[1]["turn_id"]
    out, _ = replay(imported, recipient, {second: stripped(morph)})

    assert bundle["turns"][1]["replay"]["turn_id"] == first
    assert out.charts[second].spec["usermeta"]["chart_type"] == "table"
    assert sorted(values(out.charts[second].spec), key=str) == sorted(values(spec), key=str)


def test_dashboard_idea_tiles_have_their_own_prev_chain(machines) -> None:
    author, recipient = machines
    csv_table(author, "sales")
    csv_table(recipient, "sales")
    memory = SessionMemory("author", author)
    memory.set_table("sales")
    steps = [
        BY_REGION.model_copy(update={"step_id": 100}),
        TOP.model_copy(update={"step_id": 101}),
        ResearchStep(step_id=200, tool="sort_limit", arguments={"order_by": "id", "limit": 1}),
    ]
    _, tables = run_turn(memory, author, "Дашборд", steps)
    tile_specs = [build_vega_spec("bar", tables[n], db=author) for n in (101, 200)]
    DashboardStore().create(
        Dashboard(
            session_id="author",
            title="Идея",
            tiles=[
                DashboardTile(title=str(n), spec=spec, result_table=tables[n])
                for n, spec in zip((101, 200), tile_specs, strict=True)
            ],
        )
    )

    _, _, imported = share("author", author, recipient)
    _, dashboards = replay(imported, recipient, {})

    assert [tile.snapshot_data for tile in dashboards[0].tiles] == [
        values(spec) for spec in tile_specs
    ]


def test_python_step_is_never_replayed(machines) -> None:
    author, recipient = machines
    csv_table(author, "sales")
    csv_table(recipient, "sales")
    memory = SessionMemory("author", author)
    memory.set_table("sales")
    memory.start_turn("Сложный расчёт")
    memory.record_step(
        ResearchStep(step_id=1, tool="run_python", arguments={"purpose": "x" * 20}), "temp_v1"
    )
    memory.record_chart("bar", "temp_v1", "Расчёт")
    memory.finish_turn("ok", "ответ", "temp_v1")

    bundle = session_bundle.build("author", author)
    assert bundle["turns"][0]["replay"] is None

    turn_id = bundle["turns"][0]["turn_id"]
    bundle["turns"][0]["replay"] = {"turn_id": turn_id, "step_id": 1}
    imported = SessionMemory(session_bundle.load(bundle, recipient).session_id, recipient)
    out, _ = replay(imported, recipient, {})

    assert "Python" in out.failed[turn_id] and not out.charts


def test_overview_turn_is_rebuilt_from_recipient_passport(machines) -> None:
    author, recipient = machines
    csv_table(author, "sales")
    csv_table(recipient, "sales_copy")
    memory = SessionMemory("author", author)
    memory.set_table("sales")
    memory.start_turn("Что передо мной?")
    memory.mark_overview()
    memory.finish_turn("ok", "sales: 4 строки")

    bundle, _, imported = share("author", author, recipient)
    out, _ = replay(imported, recipient, {})

    turn_id = memory.turns[0]["turn_id"]
    assert bundle["turns"][0]["replay"]["overview"] is True
    spec = out.charts[turn_id].spec
    assert spec["usermeta"]["chart_type"] == "table" and len(values(spec)) == 3


def test_failed_step_keeps_other_charts(machines) -> None:
    author, recipient = machines
    csv_table(author, "sales")
    csv_table(recipient, "sales")
    memory = SessionMemory("author", author)
    memory.set_table("sales")
    run_turn(memory, author, "Выручка", [BY_REGION])
    run_turn(memory, author, "Топ", [BY_REGION, TOP])
    bundle = session_bundle.build("author", author)
    bundle["turns"][0]["steps"][0]["arguments"]["column"] = "missing_column"
    imported = SessionMemory(session_bundle.load(bundle, recipient).session_id, recipient)

    out, _ = replay(imported, recipient, {})

    first, second = (turn["turn_id"] for turn in memory.turns)
    assert first in out.failed and "missing_column" in out.failed[first]
    assert second in out.charts


def db_view(db: DuckDBManager, identity: str, view: str, table: str, suffix: str) -> None:
    """Представление ``view`` над таблицей ``table`` подключения <identity>_<суффикс>."""
    alias = f"{identity}_{suffix}"
    db.execute(f"ATTACH IF NOT EXISTS ':memory:' AS {alias}")
    db.execute(f"CREATE TABLE IF NOT EXISTS {alias}.{table} AS {SALES}")
    db.execute(f"CREATE TEMP VIEW {view} AS SELECT * FROM {alias}.main.{table}")


def test_db_table_is_found_by_its_name_in_the_base(machines) -> None:
    author, recipient = machines
    for identity, user in (("pg_0123456789", "analyst"), ("pg_9876543210", "friend")):
        DatasourceStore().save(
            DatasourceRecord(
                identity=identity,
                kind="postgres",
                dsn=f"postgresql://{user}:***@db.local:5432/shop",
                db_schema="public",
            )
        )
    db_view(author, "pg_0123456789", "sales", "sales", "abcdef")
    db_view(recipient, "pg_9876543210", "shop_sales", "sales", "fedcba")
    memory = SessionMemory("author", author)
    memory.set_table("sales")
    spec, _ = run_turn(memory, author, "Выручка", [BY_REGION])

    bundle, result, imported = share("author", author, recipient)
    out, _ = replay(imported, recipient, {memory.turns[0]["turn_id"]: stripped(spec)})

    assert bundle["source"]["source_table"] == "sales"
    assert (result.check.status, result.table) == ("found", "shop_sales")
    assert result.check.db_schema == "public"
    assert values(out.charts[memory.turns[0]["turn_id"]].spec) == values(spec)


@pytest.fixture
def client():
    db = DuckDBManager(":memory:")
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app), db
    app.dependency_overrides.pop(get_db, None)
    db.close()


def test_replay_api(client) -> None:
    http, db = client
    memory, spec, _ = author_with_dashboard(db)
    bundle = http.get("/api/sessions/author/bundle").json()
    session_id = http.post("/api/sessions/import", json=bundle).json()["session_id"]
    turn_id = memory.turns[0]["turn_id"]

    response = http.post(
        f"/api/sessions/{session_id}/replay", json={"charts": {turn_id: stripped(spec)}}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["check"]["status"] == "found"
    assert values(body["charts"][turn_id]["spec"]) == values(spec)
    assert body["dashboards"][0]["tiles"][0]["result_table"].startswith("temp_result_v")
    assert http.post("/api/sessions/author/replay", json={}).status_code == 400


def test_replay_api_without_author_table_computes_nothing(client) -> None:
    http, db = client
    author_with_dashboard(db)
    bundle = http.get("/api/sessions/author/bundle").json()
    db.execute("DROP TABLE sales")
    session_id = http.post("/api/sessions/import", json=bundle).json()["session_id"]

    body = http.post(f"/api/sessions/{session_id}/replay", json={}).json()

    assert body["check"]["status"] == "missing"
    assert body["charts"] == {} and body["dashboards"] == []
