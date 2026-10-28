"""Связи между таблицами (#165): ключи из схемы, проверенные по данным, подсказка планировщику."""

import asyncio

import duckdb
import pytest

from app.api.schemas.chat import ChatRequest
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.planner import StepPlanner
from app.core.data import ingest, relations
from app.core.data.connectors.base import BaseConnector
from app.core.data.connectors.csv import CSVConnector
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.relations import Relation
from app.core.memory.session_memory import SessionMemory
from app.core.tools.registry import tool_registry
from app.models.insight import Insight
from app.models.research_step import ResearchPlan, ResearchStep


class TablesConnector(BaseConnector):
    """Источник из готовых SQL; foreign_keys — как у PostgreSQL."""

    def __init__(self, tables: dict[str, str], keys: list[Relation] | None = None) -> None:
        self.tables = tables
        self.keys = keys or []

    def connect(self, db) -> list[str]:
        for sql in self.tables.values():
            db.execute(sql)
        return list(self.tables)

    def foreign_keys(self, db) -> list[Relation]:
        return self.keys


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    yield manager
    manager.close()


@pytest.fixture
def demo(db, tmp_path):
    events = tmp_path / "demo_events.csv"
    events.write_text(
        "event_id,user_id,amount,event_date\n"
        "e1,u1,100,2024-01-01\ne2,u2,50,2024-01-02\ne3,u1,30,2024-01-03\ne4,u3,20,2024-01-04\n"
    )
    users = tmp_path / "demo_users.csv"
    users.write_text(
        "user_id,region,signup_date\nu1,Москва,2024-01-01\nu2,Казань,2024-01-01\n"
        "u3,Москва,2024-01-02\n"
    )
    with db.lock:
        ingest.attach(CSVConnector({"demo_events": str(events), "demo_users": str(users)}), db)
    return db


def attach(db, tables: dict[str, str], keys: list[Relation] | None = None) -> None:
    with db.lock:
        ingest.attach(TablesConnector(tables, keys), db)


def test_csv_tables_get_a_relation_checked_on_data(demo) -> None:
    assert relations.load(demo) == [
        Relation("demo_events", ("user_id",), "demo_users", ("user_id",), "inferred", 1.0)
    ]
    assert relations.load(demo, "demo_users") == relations.load(demo, "demo_events")


def test_key_named_after_the_table_points_to_its_id(db) -> None:
    attach(
        db,
        {
            "users": "CREATE TABLE users AS SELECT range AS id, 'u' || range AS name FROM range(5)",
            "orders": "CREATE TABLE orders AS SELECT range AS id, range % 5 AS user_id, "
            "range * 10 AS amount FROM range(20)",
        },
    )
    assert relations.load(db) == [
        Relation("orders", ("user_id",), "users", ("id",), "inferred", 1.0)
    ]


@pytest.mark.parametrize(
    "tables",
    [
        # id = id есть почти в любой паре таблиц.
        {
            "a": "CREATE TABLE a AS SELECT range AS id FROM range(5)",
            "b": "CREATE TABLE b AS SELECT range AS id FROM range(5)",
        },
        # Ключ справа не уникален: соединение размножило бы строки.
        {
            "a": "CREATE TABLE a AS SELECT range % 3 AS client_id FROM range(9)",
            "b": "CREATE TABLE b AS SELECT range % 3 AS client_id, range AS n FROM range(9)",
        },
        # Значения слева почти не находятся справа.
        {
            "a": "CREATE TABLE a AS SELECT range AS client_id FROM range(100)",
            "b": "CREATE TABLE b AS SELECT range AS client_id FROM range(50, 150)",
        },
        # Меры с одним именем — не ключи.
        {
            "a": "CREATE TABLE a AS SELECT range * 1.5 AS amount FROM range(5)",
            "b": "CREATE TABLE b AS SELECT range * 1.5 AS amount FROM range(5)",
        },
    ],
)
def test_coincidences_are_not_relations(db, tables) -> None:
    attach(db, tables)
    assert relations.load(db) == []


def test_one_to_one_tables_keep_a_single_relation(db) -> None:
    attach(
        db,
        {
            "people": "CREATE TABLE people AS SELECT 'p' || range AS person_id FROM range(4)",
            "passports": "CREATE TABLE passports AS SELECT 'p' || range AS person_id, "
            "range AS number FROM range(4)",
        },
    )
    assert len(relations.load(db)) == 1


def test_declared_keys_come_first_and_are_not_guessed(db) -> None:
    declared = Relation("orders", ("buyer",), "clients", ("code",))
    attach(
        db,
        {
            "clients": "CREATE TABLE clients AS SELECT 'c' || range AS code, "
            "'c' || range AS client_id FROM range(3)",
            "orders": "CREATE TABLE orders AS SELECT 'c' || (range % 3) AS buyer, "
            "'c' || (range % 3) AS client_id FROM range(6)",
        },
        [declared, Relation("orders", ("buyer",), "missing", ("code",))],
    )
    assert relations.load(db) == [declared]


def test_foreign_keys_of_duckdb_tables_are_read_from_the_catalog(db) -> None:
    attach(
        db,
        {
            "regions": "CREATE TABLE regions (code VARCHAR PRIMARY KEY, name VARCHAR)",
            "shops": "CREATE TABLE shops (shop VARCHAR, area VARCHAR REFERENCES regions (code))",
        },
    )
    assert relations.load(db) == [Relation("shops", ("area",), "regions", ("code",))]


def test_relations_of_dropped_tables_are_not_loaded(demo) -> None:
    demo.execute("DROP TABLE demo_users")
    assert relations.load(demo) == []
    relations.delete(demo, "demo_users")
    assert demo.query("SELECT count(*) AS n FROM _meta.table_relation")[0]["n"] == 0


def test_discovery_failure_keeps_the_tables(db, monkeypatch) -> None:
    def broken(*_):
        raise duckdb.Error("каталог недоступен")

    monkeypatch.setattr(relations, "declared_in_duckdb", broken)
    attach(db, {"a": "CREATE TABLE a AS SELECT 1 AS x"})
    assert db.user_tables() == ["a"]


def test_failed_check_skips_the_candidate(db, monkeypatch) -> None:
    def broken(*_):
        raise duckdb.Error("нет доступа")

    monkeypatch.setattr(relations, "verify", broken)
    attach(
        db,
        {
            "a": "CREATE TABLE a AS SELECT 'k' || range AS client_id FROM range(3)",
            "b": "CREATE TABLE b AS SELECT 'k' || range AS client_id FROM range(3)",
        },
    )
    assert relations.load(db) == []


def test_related_tables_show_columns_and_a_ready_join(demo) -> None:
    related = relations.related_tables(demo, "demo_events", "Сумма по region")
    assert [item.table for item in related] == ["demo_users"]
    assert related[0].columns[0] == "region"
    text = relations.describe_related("demo_events", related)
    assert "demo_users: колонки region" in text
    assert "найдено по данным: 100% значений есть в demo_users" in text
    assert '"right_table": "demo_users", "condition": "t.user_id = t_right.user_id"' in text
    assert relations.describe_related("demo_events", []) == ""


def test_condition_quotes_names_and_follows_the_current_table() -> None:
    relation = Relation("заказы", ("код клиента",), "clients", ("id",))
    assert relation.condition("заказы") == 't."код клиента" = t_right."id"'
    assert relation.condition("clients") == 't."id" = t_right."код клиента"'
    assert relation.describe() == "заказы.код клиента -> clients.id (ключ из схемы источника)"


def test_candidate_tables_follow_the_question(demo) -> None:
    assert relations.candidate_tables(demo, "сколько event по датам") == ["demo_events"]
    assert relations.candidate_tables(demo, "пользователи по region") == ["demo_users"]
    # Ни одно слово не совпало: обе таблицы уходят планировщику каталогом.
    assert relations.candidate_tables(demo, "выручка по регионам") == [
        "demo_events",
        "demo_users",
    ]
    assert relations.candidate_tables(DuckDBManager(":memory:"), "что угодно") == []


def test_catalog_lists_tables_with_their_relations(demo) -> None:
    text = relations.describe_catalog(demo, ["demo_events", "demo_users"], "сумма по region")
    assert "укажи её имя в input первого шага" in text
    assert "- demo_events: колонки event_id, user_id, amount, event_date\n  связь: " in text
    assert "- demo_users: колонки region, user_id, signup_date" in text
    assert text.count("связь:") == 1


def test_catalog_cuts_long_column_lists(db) -> None:
    columns = ", ".join(f"range AS c{i}" for i in range(15))
    db.execute(f"CREATE TABLE wide AS SELECT {columns} FROM range(2)")
    assert "c11 и ещё 3" in relations.describe_catalog(db, ["wide"], "c0")


def test_join_keeps_one_copy_of_the_key(demo) -> None:
    tool = tool_registry.get("join_tables")
    args = tool.Args.model_validate(
        {"right_table": "demo_users", "condition": "t.user_id = t_right.user_id"}
    )
    table = tool.execute(demo, "demo_events", args)
    assert demo.columns(table) == [
        "event_id",
        "user_id",
        "amount",
        "event_date",
        "region",
        "signup_date",
    ]
    regions = demo.query(f"SELECT region, sum(amount) AS s FROM {table} GROUP BY 1 ORDER BY 1")
    assert regions == [{"region": "Казань", "s": 50}, {"region": "Москва", "s": 150}]


def test_join_drops_quoted_keys_and_keeps_differently_named_ones(db) -> None:
    db.execute('CREATE TABLE l AS SELECT 1 AS "код", 1 AS a')
    db.execute('CREATE TABLE r AS SELECT 1 AS "код", 1 AS b, 7 AS a')
    tool = tool_registry.get("left_join_tables")
    args = tool.Args.model_validate({"right_table": "r", "condition": 't."код" = t_right."код"'})
    assert db.columns(tool.execute(db, "l", args)) == ["код", "a", "b", "a_1"]
    args = tool.Args.model_validate({"right_table": "r", "condition": "t.a = t_right.b"})
    assert db.columns(tool.execute(db, "l", args)) == ["код", "a", "код_1", "b", "a_1"]


def test_planner_prompt_lists_related_tables(demo) -> None:
    class Spy:
        prompt = ""

        async def complete_structured(self, system, user, schema):
            Spy.prompt = user
            return schema.model_validate({"steps": [], "final_viz": "none"})

    context = SessionMemory("rel", db=demo)
    context.set_table("demo_events")
    built = context.build_context("Сумма по region")
    built["related"] = relations.related_tables(demo, "demo_events", "Сумма по region")
    asyncio.run(StepPlanner(llm=Spy()).plan("Сумма по region", built))
    assert "Связанные таблицы (текущая таблица $source — demo_events)" in Spy.prompt
    assert "t.user_id = t_right.user_id" in Spy.prompt


def test_joined_table_is_recorded_in_the_turn(demo) -> None:
    memory = SessionMemory("joins", db=demo)
    memory.set_table("demo_events")
    memory.start_turn("Сумма по регионам")
    step = ResearchStep(
        step_id=1,
        tool="join_tables",
        arguments={"right_table": "demo_users", "condition": "t.user_id = t_right.user_id"},
    )
    memory.record_step(step)
    memory.record_step(step.model_copy(update={"arguments": {"right_table": "$step_1"}}))
    assert memory._turn["tables"] == ["demo_events", "demo_users"]


class SpyPlanner:
    """Запоминает контекст; первый шаг читает таблицу first_input."""

    def __init__(self, first_input: str = "$source") -> None:
        self.first_input = first_input
        self.contexts: list[dict] = []

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        self.contexts.append(context)
        step = ResearchStep(step_id=1, tool="copy", input=self.first_input)
        return ResearchPlan(steps=[step], final_viz="table")


class CopyExecutor:
    def __init__(self, db: DuckDBManager) -> None:
        self.db = db
        self.inputs: list[str] = []

    async def execute(self, step: ResearchStep, input_table: str, **_: object) -> str:
        table = input_table if step.input in ("$source", "$prev") else step.input
        self.inputs.append(table)
        return self.db.save_step_result(table, f"SELECT * FROM {table}")


class StubInsight:
    async def generate(self, question: str, result_table: str, **_: object) -> Insight:
        return Insight(headline="ответ", details=[])


def ask(db: DuckDBManager, planner: SpyPlanner, session: str, question: str):
    executor = CopyExecutor(db)
    agent = AgentOrchestrator(planner=planner, executor=executor, insight_gen=StubInsight(), db=db)

    async def collect() -> list[dict]:
        request = ChatRequest(session_id=session, question=question)
        return [event async for event in agent.run(request)]

    return asyncio.run(collect()), executor


def planning_tables(events: list[dict]) -> list[str]:
    return next(
        event["data"]["tables"]
        for event in events
        if event["type"] == "progress" and event["data"]["stage"] == "planning"
    )


def test_session_table_brings_its_related_tables(demo) -> None:
    SessionMemory("with-table").set_table("demo_events")
    planner = SpyPlanner()
    events, _ = ask(demo, planner, "with-table", "Сумма по region")
    assert planning_tables(events) == ["demo_events", "demo_users"]
    assert [item.table for item in planner.contexts[0]["related"]] == ["demo_users"]
    assert "catalog" not in planner.contexts[0]


def test_clear_winner_needs_no_catalog(demo) -> None:
    planner = SpyPlanner()
    events, executor = ask(demo, planner, "winner", "Сколько event по дням")
    assert planning_tables(events) == ["demo_events", "demo_users"]
    assert executor.inputs == ["demo_events"]
    assert SessionMemory("winner").table == "demo_events"


def test_planner_picks_the_table_from_the_catalog(demo) -> None:
    planner = SpyPlanner(first_input="demo_users")
    events, executor = ask(demo, planner, "catalog", "Выручка по регионам")
    assert planning_tables(events) == ["demo_events", "demo_users"]
    assert "Таблица для вопроса не выбрана" in planner.contexts[0]["catalog"]
    assert executor.inputs == ["demo_users"]
    memory = SessionMemory("catalog")
    assert memory.table == "demo_users"
    assert memory._state["turns"][0]["tables"] == ["demo_users"]


def test_catalog_keeps_the_first_table_when_the_plan_uses_source(demo) -> None:
    events, executor = ask(demo, SpyPlanner(), "catalog-source", "Выручка по регионам")
    assert events[-1]["type"] == "done"
    assert executor.inputs == ["demo_events"]
    assert SessionMemory("catalog-source").table == "demo_events"


def test_broken_relation_store_leaves_the_plan_without_related(demo, monkeypatch) -> None:
    def broken(*_):
        raise duckdb.Error("каталог недоступен")

    monkeypatch.setattr(relations, "related_tables", broken)
    SessionMemory("broken").set_table("demo_events")
    planner = SpyPlanner()
    events, _ = ask(demo, planner, "broken", "Сумма по region")
    assert planning_tables(events) == ["demo_events"]
    assert planner.contexts[0]["related"] == []
