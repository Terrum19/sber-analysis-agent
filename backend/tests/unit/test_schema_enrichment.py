import asyncio
import json
import logging

import httpx
import openai
import pytest

from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.profiling import profile_table
from app.core.data.schema_enrichment import TITLE_MAX_CHARS, SchemaEnricher, source_comment
from app.core.llm.client import LLMClient
from app.core.memory.profile_memory import ProfileMemory

SHORT = "Сумма заказа в рублях с НДС"
LONG = "Код статуса заказа: 1 — новый, 2 — оплачен, 3 — отгружен. " * 5
DOCUMENT = "## Заказы\n- amount — сумма заказа\n- status — статус заказа\n- day — дата заказа"
MARKER = "Колонки (JSON):\n"


def requested(prompt: str) -> list[dict]:
    start = prompt.index(MARKER) + len(MARKER)
    return json.loads(prompt[start : prompt.index("\n\n", start)])


def auto_answer(columns: list[dict]) -> dict:
    return {
        "columns": [
            {
                "column": column["column"],
                "title": f"Название {column['column']}",
                "description": f"Описание {column['column']}",
            }
            for column in columns
        ]
    }


class FakeLLM:
    """LLM-заглушка: считает вызовы и отвечает по запрошенным колонкам."""

    def __init__(self, answer=auto_answer) -> None:
        self.answer = answer
        self.calls: list[list[dict]] = []
        self.prompts: list[str] = []

    async def complete_structured(self, system: str, user: str, schema):
        columns = requested(user)
        self.calls.append(columns)
        self.prompts.append(user)
        result = self.answer(columns)
        if isinstance(result, Exception):
            raise result
        return schema.model_validate(result)

    def columns(self) -> list[list[str]]:
        return [[column["column"] for column in columns] for columns in self.calls]


def unavailable() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx.Request("POST", "http://llm.local/v1"))


def profiled(db, table: str) -> None:
    ProfileMemory(db).save_profile(profile_table(db, table))


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE orders AS SELECT * FROM (VALUES "
        "(10.5, 1, 'msk', DATE '2024-01-05'), (20.0, 2, 'spb', DATE '2024-02-05'), "
        "(5.25, 3, 'msk', DATE '2024-03-05')) AS rows(amount, status, region, day)"
    )
    profiled(manager, "orders")
    yield manager
    manager.close()


def by_column(rows) -> dict:
    return {row.column: row for row in rows}


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("\n\t", None),
        ("Amount", None),
        (" amount ", None),
        ("x", "x"),
        (LONG, LONG),
    ],
)
def test_source_comment(comment, expected) -> None:
    assert source_comment("amount", comment) == expected


async def test_without_description_nothing_happens(db) -> None:
    llm = FakeLLM()
    rows = await SchemaEnricher(db, llm).enrich("orders", comments={"region": "  "})
    assert llm.calls == []
    assert [row.column for row in rows] == ["amount", "status", "region", "day"]
    assert all(
        row.title is None and row.description is None and row.fingerprint is None for row in rows
    )
    [passport] = db.query("SELECT enriched_at FROM _meta.table_passport")
    assert passport["enriched_at"] is None


async def test_comments_are_saved_as_is_without_llm(db) -> None:
    llm = FakeLLM()
    comments = {"amount": SHORT, "status": LONG, "region": "  ", "day": "Day"}
    rows = by_column(await SchemaEnricher(db, llm).enrich("orders", comments=comments))
    assert llm.calls == []
    assert rows["amount"].description == SHORT
    assert rows["status"].description == LONG
    assert {rows[name].description_source for name in ("amount", "status")} == {"source"}
    for name in ("region", "day"):
        assert rows[name].description is None
        assert rows[name].description_source == "none"
    assert all(row.title is None and row.fingerprint for row in rows.values())
    assert by_column(ProfileMemory(db).load_enrichment("orders")) == rows


async def test_document_gives_titles_and_descriptions(db) -> None:
    llm = FakeLLM()
    rows = by_column(await SchemaEnricher(db, llm).enrich("orders", document=DOCUMENT))
    assert llm.columns() == [["amount", "status", "region", "day"]]
    assert DOCUMENT in llm.prompts[0]
    assert llm.calls[0][0] == {"column": "amount", "type": "DECIMAL(4,2)", "comment": None}
    for name, row in rows.items():
        assert row.title == f"Название {name}"
        assert row.description == f"Описание {name}"
        assert row.description_source == "document"
        assert row.fingerprint
    assert by_column(ProfileMemory(db).load_enrichment("orders")) == rows


async def test_comment_wins_over_document(db) -> None:
    llm = FakeLLM()
    rows = by_column(
        await SchemaEnricher(db, llm).enrich("orders", {"amount": SHORT}, document=DOCUMENT)
    )
    assert llm.calls[0][0]["comment"] == SHORT
    assert rows["amount"].description == SHORT
    assert rows["amount"].description_source == "source"
    assert rows["amount"].title == "Название amount"
    assert rows["status"].description_source == "document"


async def test_column_missing_in_document_stays_empty(db) -> None:
    def answer(columns):
        reply = auto_answer(columns)
        for item in reply["columns"]:
            if item["column"] == "region":
                item["title"] = item["description"] = None
        return reply

    llm = FakeLLM(answer)
    rows = by_column(await SchemaEnricher(db, llm).enrich("orders", document=DOCUMENT))
    region = rows["region"]
    assert region.title is None and region.description is None
    assert region.description_source == "none"
    assert region.fingerprint
    llm = FakeLLM()
    await SchemaEnricher(db, llm).enrich("orders", document=DOCUMENT)
    assert llm.calls == []


async def test_bad_values_are_rejected(db, caplog) -> None:
    def answer(columns):
        return {
            "columns": [
                {"column": "amount", "title": "x" * (TITLE_MAX_CHARS + 1), "description": "  "},
                {"column": "status", "title": "STATUS", "description": "z" * 201},
                {"column": "region", "title": "  ", "description": "Регион"},
                {"column": "day", "title": " День ", "description": " Дата заказа "},
            ]
        }

    with caplog.at_level(logging.WARNING):
        rows = by_column(await SchemaEnricher(db, FakeLLM(answer)).enrich("orders", None, "doc"))
    assert rows["amount"].title is None and rows["amount"].description is None
    assert rows["status"].title is None and rows["status"].description is None
    assert rows["region"].title is None and rows["region"].description == "Регион"
    assert rows["day"].title == "День" and rows["day"].description == "Дата заказа"
    assert "негодное название колонки «amount»" in caplog.text
    assert "описание колонки «status» длиннее 200" in caplog.text


async def test_unrequested_and_duplicate_answers_are_ignored(db, caplog) -> None:
    def answer(columns):
        reply = auto_answer(columns)
        reply["columns"] += [
            {"column": "amount", "title": "Дубль"},
            {"column": "ghost", "title": "Призрак"},
        ]
        return reply

    with caplog.at_level(logging.WARNING):
        rows = by_column(await SchemaEnricher(db, FakeLLM(answer)).enrich("orders", None, "doc"))
    assert rows["amount"].title == "Название amount"
    assert "ghost" not in rows
    assert "незапрошенную колонку «ghost»" in caplog.text


async def test_profile_is_required_and_not_recomputed(db) -> None:
    db.execute("CREATE TABLE fresh AS SELECT 1 AS id")
    with pytest.raises(LookupError):
        await SchemaEnricher(db, FakeLLM()).enrich("fresh", document=DOCUMENT)

    db.execute("INSERT INTO orders VALUES (7.0, 1, 'kzn', DATE '2024-04-05')")
    await SchemaEnricher(db, FakeLLM()).enrich("orders", document=DOCUMENT)
    assert ProfileMemory(db).load_profile("orders").row_count == 3


async def test_invalid_batch_does_not_stop_other_batches(db, monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "enrich_batch_size", 2)
    replies = iter([ValueError("not json"), None])

    def answer(columns):
        return next(replies) or auto_answer(columns)

    llm = FakeLLM(answer)
    with caplog.at_level(logging.WARNING):
        rows = by_column(await SchemaEnricher(db, llm).enrich("orders", {"amount": SHORT}, "doc"))
    assert llm.columns() == [["amount", "status"], ["region", "day"]]
    assert rows["amount"].description == SHORT
    assert rows["amount"].description_source == "source"
    assert rows["amount"].title is None and rows["amount"].fingerprint is None
    assert rows["status"].description_source == "none"
    assert rows["region"].title == "Название region"
    assert "Невалидный ответ LLM" in caplog.text


async def test_unexpected_llm_error_fails_only_its_batch(db, monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "enrich_batch_size", 2)
    replies = iter([IndexError("list index out of range"), None])

    def answer(columns):
        return next(replies) or auto_answer(columns)

    with caplog.at_level(logging.WARNING):
        rows = by_column(await SchemaEnricher(db, FakeLLM(answer)).enrich("orders", None, "doc"))
    assert rows["amount"].description_source == "none"
    assert rows["amount"].fingerprint is None
    assert rows["region"].description_source == "document"
    [record] = [r for r in caplog.records if "Неожиданная ошибка LLM" in r.message]
    assert record.exc_info[0] is IndexError


async def test_cancellation_is_not_swallowed(db) -> None:
    def cancel(columns):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await SchemaEnricher(db, FakeLLM(cancel)).enrich("orders", document=DOCUMENT)


async def test_schema_mismatch_is_a_failure(db) -> None:
    def answer(columns):
        return {"columns": [{"column": "amount", "title": ["не строка"]}]}

    rows = await SchemaEnricher(db, FakeLLM(answer)).enrich("orders", document=DOCUMENT)
    assert all(row.description_source == "none" and row.fingerprint is None for row in rows)


async def test_missing_column_in_answer_fails_only_that_column(db, caplog) -> None:
    def answer(columns):
        reply = auto_answer(columns)
        reply["columns"] = [item for item in reply["columns"] if item["column"] != "region"]
        return reply

    with caplog.at_level(logging.WARNING):
        rows = by_column(await SchemaEnricher(db, FakeLLM(answer)).enrich("orders", None, "doc"))
    assert rows["region"].fingerprint is None
    assert rows["day"].description_source == "document"
    assert "не вернула колонки ['region']" in caplog.text


async def test_unavailable_llm_is_not_called_again(db, monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "enrich_batch_size", 1)
    llm = FakeLLM(lambda columns: unavailable())
    with caplog.at_level(logging.WARNING):
        rows = by_column(
            await SchemaEnricher(db, llm).enrich("orders", {"amount": SHORT}, DOCUMENT)
        )
    assert len(llm.calls) == 1
    assert rows["amount"].description == SHORT
    assert rows["region"].description is None
    assert all(row.title is None and row.fingerprint is None for row in rows.values())
    assert "LLM недоступна" in caplog.text


async def test_next_run_after_failure_retries(db) -> None:
    await SchemaEnricher(db, FakeLLM(lambda columns: unavailable())).enrich("orders", None, "doc")
    llm = FakeLLM()
    enricher = SchemaEnricher(db, llm)
    rows = await enricher.enrich("orders", document="doc")
    assert llm.columns() == [["amount", "status", "region", "day"]]
    assert all(row.description_source == "document" for row in rows)
    assert enricher.llm_available


async def test_repeated_enrich_skips_llm(db) -> None:
    first = await SchemaEnricher(db, FakeLLM()).enrich("orders", {"amount": SHORT}, DOCUMENT)
    llm = FakeLLM()
    second = await SchemaEnricher(db, llm).enrich("orders", {"amount": SHORT}, DOCUMENT)
    assert llm.calls == []
    assert second == first


async def test_only_changed_columns_are_reprocessed(db) -> None:
    comments = {"amount": SHORT}
    first = by_column(await SchemaEnricher(db, FakeLLM()).enrich("orders", comments, DOCUMENT))

    db.execute("ALTER TABLE orders ALTER status TYPE BIGINT")
    profiled(db, "orders")
    llm = FakeLLM()
    rows = by_column(await SchemaEnricher(db, llm).enrich("orders", comments, DOCUMENT))
    assert llm.columns() == [["status"]]
    assert rows["amount"] == first["amount"]

    llm = FakeLLM()
    rows = by_column(
        await SchemaEnricher(db, llm).enrich("orders", {"amount": "Сумма без НДС"}, DOCUMENT)
    )
    assert llm.columns() == [["amount"]]
    assert rows["amount"].description == "Сумма без НДС"

    llm = FakeLLM()
    await SchemaEnricher(db, llm).enrich("orders", {"amount": "Сумма без НДС"}, DOCUMENT + "!")
    assert llm.columns() == [["amount", "status", "region", "day"]]


async def test_long_document_is_trimmed(db, monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "enrich_document_max_chars", 10)
    llm = FakeLLM()
    with caplog.at_level(logging.WARNING):
        await SchemaEnricher(db, llm).enrich("orders", document="0123456789ABCDEF")
    assert "<<<\n0123456789\n>>>" in llm.prompts[0]
    assert "обрезан" in caplog.text


def test_default_llm_client(db) -> None:
    assert isinstance(SchemaEnricher(db).llm, LLMClient)


async def test_reconnect_without_document_keeps_document_titles(db) -> None:
    first = by_column(
        await SchemaEnricher(db, FakeLLM()).enrich("orders", {"amount": SHORT}, DOCUMENT)
    )
    llm = FakeLLM()
    rows = by_column(await SchemaEnricher(db, llm).enrich("orders", {"amount": SHORT}))
    assert llm.calls == []
    assert rows == first
    assert by_column(ProfileMemory(db).load_enrichment("orders")) == first

    rows = by_column(await SchemaEnricher(db, llm).enrich("orders", {"status": "Код статуса"}))
    assert llm.calls == []
    assert rows["amount"].title == "Название amount"
    assert (rows["amount"].description, rows["amount"].description_source) == (None, "none")
    assert rows["status"].title == "Название status"
    assert (rows["status"].description, rows["status"].description_source) == (
        "Код статуса",
        "source",
    )
    assert rows["region"] == first["region"]

    llm = FakeLLM()
    await SchemaEnricher(db, llm).enrich("orders", {"status": "Код статуса"}, DOCUMENT)
    assert llm.columns() == [["amount", "status"]]
