import pytest

from app.core.data import ingest
from app.core.data.connectors.base import BaseConnector
from app.core.data.connectors.csv import CSVConnector
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.profile_memory import ProfileMemory


class CommentedConnector(BaseConnector):
    """Источник с комментариями колонок, как PostgreSQL с COMMENT ON COLUMN."""

    def __init__(self, comments: dict[str, dict[str, str]]) -> None:
        self.comments = comments

    def connect(self, db) -> list[str]:
        for table in self.comments:
            db.execute(f"CREATE TABLE {table} AS SELECT 1 AS id, 2.5 AS amount")
        return list(self.comments)

    def column_comments(self, db, table: str) -> dict[str, str]:
        return self.comments[table]


class FakeLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def complete_structured(self, system: str, user: str, schema):
        self.calls += 1
        return schema.model_validate(
            {
                "columns": [
                    {"column": "id", "title": "Номер"},
                    {"column": "amount", "title": "Сумма"},
                ]
            }
        )


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    yield manager
    manager.close()


@pytest.fixture
def csv_files(tmp_path):
    first = tmp_path / "orders.csv"
    first.write_text("id,amount\n1,10\n2,20\n")
    second = tmp_path / "users.csv"
    second.write_text("id,name\n1,Ann\n")
    return {"orders": str(first), "users": str(second)}


def test_attach_profiles_every_table_right_after_connect(db, csv_files) -> None:
    with db.lock:
        profiles = ingest.attach(CSVConnector(csv_files), db)
    assert list(profiles) == ["orders", "users"]
    memory = ProfileMemory(db)
    assert memory.load_profile("orders") == profiles["orders"]
    assert memory.load_profile("users").row_count == 1


def test_attach_rolls_back_when_profiling_fails(db, csv_files, monkeypatch) -> None:
    real = ingest.profile_table

    def flaky(db, table, **kwargs):
        if table == "users":
            raise RuntimeError("профиль упал")
        return real(db, table, **kwargs)

    monkeypatch.setattr(ingest, "profile_table", flaky)
    with pytest.raises(RuntimeError), db.lock:
        ingest.attach(CSVConnector(csv_files), db)
    tables = db.query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    )
    assert tables == []
    assert ProfileMemory(db).load_profile("orders") is None


async def test_describe_skips_tables_without_description(db) -> None:
    connector = CommentedConnector({"orders": {"amount": "Сумма заказа"}, "users": {}})
    with db.lock:
        ingest.attach(connector, db)
    llm = FakeLLM()
    described = await ingest.describe(connector, db, ["orders", "users"], llm=llm)
    assert list(described) == ["orders"]
    assert llm.calls == 0
    amount = described["orders"][1]
    assert (amount.description, amount.description_source) == ("Сумма заказа", "source")


async def test_describe_sends_document_for_every_table(db) -> None:
    connector = CommentedConnector({"orders": {}, "users": {}})
    with db.lock:
        ingest.attach(connector, db)
    llm = FakeLLM()
    described = await ingest.describe(connector, db, ["orders", "users"], " doc ", llm)
    assert llm.calls == 2
    assert [row.title for row in described["users"]] == ["Номер", "Сумма"]
    assert await ingest.describe(connector, db, ["orders"], "   ", llm) == {}
