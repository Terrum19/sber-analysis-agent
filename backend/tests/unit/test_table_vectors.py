"""Близость вопроса к таблицам по смыслу (эмбеддинги): кэш векторов, сбои, переход к данным."""

import asyncio
from types import SimpleNamespace

import pytest

from app.config import settings
from app.core.data import ingest, relations, table_vectors
from app.core.data.connectors.csv import CSVConnector
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.relations import TableChoice
from app.core.data.table_vectors import TableVectors, cosine, table_text
from app.core.memory.profile_memory import ProfileMemory

# Оси смысла: погода, покупки, прочее.
TOPICS = {"погод": 0, "температур": 0, "холодно": 0, "заказ": 1, "покуп": 1, "выручк": 1}


class FakeEmbeddings:
    """Вектор текста — сколько слов каждой темы в нём; запоминает запросы."""

    def __init__(self, failing: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.failing = failing
        self.embeddings = self

    async def create(self, model: str, input: list[str]) -> SimpleNamespace:
        self.calls.append(input)
        if self.failing:
            raise RuntimeError("сервер эмбеддингов недоступен")
        data = [
            SimpleNamespace(index=index, embedding=_vector(text))
            for index, text in reversed(list(enumerate(input)))
        ]
        return SimpleNamespace(data=data)


def _vector(text: str) -> list[float]:
    vector = [0.0, 0.0, 0.1]
    for stem, axis in TOPICS.items():
        vector[axis] += text.lower().count(stem)
    return vector


@pytest.fixture(autouse=True)
def embed_model(monkeypatch):
    monkeypatch.setattr(settings, "embed_model", "bge-m3")
    monkeypatch.setattr(table_vectors, "_down_until", 0.0)


@pytest.fixture
def db(tmp_path):
    orders = tmp_path / "orders.csv"
    orders.write_text("order_id,total,created\n1,10,2024-01-01\n2,20,2024-01-02\n")
    weather = tmp_path / "weather.csv"
    weather.write_text("day,temp_c\n2024-01-01,-5\n2024-01-02,3\n")
    manager = DuckDBManager(":memory:")
    with manager.lock:
        ingest.attach(CSVConnector({"orders": str(orders), "weather": str(weather)}), manager)
    memory = ProfileMemory(manager)
    memory.save_display_name("orders", "Заказы покупателей")
    memory.save_display_name("weather", "Температура по дням")
    yield manager
    manager.close()


def test_table_text_names_the_data(db) -> None:
    ProfileMemory(db).save_summary("weather", "Погода за квартал.\n\nПодробности.", "x")
    text = table_text(db, "weather")
    assert text.splitlines()[:3] == ["weather", "Температура по дням", "Погода за квартал."]
    assert "Подробности" not in text
    assert text.endswith("Колонки: day, temp_c")


def test_vectors_are_cached_until_the_table_changes(db) -> None:
    client = FakeEmbeddings()
    vectors = TableVectors(db, client)
    first = asyncio.run(vectors.similarity("А погода?"))
    assert first["weather"] > first["orders"]
    assert client.calls[0] == [table_text(db, "orders"), table_text(db, "weather")]

    asyncio.run(vectors.similarity("А заказы?"))
    assert client.calls[2] == ["А заказы?"]

    # Новое название — новый вектор только этой таблицы.
    ProfileMemory(db).save_display_name("weather", "Погода")
    asyncio.run(vectors.similarity("А погода?"))
    assert client.calls[3] == [table_text(db, "weather")]


def test_no_model_or_failure_means_words_only(db, monkeypatch) -> None:
    assert asyncio.run(TableVectors(db, FakeEmbeddings(failing=True)).similarity("погода")) == {}
    # Сервер не ответил — RETRY_S его не спрашиваем, а не падаем на каждом вопросе.
    client = FakeEmbeddings()
    assert asyncio.run(TableVectors(db, client).similarity("погода")) == {}
    assert client.calls == []
    monkeypatch.setattr(table_vectors, "_down_until", 0.0)
    empty = DuckDBManager(":memory:")
    assert asyncio.run(TableVectors(empty, FakeEmbeddings()).similarity("погода")) == {}
    monkeypatch.setattr(settings, "embed_model", "")
    client = FakeEmbeddings()
    assert asyncio.run(TableVectors(db, client).similarity("погода")) == {}
    assert client.calls == []


def test_real_client_takes_llm_server_by_default(db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "embed_base_url", "")
    client = TableVectors(db).client
    assert str(client.base_url).rstrip("/") == settings.llm_base_url.rstrip("/")
    assert client.api_key == settings.llm_api_key.get_secret_value()


def test_cosine() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def similarity(db, question: str) -> dict[str, float]:
    return asyncio.run(TableVectors(db, FakeEmbeddings()).similarity(question))


def test_synonym_switches_by_meaning(db) -> None:
    """«Холодно» не совпадает словами ни с «Температура по дням», ни с day, temp_c."""
    question = "Было ли холодно в январе?"
    assert relations.switch_table(db, "orders", question) is None
    assert relations.switch_table(db, "orders", question, similarity(db, question)) == (
        TableChoice(table="weather")
    )


def test_meaning_needs_a_clear_margin(db) -> None:
    close = {"orders": 0.50, "weather": 0.50 + relations.MEANING_MARGIN / 2}
    assert relations.switch_table(db, "orders", "А в процентах?", close) is None
    far = {"orders": 0.40, "weather": 0.41 + relations.MEANING_MARGIN}
    assert relations.switch_table(db, "orders", "А в процентах?", far) == TableChoice(
        table="weather"
    )
    assert relations.switch_table(db, "orders", "А в процентах?", {"orders": 0.5}) is None


def test_meaning_breaks_a_tie_of_words_without_a_table(db) -> None:
    question = "Было ли холодно в январе?"
    assert relations.choose_table(db, question).table != "weather"
    assert relations.choose_table(db, question, similarity(db, question)) == TableChoice(
        table="weather"
    )
    # Близко по смыслу к обеим — как раньше, по словам и названиям.
    even = {"orders": 0.5, "weather": 0.5}
    assert relations.choose_table(db, question, even).options == {
        "Заказы покупателей": "orders",
        "Температура по дням": "weather",
    }
