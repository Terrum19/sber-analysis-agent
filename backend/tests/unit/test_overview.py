import re

import httpx
import openai
import pytest

from app.core.data import ingest, overview
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.profiling import profile_table
from app.core.llm.prompts.schema_describe import SCHEMA_DESCRIBE_SYSTEM
from app.core.memory.profile_memory import ProfileMemory
from app.models.enrichment import ColumnEnrichment


class CountingLLM:
    def __init__(self, answer: str = "Передо мной заказы по регионам.\n\nПропусков нет.") -> None:
        self.answer = answer
        self.calls: list[tuple[str, str, dict]] = []

    async def complete(self, system: str, user: str, **kwargs) -> str:
        self.calls.append((system, user, kwargs))
        return self.answer


class BrokenLLM:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def complete(self, system: str, user: str, **kwargs) -> str:
        self.calls += 1
        raise self.error


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    yield manager
    manager.close()


def orders(db, rows: int = 3) -> None:
    db.execute(
        "CREATE OR REPLACE TABLE orders AS SELECT DATE '2024-01-01' + i::INTEGER AS day, "
        "'msk' AS region, i * 10.0 AS amount FROM range($rows) t(i)",
        {"rows": rows},
    )
    ProfileMemory(db).save_profile(profile_table(db, "orders"))


def connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx.Request("POST", "http://llm.local/v1"))


async def test_second_call_does_not_reach_llm(db) -> None:
    orders(db)
    llm = CountingLLM()
    first = await overview.describe_dataset(db, "orders", llm)
    second = await overview.describe_dataset(db, "orders", llm)
    assert first == second == llm.answer.strip()
    assert len(llm.calls) == 1
    system, prompt, kwargs = llm.calls[0]
    assert system == SCHEMA_DESCRIBE_SYSTEM
    assert "orders: 3 строки, 3 колонки, данные с 2024-01-01 по 2024-01-03 («day»)" in prompt
    assert kwargs == {"max_tokens": overview.ANSWER_MAX_TOKENS}


async def test_summary_survives_a_new_memory_instance(db) -> None:
    orders(db)
    await overview.describe_dataset(db, "orders", CountingLLM())
    llm = CountingLLM("другой ответ")
    assert await overview.describe_dataset(db, "orders", llm) != "другой ответ"
    assert llm.calls == []


async def test_changed_profile_reaches_llm_again(db) -> None:
    orders(db)
    llm = CountingLLM()
    await overview.describe_dataset(db, "orders", llm)
    orders(db, rows=5)
    await overview.describe_dataset(db, "orders", llm)
    assert len(llm.calls) == 2
    assert "orders: 5 строк" in llm.calls[1][1]


async def test_same_data_profiled_again_keeps_summary(db) -> None:
    orders(db)
    llm = CountingLLM()
    await overview.describe_dataset(db, "orders", llm)
    ProfileMemory(db).save_profile(profile_table(db, "orders"))
    await overview.describe_dataset(db, "orders", llm)
    assert len(llm.calls) == 1


async def test_new_column_title_reaches_llm_again(db) -> None:
    orders(db)
    llm = CountingLLM()
    await overview.describe_dataset(db, "orders", llm)
    ProfileMemory(db).save_enrichment(
        "orders", [ColumnEnrichment(column="amount", title="Сумма заказа", fingerprint="f")]
    )
    await overview.describe_dataset(db, "orders", llm)
    assert len(llm.calls) == 2
    assert "amount «Сумма заказа»" in llm.calls[1][1]


async def test_cached_summary_only_for_current_passport(db) -> None:
    orders(db)
    assert overview.cached_summary(db, "orders") is None
    await overview.describe_dataset(db, "orders", CountingLLM("описание"))
    assert overview.cached_summary(db, "orders") == "описание"
    orders(db, rows=5)
    assert overview.cached_summary(db, "orders") is None
    assert overview.cached_summary(db, "missing") is None


async def test_llm_failure_keeps_previous_summary(db) -> None:
    orders(db)
    await overview.describe_dataset(db, "orders", CountingLLM("старое описание"))
    orders(db, rows=5)
    with pytest.raises(RuntimeError):
        await overview.describe_dataset(db, "orders", BrokenLLM(RuntimeError("LLM упала")))
    assert ProfileMemory(db).load_summary("orders")[0] == "старое описание"


async def test_empty_answer_is_not_saved(db) -> None:
    orders(db)
    llm = CountingLLM("  ")
    assert await overview.describe_dataset(db, "orders", llm) == ""
    assert ProfileMemory(db).load_summary("orders") is None
    await overview.describe_dataset(db, "orders", llm)
    assert len(llm.calls) == 2


async def test_table_without_passport_is_profiled(db) -> None:
    db.execute("CREATE TABLE raw AS SELECT 1 AS id")
    await overview.describe_dataset(db, "raw", CountingLLM())
    assert ProfileMemory(db).load_profile("raw").row_count == 1


def wide_table(db) -> None:
    columns = [f"{i % 5 + 1} AS Q{n}{part}" for n in range(1, 36) for i, part in enumerate("AIE")]
    db.execute(f"CREATE TABLE psych AS SELECT 'RU' AS country, {', '.join(columns)}")
    ProfileMemory(db).save_profile(profile_table(db, "psych"))


def test_wide_table_folds_families(db) -> None:
    wide_table(db)
    memory = ProfileMemory(db)
    prompt = overview.build_prompt(memory.load_profile("psych"), [])
    assert "- Q1A…Q35A (колонок: 35) [numeric, INTEGER]: от 1 до 1" in prompt
    assert "- Q1E…Q35E (колонок: 35)" in prompt
    assert "- country" in prompt
    assert "Q17A" not in prompt
    assert "ещё" not in prompt


def test_prompt_never_exceeds_the_limit(db) -> None:
    names = [f"col{chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(300)]
    db.execute(f"CREATE TABLE huge AS SELECT {', '.join(f'NULL::VARCHAR AS {n}' for n in names)}")
    memory = ProfileMemory(db)
    memory.save_profile(profile_table(db, "huge"))
    rows = [ColumnEnrichment(column=name, description="описание " * 20) for name in names]
    prompt = overview.build_prompt(memory.load_profile("huge"), rows)
    assert len(SCHEMA_DESCRIBE_SYSTEM) + len(prompt) <= overview.PROMPT_MAX_CHARS
    assert "- … и ещё" in prompt
    assert "колонок" in prompt.split("Качество данных:")[0].splitlines()[-2]
    quality = prompt.split("Качество данных:\n")[1].strip()
    # Замечания таблицы (#105) идут перед замечаниями колонок.
    assert quality.startswith(
        "- пустых строк (все колонки NULL): 1\n- все значения пустые: «colaa», «colab»"
    )
    assert re.search(r" и ещё \d+ колон\w+$", quality)
    assert len(quality) <= overview.QUALITY_MAX_CHARS


def test_quality_lists_columns_by_kind(db) -> None:
    db.execute(
        "CREATE TABLE notes AS SELECT CASE WHEN i < 2 THEN NULL ELSE i END AS a, "
        "CASE WHEN i < 5 THEN NULL ELSE i * 1.0 END AS b, 'x' AS c, "
        "CASE WHEN i = 9 THEN 1000 ELSE i END AS d, i::VARCHAR || 'k' AS code FROM range(10) t(i)"
    )
    assert overview.quality_block(profile_table(db, "notes", approx=False)) == (
        "- пропуски: «a» (20%), «b» (50%)\n"
        "- константы: «c» (x)\n"
        "- выбросы за 1.5·IQR: «d» (1)\n"
        "- все значения уникальны — похоже на идентификатор: «code»"
    )


def test_quality_keeps_every_kind_within_the_limit(db) -> None:
    columns = [f"CASE WHEN i < {n % 8 + 1} THEN NULL ELSE i END AS Q{n}A" for n in range(1, 106)]
    db.execute(f"CREATE TABLE gaps AS SELECT {', '.join(columns)}, 'x' AS c FROM range(10) t(i)")
    block = overview.quality_block(profile_table(db, "gaps", approx=False), limit=300)
    lines = block.splitlines()
    assert len(block) <= 300
    assert lines[0].startswith("- пропуски: «Q1A» (20%), «Q2A» (30%)")
    assert re.search(r" и ещё \d+ колон\w+$", lines[0])
    assert lines[1] == "- константы: «c» (x)"


def test_quality_without_notes(db) -> None:
    db.execute("CREATE TABLE clean AS SELECT 1 AS a")
    assert overview.quality_block(profile_table(db, "clean")) == "- замечаний нет"


@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [
        ("пропусков 15.3042%", "пропуски", "15.3042%"),
        ("NaN 16.6667%", "NaN", "16.6667%"),
        ("константа (Москва (центр))", "константы", "Москва (центр)"),
        ("выбросов за 1.5·IQR — 42317", "выбросы за 1.5·IQR", "42317"),
        ("литералы пропусков в тексте: 6", "литералы пропусков в тексте", "6"),
        ("числа записаны текстом", "числа записаны текстом", None),
        (
            "все значения уникальны — похоже на идентификатор",
            "все значения уникальны — похоже на идентификатор",
            None,
        ),
    ],
)
def test_split_note(text: str, kind: str, value: str | None) -> None:
    assert overview.split_note(text) == (kind, value)


def test_prompt_limit_fits_default_ollama_context() -> None:
    tokens = overview.PROMPT_MAX_CHARS / overview.CHARS_PER_TOKEN + overview.ANSWER_MAX_TOKENS
    assert tokens < overview.CONTEXT_TOKENS == 4096


def test_clip_counts_columns_of_dropped_lines() -> None:
    lines = [("- a", 1), ("- b…e (колонок: 4)", 4), ("- f", 1)]
    assert overview.clip(lines, 100, overview.COLUMN_WORDS) == "- a\n- b…e (колонок: 4)\n- f"
    assert overview.clip(lines, 25, overview.COLUMN_WORDS) == "- a\n- … и ещё 5 колонок"


@pytest.mark.parametrize(
    ("count", "text"),
    [(1, "1 строка"), (3, "3 строки"), (11, "11 строк"), (22, "22 строки"), (1500, "1 500 строк")],
)
def test_plural(count: int, text: str) -> None:
    assert overview.plural(count, overview.ROW_WORDS) == text


def test_facts_without_dates(db) -> None:
    db.execute("CREATE TABLE plain AS SELECT 1 AS id")
    profile = profile_table(db, "plain")
    assert overview.facts(profile) == (
        "plain: 1 строка, 1 колонка, колонок с датами нет — период не определить"
    )


def test_profile_rows(db) -> None:
    orders(db)
    memory = ProfileMemory(db)
    rows = overview.profile_rows(memory.load_profile("orders"), [])
    assert [row["Колонка"] for row in rows] == ["day", "region", "amount"]
    assert rows[0]["Тип"] == "дата"
    assert rows[2]["Заполнено, %"] == 100
    assert "Название" not in rows[0]
    titled = [ColumnEnrichment(column="amount", title="Сумма")]
    rows = overview.profile_rows(memory.load_profile("orders"), titled)
    assert [row["Название"] for row in rows] == ["", "", "Сумма"]


@pytest.mark.parametrize(
    "question",
    [
        "Что передо мной?",
        "что перед тобой?",
        "что перед тобой за данные?",
        "что у тебя перед глазами?",
        "Что перед нами?",
        "Что ты видишь?",
        "что передо мной и за какой период?",
        "А что за данные?",
        "Сколько строк?",
        "За какой период данные и сколько их?",
        "Расскажи, что за данные",
        "Опиши данные",
        "Какие колонки есть в таблице?",
        "сколько записей в таблице",
    ],
)
def test_overview_questions(question: str) -> None:
    assert overview.is_overview_question(question)


@pytest.mark.parametrize(
    "question",
    [
        "Сколько строк по Москве?",
        "За какой период выручка росла?",
        "Покажи в другом виде",
        "Опиши выручку по регионам",
        "Сколько продаж за март?",
        "пожалуйста",
        "",
    ],
)
def test_data_questions_go_to_planner(question: str) -> None:
    assert not overview.is_overview_question(question)


async def test_summarize_continues_after_failed_table(db, monkeypatch, caplog) -> None:
    orders(db)
    db.execute("CREATE TABLE other AS SELECT 1 AS id")
    ProfileMemory(db).save_profile(profile_table(db, "other"))
    described = []

    async def describe_dataset(db, table, llm):
        described.append(table)
        if table == "orders":
            raise RuntimeError("сбой")
        return "ок"

    monkeypatch.setattr(overview, "describe_dataset", describe_dataset)
    await ingest.summarize(db, ["orders", "other"], CountingLLM())
    assert described == ["orders", "other"]
    assert "Описание таблицы orders не построено" in caplog.text


async def test_summarize_stops_when_llm_is_unreachable(db) -> None:
    orders(db)
    db.execute("CREATE TABLE other AS SELECT 1 AS id")
    llm = BrokenLLM(connection_error())
    await ingest.summarize(db, ["orders", "other"], llm)
    assert llm.calls == 1
