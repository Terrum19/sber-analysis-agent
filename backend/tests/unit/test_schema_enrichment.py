import json
import logging

import httpx
import openai
import pytest

from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.schema_enrichment import (
    ALIAS_MAX_CHARS,
    SchemaEnricher,
    clean_aliases,
    description_source,
)
from app.core.llm.client import LLMClient
from app.core.memory.profile_memory import ProfileMemory

SHORT = "Сумма заказа в рублях с НДС"
LONG = "Код статуса заказа: 1 — новый, 2 — оплачен, 3 — отгружен. " * 5
MARKER = "Колонки (JSON):\n"


def requested(prompt: str) -> list[dict]:
    start = prompt.index(MARKER) + len(MARKER)
    return json.loads(prompt[start : prompt.index("\n\n", start)])


def auto_answer(kind: str, columns: list[dict]) -> dict:
    if kind == "compress":
        return {
            "columns": [
                {"column": column["column"], "description": f"Сжато: {column['column']}"}
                for column in columns
            ]
        }
    return {
        "columns": [
            {
                "column": column["column"],
                "description": f"Описание {column['column']}",
                "aliases": [f"{column['column']} алиас"],
            }
            for column in columns
        ]
    }


class FakeLLM:
    """LLM-заглушка: считает вызовы и отвечает по запрошенным колонкам."""

    def __init__(self, answer=auto_answer) -> None:
        self.answer = answer
        self.calls: list[tuple[str, list[dict]]] = []
        self.prompts: list[str] = []

    async def complete_structured(self, system: str, user: str, schema):
        kind = "compress" if user.startswith("Сожми") else "aliases"
        columns = requested(user)
        self.calls.append((kind, columns))
        self.prompts.append(user)
        result = self.answer(kind, columns)
        if isinstance(result, Exception):
            raise result
        return schema.model_validate(result)

    def columns(self, kind: str) -> list[list[str]]:
        return [
            [column["column"] for column in columns] for name, columns in self.calls if name == kind
        ]


def unavailable() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx.Request("POST", "http://llm.local/v1"))


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE orders AS SELECT * FROM (VALUES "
        "(10.5, 1, 'msk', DATE '2024-01-05'), (20.0, 2, 'spb', DATE '2024-02-05'), "
        "(5.25, 3, 'msk', DATE '2024-03-05')) AS rows(amount, status, region, day)"
    )
    yield manager
    manager.close()


def by_column(rows) -> dict:
    return {row.column: row for row in rows}


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        (None, "generated"),
        ("", "generated"),
        ("   ", "generated"),
        ("\n\t", "generated"),
        ("Amount", "generated"),
        (" amount ", "generated"),
        ("x", "source"),
        ("x" * 200, "source"),
        ("x" * 201, "source_compressed"),
        (" " + "x" * 199 + " ", "source_compressed"),
    ],
)
def test_description_source(comment, expected) -> None:
    assert description_source("amount", comment) == expected


def test_description_limit_follows_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "enrich_description_max_chars", 10)
    assert description_source("amount", "x" * 10) == "source"
    assert description_source("amount", "x" * 11) == "source_compressed"


def test_clean_aliases() -> None:
    aliases = [
        " Сумма ",
        "сумма",
        "AMOUNT",
        "",
        "   ",
        "x" * (ALIAS_MAX_CHARS + 1),
        "y" * ALIAS_MAX_CHARS,
        "Выручка",
        "Оборот",
        "Деньги",
        "Чек",
    ]
    assert clean_aliases("amount", aliases) == ["Сумма", "y" * ALIAS_MAX_CHARS, "Выручка", "Оборот"]


async def test_enrich_picks_description_source_and_saves_to_meta(db) -> None:
    llm = FakeLLM()
    comments = {"amount": SHORT, "status": LONG, "region": "  ", "day": "Day"}
    rows = await SchemaEnricher(db, llm).enrich("orders", comments=comments)
    result = by_column(rows)
    assert list(result) == ["amount", "status", "region", "day"]

    amount = result["amount"]
    assert amount.description == SHORT
    assert amount.description_source == "source"
    assert amount.description_original == SHORT

    status = result["status"]
    assert status.description == "Сжато: status"
    assert status.description_source == "source_compressed"
    assert status.description_original == LONG

    for name in ("region", "day"):
        assert result[name].description == f"Описание {name}"
        assert result[name].description_source == "generated"
    assert all(row.aliases == [f"{row.column} алиас"] and row.fingerprint for row in rows)

    assert llm.columns("compress") == [["status"]]
    assert llm.columns("aliases") == [["amount", "status", "region", "day"]]
    sent = {item["column"]: item for item in llm.calls[1][1]}
    assert sent["amount"]["description"] == SHORT
    assert sent["status"]["description"] == "Сжато: status"
    assert sent["region"]["description"] is None
    assert sent["region"]["top_values"] == ["msk", "spb"]
    assert sent["day"]["min"] == "2024-01-05"

    assert ProfileMemory(db).load_enrichment("orders") == rows
    assert ProfileMemory(db).load_profile("orders").row_count == 3


async def test_short_description_is_kept_verbatim(db) -> None:
    padded = "  " + "Сумма, ₽ " * 22
    assert len(padded) <= 200

    def answer(kind, columns):
        reply = auto_answer(kind, columns)
        for column in reply["columns"]:
            column["description"] = "LLM переписала описание"
        return reply

    llm = FakeLLM(answer)
    rows = await SchemaEnricher(db, llm).enrich("orders", comments={"amount": padded})
    amount = by_column(rows)["amount"]
    assert amount.description == padded
    assert amount.description_source == "source"
    assert llm.columns("compress") == []


async def test_aliases_are_validated(db) -> None:
    def answer(kind, columns):
        return {
            "columns": [
                {
                    "column": "amount",
                    "description": None,
                    "aliases": [
                        "Сумма",
                        "сумма",
                        "Amount",
                        "x" * 41,
                        "Выручка",
                        "Оборот",
                        "Чек",
                        "Ещё",
                    ],
                },
                {"column": "amount", "aliases": ["Дубль ответа"]},
                {"column": "ghost", "aliases": ["Призрак"]},
                {"column": "status", "description": "Статус заказа", "aliases": []},
                {"column": "region", "description": "Регион", "aliases": ["Город"]},
                {"column": "day", "description": "День", "aliases": ["Дата"]},
            ]
        }

    rows = by_column(await SchemaEnricher(db, FakeLLM(answer)).enrich("orders", {"amount": SHORT}))
    assert rows["amount"].aliases == ["Сумма", "Выручка", "Оборот", "Чек"]
    assert rows["amount"].description == SHORT
    assert rows["status"].aliases == []
    assert rows["status"].fingerprint
    assert all(len(row.aliases) <= 4 for row in rows.values())
    assert "ghost" not in rows


async def test_compressed_description_too_long_falls_back(db) -> None:
    def answer(kind, columns):
        reply = auto_answer(kind, columns)
        if kind == "compress":
            reply["columns"][0]["description"] = "y" * 201
        return reply

    llm = FakeLLM(answer)
    rows = by_column(await SchemaEnricher(db, llm).enrich("orders", {"status": LONG}))
    status = rows["status"]
    assert status.description == LONG
    assert status.description_source == "source"
    assert status.aliases == []
    assert status.fingerprint is None
    assert llm.columns("aliases") == [["amount", "region", "day"]]


async def test_generated_description_must_be_short_and_non_empty(db) -> None:
    def answer(kind, columns):
        reply = auto_answer(kind, columns)
        reply["columns"][0]["description"] = "  "
        reply["columns"][1]["description"] = "z" * 201
        return reply

    rows = by_column(await SchemaEnricher(db, FakeLLM(answer)).enrich("orders"))
    for name in ("amount", "status"):
        assert rows[name].description is None
        assert rows[name].description_source == "none"
        assert rows[name].aliases == []
        assert rows[name].fingerprint is None
    assert rows["region"].description_source == "generated"


async def test_invalid_batch_does_not_stop_other_batches(db, monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "enrich_batch_size", 2)
    replies = iter([ValueError("not json"), None])

    def answer(kind, columns):
        return next(replies) or auto_answer(kind, columns)

    llm = FakeLLM(answer)
    with caplog.at_level(logging.WARNING):
        rows = by_column(await SchemaEnricher(db, llm).enrich("orders", {"amount": SHORT}))
    assert llm.columns("aliases") == [["amount", "status"], ["region", "day"]]
    assert rows["amount"].description == SHORT
    assert rows["amount"].description_source == "source"
    assert rows["amount"].aliases == []
    assert rows["status"].description_source == "none"
    assert rows["region"].aliases == ["region алиас"]
    assert "Невалидный ответ LLM" in caplog.text


async def test_schema_mismatch_is_a_failure(db) -> None:
    def answer(kind, columns):
        return {"columns": [{"column": "amount", "aliases": "не список"}]}

    rows = await SchemaEnricher(db, FakeLLM(answer)).enrich("orders")
    assert all(row.description_source == "none" and row.aliases == [] for row in rows)


async def test_missing_column_in_answer_fails_only_that_column(db, caplog) -> None:
    def answer(kind, columns):
        reply = auto_answer(kind, columns)
        reply["columns"] = [item for item in reply["columns"] if item["column"] != "region"]
        return reply

    with caplog.at_level(logging.WARNING):
        rows = by_column(await SchemaEnricher(db, FakeLLM(answer)).enrich("orders"))
    assert rows["region"].description_source == "none"
    assert rows["region"].fingerprint is None
    assert rows["day"].description_source == "generated"
    assert "не вернула колонки ['region']" in caplog.text


async def test_unavailable_llm_is_not_called_again(db, monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "enrich_batch_size", 1)
    llm = FakeLLM(lambda kind, columns: unavailable())
    with caplog.at_level(logging.WARNING):
        rows = by_column(
            await SchemaEnricher(db, llm).enrich("orders", {"amount": SHORT, "status": LONG})
        )
    assert len(llm.calls) == 1
    assert rows["amount"].description == SHORT
    assert rows["status"].description == LONG
    assert rows["status"].description_source == "source"
    assert rows["region"].description is None
    assert all(row.aliases == [] and row.fingerprint is None for row in rows.values())
    assert "LLM недоступна" in caplog.text
    assert ProfileMemory(db).load_profile("orders").row_count == 3


async def test_next_run_after_failure_retries(db) -> None:
    await SchemaEnricher(db, FakeLLM(lambda kind, columns: unavailable())).enrich("orders")
    llm = FakeLLM()
    enricher = SchemaEnricher(db, llm)
    rows = await enricher.enrich("orders")
    assert llm.columns("aliases") == [["amount", "status", "region", "day"]]
    assert all(row.description_source == "generated" for row in rows)
    assert enricher.llm_available


async def test_batches(db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "enrich_batch_size", 2)
    db.execute("ALTER TABLE orders ADD COLUMN channel VARCHAR DEFAULT 'web'")
    llm = FakeLLM()
    comments = {"amount": LONG, "status": LONG, "region": LONG}
    await SchemaEnricher(db, llm).enrich("orders", comments)
    assert llm.columns("compress") == [["amount", "status"], ["region"]]
    assert llm.columns("aliases") == [["amount", "status"], ["region", "day"], ["channel"]]


async def test_repeated_enrich_skips_llm_and_refreshes_stats(db) -> None:
    comments = {"amount": SHORT, "status": LONG}
    glossary = {"чек": "заказ"}
    first = await SchemaEnricher(db, FakeLLM()).enrich("orders", comments, glossary)
    db.execute("INSERT INTO orders VALUES (7.0, 1, 'kzn', DATE '2024-04-05')")
    llm = FakeLLM()
    second = await SchemaEnricher(db, llm).enrich("orders", comments)
    assert llm.calls == []
    assert second == first
    assert ProfileMemory(db).load_profile("orders").row_count == 4


async def test_only_changed_columns_are_reprocessed(db) -> None:
    comments = {"amount": SHORT, "status": LONG}
    first = by_column(await SchemaEnricher(db, FakeLLM()).enrich("orders", comments))

    db.execute("ALTER TABLE orders ALTER status TYPE BIGINT")
    llm = FakeLLM()
    rows = by_column(await SchemaEnricher(db, llm).enrich("orders", comments))
    assert llm.columns("compress") == [["status"]]
    assert llm.columns("aliases") == [["status"]]
    assert rows["amount"] == first["amount"]

    llm = FakeLLM()
    changed = comments | {"amount": "Сумма без НДС"}
    rows = by_column(await SchemaEnricher(db, llm).enrich("orders", changed))
    assert llm.columns("compress") == []
    assert llm.columns("aliases") == [["amount"]]
    assert rows["amount"].description == "Сумма без НДС"

    db.execute("ALTER TABLE orders DROP COLUMN region")
    llm = FakeLLM()
    rows = await SchemaEnricher(db, llm).enrich("orders", changed)
    assert llm.calls == []
    assert [row.column for row in rows] == ["amount", "status", "day"]
    stored = db.query("SELECT column_name FROM _meta.column_passport ORDER BY position")
    assert [row["column_name"] for row in stored] == ["amount", "status", "day"]


async def test_glossary_change_reprocesses_and_reaches_prompt(db) -> None:
    await SchemaEnricher(db, FakeLLM()).enrich("orders", glossary={"чек": "заказ"})
    llm = FakeLLM()
    await SchemaEnricher(db, llm).enrich("orders", glossary={"чек": "заказ", "выручка": "amount"})
    assert llm.columns("aliases") == [["amount", "status", "region", "day"]]
    assert '"выручка": "amount"' in llm.prompts[0]
    assert ProfileMemory(db).load_glossary("orders") == {"чек": "заказ", "выручка": "amount"}


async def test_long_values_are_trimmed_in_prompt(db) -> None:
    db.execute("CREATE TABLE notes AS SELECT repeat('й', 500) || i AS body FROM range(10) t(i)")
    llm = FakeLLM()
    await SchemaEnricher(db, llm).enrich("notes")
    sent = llm.calls[0][1][0]
    assert len(sent["top_values"]) == 5
    assert all(len(value) == 60 for value in sent["top_values"])
    assert len(sent["min"]) == 60


async def test_empty_table(db) -> None:
    db.execute("CREATE TABLE nothing (amount DOUBLE, day DATE)")
    rows = await SchemaEnricher(db, FakeLLM()).enrich("nothing")
    assert [row.description_source for row in rows] == ["generated", "generated"]
    assert ProfileMemory(db).load_profile("nothing").row_count == 0


def test_default_llm_client(db) -> None:
    assert isinstance(SchemaEnricher(db).llm, LLMClient)
