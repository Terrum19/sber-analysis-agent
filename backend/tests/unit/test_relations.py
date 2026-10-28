"""Связи между таблицами (#165): ключи из схемы, проверенные по данным, подсказка планировщику."""

import asyncio

import duckdb
import pytest

from app.api.schemas.chat import ChatRequest
from app.core.agent.orchestrator import SUMMARY_UNAVAILABLE, AgentOrchestrator
from app.core.agent.planner import StepPlanner
from app.core.agent.step_executor import StepExecutor
from app.core.data import ingest, relations
from app.core.data.connectors.base import BaseConnector
from app.core.data.connectors.csv import CSVConnector
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.relations import Relation, TableChoice
from app.core.memory.session_memory import SessionMemory
from app.core.tools.base import ToolContext
from app.core.tools.registry import tool_registry
from app.core.tools.sql.join_related import _free_name
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


def test_related_tables_show_columns_and_the_join_step(demo) -> None:
    related = relations.related_tables(demo, "demo_events", "Сумма по region")
    assert [item.table for item in related] == ["demo_users"]
    assert related[0].columns[0] == "region"
    text = relations.describe_related("demo_events", related)
    assert "- demo_users: колонки region" in text
    assert "связь: demo_events.user_id -> demo_users.user_id (найдено по данным: 100%" in text
    assert '{"tool": "join_related", "arguments": {"tables": ["demo_users"]}}' in text
    assert relations.describe_related("demo_events", []) == ""


def test_relation_describes_its_origin() -> None:
    relation = Relation("заказы", ("код клиента",), "clients", ("id",))
    assert relation.describe() == "заказы.код клиента -> clients.id (ключ из схемы источника)"


@pytest.fixture
def shop(db):
    """orders -> clients -> cities -> regions: цепочка из четырёх таблиц."""
    attach(
        db,
        {
            "regions": "CREATE TABLE regions AS SELECT * FROM (VALUES ('r1', 'Центр'), "
            "('r2', 'Поволжье')) AS v(region_id, name)",
            "cities": "CREATE TABLE cities AS SELECT * FROM (VALUES ('c1', 'r1', 'Москва'), "
            "('c2', 'r2', 'Казань'), ('c3', 'r2', 'Самара')) AS v(city_id, region_id, name)",
            "clients": "CREATE TABLE clients AS SELECT 'k' || range AS client_id, "
            "'c' || (1 + range % 3) AS city_id, 'Клиент ' || range AS name FROM range(6)",
            "orders": "CREATE TABLE orders AS SELECT range AS order_no, 'k' || (range % 6) "
            "AS client_id, 10 * (1 + range % 4) AS amount FROM range(40)",
        },
    )
    return db


def test_chains_are_found_through_intermediate_tables(shop) -> None:
    routes = relations.paths(relations.load(shop), "orders")
    assert {table: [hop.to_table for hop in path] for table, path in routes.items()} == {
        "clients": ["clients"],
        "cities": ["clients", "cities"],
        "regions": ["clients", "cities", "regions"],
    }
    assert relations.paths(relations.load(shop), "orders", max_hops=1).keys() == {"clients"}
    assert relations.component(relations.load(shop), "regions") == {
        "orders",
        "clients",
        "cities",
        "regions",
    }
    related = relations.related_tables(shop, "orders", "выручка по регионам")
    text = relations.describe_related("orders", related)
    assert "- regions (через clients, cities): колонки" in text
    assert text.count("связь: ") == 6


def test_join_related_walks_the_chain_in_one_step(shop) -> None:
    tool = tool_registry.get("join_related")
    table = tool.execute(shop, "orders", tool.Args.model_validate({"tables": ["regions"]}))
    assert shop.columns(table) == [
        "order_no",
        "client_id",
        "amount",
        "clients_city_id",
        "clients_name",
        "cities_region_id",
        "cities_name",
        "regions_name",
    ]
    totals = shop.query(f"SELECT regions_name, sum(amount) AS s FROM {table} GROUP BY 1 ORDER BY 1")
    gold = shop.query(
        "SELECT r.name AS regions_name, sum(o.amount) AS s FROM orders o "
        "JOIN clients c USING (client_id) JOIN cities ci USING (city_id) "
        "JOIN regions r ON r.region_id = ci.region_id GROUP BY 1 ORDER BY 1"
    )
    assert totals == gold


def test_join_related_goes_from_a_reference_back_to_facts(shop) -> None:
    tool = tool_registry.get("join_related")
    args = tool.Args.model_validate({"tables": ["orders"], "keep_unmatched": False})
    table = tool.execute(shop, "regions", args)
    assert shop.query(f"SELECT count(*) AS n FROM {table}")[0]["n"] == 40


@pytest.mark.parametrize(
    ("input_sql", "tables", "message"),
    [
        ("SELECT * FROM orders", ["weather"], "«weather» не связана с «orders»"),
        ("SELECT amount FROM orders", ["clients"], "нет ключа orders.client_id"),
        ("SELECT * FROM orders", ["orders"], "уже в данных шага"),
    ],
)
def test_join_related_explains_what_to_fix(shop, input_sql, tables, message) -> None:
    shop.execute("CREATE TABLE weather AS SELECT 1 AS t")
    step = shop.save_step_result("orders", input_sql)
    tool = tool_registry.get("join_related")
    with pytest.raises(ValueError, match=message):
        asyncio.run(tool.arun(ToolContext(source="orders"), shop, step, None, tables=tables))


def test_executor_starts_the_path_at_the_source_table(shop) -> None:
    executor = StepExecutor(db=shop)
    filtered = shop.save_step_result("orders", "SELECT * FROM orders WHERE amount > 20")
    step = ResearchStep(step_id=2, tool="join_related", arguments={"tables": ["regions"]})
    table = asyncio.run(executor.execute(step, filtered, source_table="orders"))
    assert "regions_name" in shop.columns(table)
    assert shop.query(f"SELECT count(*) AS n FROM {table}")[0]["n"] == 20


def test_names_shared_by_linked_tables_get_the_table_prefix(shop) -> None:
    names = relations.output_names(shop, relations.load(shop), "orders")
    assert names["regions"] == {"region_id": "regions_region_id", "name": "regions_name"}
    assert names["clients"]["client_id"] == "clients_client_id"
    related = relations.related_tables(shop, "orders", "выручка по регионам")
    regions = next(item for item in related if item.table == "regions")
    assert regions.columns == ["regions_name"]
    assert _free_name("name", {"name"}) == "name_2"
    assert _free_name("name", {"name", "name_2"}) == "name_3"
    assert _free_name("Name", set()) == "Name"


def test_a_taken_name_from_an_earlier_step_gets_a_number(shop) -> None:
    created = shop.save_step_result("orders", "SELECT *, 1 AS regions_name FROM orders")
    tool = tool_registry.get("join_related")
    context = ToolContext(source="orders")
    table = asyncio.run(tool.arun(context, shop, created, None, tables=["regions"]))
    assert shop.columns(table)[3] == "regions_name"
    assert shop.columns(table)[-1] == "regions_name_2"


def test_tables_go_from_facts_to_references(shop) -> None:
    order = relations.priority_order(shop, ["regions", "cities", "orders", "clients"])
    assert order == ["orders", "clients", "cities", "regions"]
    links = relations.load(shop)
    assert relations.role(links, "orders") == "факты"
    assert relations.role(links, "clients") == "справочник"
    assert relations.role(links, "regions") == "справочник"
    assert relations.references(links, "cities") == ["regions"]


def set_titles(db, titles: dict[str, str]) -> None:
    """Названия таблиц, как их пишет обогащение паспорта (#211)."""
    db.execute("ALTER TABLE _meta.table_passport ADD COLUMN IF NOT EXISTS display_name VARCHAR")
    for table, title in titles.items():
        db.execute(
            "UPDATE _meta.table_passport SET display_name = $title WHERE table_name = $table",
            {"table": table, "title": title},
        )


def test_table_titles_come_from_the_passport(demo) -> None:
    assert relations.table_title(demo, "demo_events") is None
    set_titles(demo, {"demo_events": "События покупок", "demo_users": "  "})
    assert relations.table_title(demo, "demo_events") == "События покупок"
    assert relations.table_title(demo, "demo_users") is None
    assert relations.table_title(demo, "missing") is None
    # Название участвует в выборе таблицы по вопросу.
    assert relations.candidate_tables(demo, "покупки по регионам") == ["demo_events"]


def test_candidate_tables_follow_the_question(demo) -> None:
    assert relations.candidate_tables(demo, "сколько event по датам") == ["demo_events"]
    assert relations.candidate_tables(demo, "пользователи по region") == ["demo_users"]
    # Ни одно слово не совпало: обе таблицы равны, факты впереди.
    assert relations.candidate_tables(demo, "выручка по регионам") == [
        "demo_events",
        "demo_users",
    ]
    assert relations.candidate_tables(DuckDBManager(":memory:"), "что угодно") == []


@pytest.fixture
def two_sources(demo):
    """demo_events + demo_users и не связанная с ними таблица погоды."""
    attach(demo, {"weather": "CREATE TABLE weather AS SELECT DATE '2024-01-01' AS day, 5 AS t"})
    return demo


def test_tied_tables_linked_by_keys_are_one_choice(demo) -> None:
    assert relations.choose_table(demo, "выручка по регионам") == TableChoice(table="demo_events")
    assert relations.choose_table(demo, "сколько event") == TableChoice(table="demo_events")
    assert relations.choose_table(DuckDBManager(":memory:"), "что угодно") == TableChoice()


def test_unrelated_tables_without_titles_go_to_the_catalog(two_sources) -> None:
    assert relations.choose_table(two_sources, "выручка по регионам") == TableChoice(
        table="demo_events", catalog=["demo_events", "weather", "demo_users"]
    )
    # Одинаковые названия не различить — вопрос пользователю не задаём.
    set_titles(two_sources, {"demo_events": "Данные", "weather": "Данные"})
    assert relations.choose_table(two_sources, "выручка по регионам").options == {}


def test_unrelated_tables_with_titles_become_options(two_sources) -> None:
    set_titles(two_sources, {"demo_events": "Покупки", "weather": "Погода"})
    assert relations.choose_table(two_sources, "что было в январе") == TableChoice(
        options={"Покупки": "demo_events", "Погода": "weather"}
    )


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
    assert '"join_related", "arguments": {"tables": ["demo_users"]}' in Spy.prompt


def test_joined_tables_are_recorded_in_the_turn(demo) -> None:
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
    memory.record_step(step.model_copy(update={"arguments": {"tables": ["regions", "$x"]}}))
    assert memory._turn["tables"] == ["demo_events", "demo_users", "regions"]


class SpyPlanner:
    """Запоминает контекст; первый шаг читает таблицу first_input."""

    def __init__(self, first_input: str = "$source", llm=None) -> None:
        self.first_input = first_input
        self.contexts: list[dict] = []
        self.questions: list[str] = []
        self.llm = llm

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        self.questions.append(question)
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


class SummaryLLM:
    """Описание таблицы «что передо мной»; failing — эти таблицы LLM не описывает."""

    def __init__(self, failing: tuple[str, ...] = ()) -> None:
        self.failing = failing

    async def complete(self, system: str, prompt: str, **_: object) -> str:
        if any(f"{name}: " in prompt for name in self.failing):
            raise RuntimeError("LLM недоступна")
        return "Описание данных.\nВторой абзац."


def ask(
    db: DuckDBManager,
    planner: SpyPlanner,
    session: str,
    question: str,
    recovery_response: str | None = None,
):
    executor = CopyExecutor(db)
    agent = AgentOrchestrator(planner=planner, executor=executor, insight_gen=StubInsight(), db=db)

    async def collect() -> list[dict]:
        request = ChatRequest(
            session_id=session, question=question, recovery_response=recovery_response
        )
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


def test_planner_picks_the_table_from_the_catalog(two_sources) -> None:
    planner = SpyPlanner(first_input="weather")
    events, executor = ask(two_sources, planner, "catalog", "Выручка по регионам")
    assert planning_tables(events) == ["demo_events", "weather", "demo_users"]
    assert "Таблица для вопроса не выбрана" in planner.contexts[0]["catalog"]
    assert executor.inputs == ["weather"]
    memory = SessionMemory("catalog")
    assert memory.table == "weather"
    assert memory._state["turns"][0]["tables"] == ["weather"]


def test_catalog_keeps_the_first_table_when_the_plan_uses_source(two_sources) -> None:
    events, executor = ask(two_sources, SpyPlanner(), "catalog-source", "Выручка по регионам")
    assert events[-1]["type"] == "done"
    assert executor.inputs == ["demo_events"]
    assert SessionMemory("catalog-source").table == "demo_events"


def test_ambiguous_question_asks_which_data_to_use(two_sources) -> None:
    set_titles(two_sources, {"demo_events": "Покупки", "weather": "Погода"})
    planner = SpyPlanner()
    events, _ = ask(two_sources, planner, "choice", "Что было в январе?")
    assert [event["type"] for event in events] == ["clarification_needed"]
    data = events[0]["data"]
    assert data["kind"] == "table"
    assert data["step_id"] == -1
    assert data["suggestions"] == ["Покупки", "Погода"]
    assert planner.contexts == []
    memory = SessionMemory("choice")
    assert memory.table is None
    assert memory._state["turns"][0]["status"] == "clarification"

    events, executor = ask(two_sources, planner, "choice", "Что было в январе?", "Погода")
    assert events[-1]["type"] == "done"
    assert planner.questions == ["Что было в январе?"]
    assert executor.inputs == ["weather"]
    memory = SessionMemory("choice")
    assert memory.table == "weather"
    assert memory.get_pending_recovery() is None
    assert [(turn["status"], turn["tables"]) for turn in memory._state["turns"]] == [
        ("ok", ["weather"])
    ]


def test_table_answer_by_name_or_free_text(two_sources) -> None:
    set_titles(two_sources, {"demo_events": "Покупки", "weather": "Погода"})
    planner = SpyPlanner()
    ask(two_sources, planner, "by-name", "Что было в январе?")
    _, executor = ask(two_sources, planner, "by-name", "x", "DEMO_EVENTS")
    assert executor.inputs == ["demo_events"]
    assert planner.questions == ["Что было в январе?"]

    ask(two_sources, planner, "free", "Что было в январе?")
    _, executor = ask(two_sources, planner, "free", "x", "про температуру")
    assert planner.questions[-1] == "Что было в январе?\nУточнение пользователя: про температуру"
    assert "Таблица для вопроса не выбрана" in planner.contexts[-1]["catalog"]
    assert "- weather: колонки" in planner.contexts[-1]["catalog"]
    assert executor.inputs == ["demo_events"]


def test_new_question_instead_of_the_answer_drops_the_choice(two_sources) -> None:
    set_titles(two_sources, {"demo_events": "Покупки", "weather": "Погода"})
    ask(two_sources, SpyPlanner(), "drop", "Что было в январе?")
    events, executor = ask(two_sources, SpyPlanner(), "drop", "Сколько event по дням")
    assert events[-1]["type"] == "done"
    assert executor.inputs == ["demo_events"]


def test_overview_describes_linked_tables_by_priority(shop) -> None:
    set_titles(shop, {"orders": "Заказы", "regions": "Регионы"})
    SessionMemory("overview").set_table("regions")
    planner = SpyPlanner(llm=SummaryLLM(failing=("cities",)))
    events, _ = ask(shop, planner, "overview", "Что передо мной?")
    text = next(event["data"] for event in events if event["type"] == "text")
    assert text["headline"] == (
        "Перед вами 4 таблицы, связанные ключами; главные данные — «Заказы» (orders)"
    )
    assert text["details"][0].startswith("1. «Заказы» (orders) — факты: 40 строк, 3 колонки")
    assert text["details"][0].endswith(". Описание данных.")
    assert text["details"][1].startswith("2. clients — справочник: 6 строк")
    assert text["details"][2].endswith(SUMMARY_UNAVAILABLE)
    assert text["details"][3].startswith("4. «Регионы» (regions) — справочник")
    assert text["details"][4].startswith("Связи: ")
    assert text["details"][4].count(" -> ") == 3
    chart = next(event["data"] for event in events if event["type"] == "chart")
    assert chart["caption"] == "Связанные таблицы"
    rows = chart["spec"]["data"]["values"]
    assert [row["Таблица"] for row in rows] == ["orders", "clients", "cities", "regions"]
    assert rows[0]["Ссылается на"] == "clients"
    assert rows[3]["Название"] == "Регионы"
    assert planner.contexts == []
    assert events[-1]["type"] == "done"


def test_overview_of_a_lone_table_stays_as_before(two_sources) -> None:
    SessionMemory("lone").set_table("weather")
    events, _ = ask(two_sources, SpyPlanner(llm=SummaryLLM()), "lone", "Что передо мной?")
    chart = next(event["data"] for event in events if event["type"] == "chart")
    assert chart["caption"] == "Колонки таблицы"


def test_overview_failure_ends_the_turn(shop, monkeypatch) -> None:
    def broken(*_):
        raise RuntimeError("паспорт недоступен")

    monkeypatch.setattr(relations, "priority_order", broken)
    SessionMemory("broken-overview").set_table("orders")
    events, _ = ask(shop, SpyPlanner(llm=SummaryLLM()), "broken-overview", "Что передо мной?")
    assert [event["type"] for event in events][-2:] == ["error", "done"]
    assert SessionMemory("broken-overview")._state["turns"][0]["status"] == "failed"


def test_broken_relation_store_leaves_the_plan_without_related(demo, monkeypatch) -> None:
    def broken(*_):
        raise duckdb.Error("каталог недоступен")

    monkeypatch.setattr(relations, "related_tables", broken)
    SessionMemory("broken").set_table("demo_events")
    planner = SpyPlanner()
    events, _ = ask(demo, planner, "broken", "Сумма по region")
    assert planning_tables(events) == ["demo_events"]
    assert planner.contexts[0]["related"] == []
