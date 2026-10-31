import asyncio
import logging
import sqlite3
import threading
import time
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from app import main
from app.api.deps import get_db
from app.api.routes import datasources
from app.config import settings
from app.core.data import ingest, restore
from app.core.data.connectors.base import BaseConnector
from app.core.data.connectors.clickhouse import ClickHouseConnector
from app.core.data.connectors.postgres import PostgresConnector
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.profiling import profile_table
from app.core.memory.datasource_store import DatasourceRecord, DatasourceStore
from app.core.memory.profile_memory import ProfileMemory
from app.main import app

SECRET = "TopSecret!42"
DSN = f"postgresql://analyst:{SECRET}@db.local:5432/shop?sslmode=disable"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(restore, "ENV_FILES", ())
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    yield manager
    manager.close()


@pytest.fixture
def store() -> DatasourceStore:
    return DatasourceStore()


def record(identity: str = "pg_one", dsn: str = "postgresql://analyst@db.local/shop", **extra):
    return DatasourceRecord(identity=identity, kind="postgres", dsn=dsn, **extra)


def test_store_upserts_by_identity(store: DatasourceStore) -> None:
    store.save(record("pg_one", db_schema="public", description="старое", created_at=1.0))
    store.save(record("pg_two", created_at=2.0))
    store.save(record("pg_one", db_schema="sales", description="новое", created_at=3.0))
    assert [item.identity for item in store.list()] == ["pg_two", "pg_one"]
    saved = store.get("pg_one")
    assert (saved.db_schema, saved.description, saved.created_at) == ("sales", "новое", 3.0)
    assert store.get("pg_missing") is None
    assert store.delete("pg_one") is True
    assert store.delete("pg_one") is False
    assert [item.identity for item in store.list()] == ["pg_two"]


def test_store_lives_in_sqlite_path(store: DatasourceStore) -> None:
    store.save(record())
    with sqlite3.connect(settings.sqlite_path) as connection:
        rows = connection.execute("SELECT identity, kind FROM datasources").fetchall()
    assert rows == [("pg_one", "postgres")]


def test_remember_keeps_password_off_disk(store: DatasourceStore) -> None:
    connector = PostgresConnector(DSN, schema="sales")
    restore.remember(connector, "описание", store)
    saved = store.get(connector.identity)
    assert saved.kind == "postgres"
    assert saved.dsn == "postgresql://analyst:***@db.local:5432/shop"
    assert (saved.db_schema, saved.description) == ("sales", "описание")
    assert SECRET.encode() not in Path(settings.sqlite_path).read_bytes()


def test_remember_keeps_clickhouse_without_password(store: DatasourceStore) -> None:
    connector = ClickHouseConnector(f"clickhouse://analyst:{SECRET}@ch.local:9000/sales")
    restore.remember(connector, None, store)
    saved = store.get(connector.identity)
    assert (saved.kind, saved.db_schema) == ("clickhouse", None)
    assert saved.dsn == "clickhouse://analyst:***@ch.local:9000/sales"
    assert SECRET.encode() not in Path(settings.sqlite_path).read_bytes()


def test_restore_skips_clickhouse(db, store, connectors) -> None:
    store.save(
        DatasourceRecord(
            identity="ch_0123456789", kind="clickhouse", dsn="clickhouse://analyst@ch.local:9000/x"
        )
    )
    assert asyncio.run(restore.restore_saved_sources(db, store)) == []
    assert connectors["calls"] == []


def test_remember_skips_other_connectors(store: DatasourceStore) -> None:
    class Other(BaseConnector):
        def connect(self, db) -> list[str]:
            return []

    restore.remember(Other(), None, store)
    assert store.list() == []


def test_password_comes_from_environment(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, backend = tmp_path / "root.env", tmp_path / "backend.env"
    root.write_text("POSTGRES_PASSWORD=from-root\nPOSTGRES_PASSWORD__pg_file=root-file\n")
    backend.write_text("POSTGRES_PASSWORD__pg_file=backend-file\n")
    monkeypatch.setattr(restore, "ENV_FILES", (root, backend))
    assert restore.password_for("pg_other") == "from-root"
    assert restore.password_for("pg_file") == "backend-file"
    monkeypatch.setenv("POSTGRES_PASSWORD", "general")
    assert restore.password_for("pg_other") == "general"
    assert restore.password_for("pg_file") == "backend-file"
    monkeypatch.setenv("POSTGRES_PASSWORD__pg_file", "own")
    assert restore.password_for("pg_file") == "own"


def test_password_is_missing() -> None:
    assert restore.password_for("pg_none") is None


def test_connection_string_restores_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    original = PostgresConnector(DSN, schema="sales")
    saved = record(original.identity, original.dsn.masked(), db_schema="sales")
    assert restore.connection_string(saved) is None
    monkeypatch.setenv(f"POSTGRES_PASSWORD__{original.identity}", "it's new")
    rebuilt = PostgresConnector(restore.connection_string(saved), schema="sales")
    assert rebuilt.identity == original.identity
    assert rebuilt.dsn.password == "it's new"
    assert rebuilt.dsn.params["user"] == "analyst"


def test_connection_string_without_password() -> None:
    saved = record()
    assert restore.connection_string(saved) == saved.dsn


class FakeConnector(BaseConnector):
    def __init__(self, table: str, failure: Exception | None = None, delay: float = 0) -> None:
        self.table = table
        self.failure = failure
        self.delay = delay
        self.connects = 0

    def connect(self, db) -> list[str]:
        self.connects += 1
        time.sleep(self.delay)
        if self.failure is not None:
            raise self.failure
        db.execute(f"CREATE OR REPLACE TABLE {self.table} AS SELECT 1 AS id, 'x' AS name")
        return [self.table]


@pytest.fixture
def profiled(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls = []

    def counting(db, table, approx=True):
        calls.append(table)
        return profile_table(db, table, approx=approx)

    monkeypatch.setattr(ingest, "profile_table", counting)
    return calls


def test_ingest_restore_keeps_matching_passport(db, profiled) -> None:
    connector = FakeConnector("orders")
    ingest.attach(connector, db)
    profiled_at = ProfileMemory(db).load_profile("orders").profiled_at
    assert ingest.restore(connector, db) == ["orders"]
    assert connector.connects == 2
    assert profiled == ["orders"]
    assert ProfileMemory(db).load_profile("orders").profiled_at == profiled_at


def test_ingest_restore_profiles_changed_or_new_tables(db, profiled) -> None:
    ingest.attach(FakeConnector("orders"), db)
    db.execute("CREATE OR REPLACE TABLE orders AS SELECT 1 AS id")
    memory = ProfileMemory(db)

    class Changed(BaseConnector):
        def connect(self, db) -> list[str]:
            db.execute("CREATE OR REPLACE TABLE orders AS SELECT 1 AS id, 2 AS total")
            db.execute("CREATE OR REPLACE TABLE users AS SELECT 1 AS id")
            return ["orders", "users"]

    assert ingest.restore(Changed(), db) == ["orders", "users"]
    assert profiled == ["orders", "orders", "users"]
    assert [column.name for column in memory.load_profile("orders").columns] == ["id", "total"]
    assert memory.load_profile("users") is not None


class GuardedConnector(BaseConnector):
    """Вторая таблица без прав на SELECT; skip и rollback записываются."""

    def __init__(self, skippable: bool) -> None:
        self.skippable = skippable
        self.skipped: list[str] = []
        self.rolled_back = False

    def connect(self, db) -> list[str]:
        db.execute("CREATE OR REPLACE TABLE orders AS SELECT 1 AS id")
        db.execute("CREATE OR REPLACE TABLE users AS SELECT 1 AS id")
        return ["orders", "users"]

    def skip(self, db, table: str, error: Exception) -> bool:
        if not self.skippable:
            return False
        self.skipped.append(table)
        db.execute(f"DROP TABLE {table}")
        return True

    def rollback(self, db) -> None:
        self.rolled_back = True
        db.execute("DROP TABLE IF EXISTS orders")
        db.execute("DROP TABLE IF EXISTS users")


def denied(db, table, approx=True):
    if table == "users":
        raise duckdb.PermissionException("permission denied for table users")
    return profile_table(db, table, approx=approx)


def test_ingest_restore_skips_unreadable_table(db, monkeypatch) -> None:
    memory = ProfileMemory(db)
    db.execute("CREATE TABLE users AS SELECT 1 AS old")
    memory.save_profile(profile_table(db, "users"))
    monkeypatch.setattr(ingest, "profile_table", denied)
    connector = GuardedConnector(skippable=True)
    assert ingest.restore(connector, db) == ["orders"]
    assert connector.skipped == ["users"]
    assert not connector.rolled_back
    assert db.user_tables() == ["orders"]
    assert memory.load_profile("orders") is not None
    assert memory.load_profile("users") is None


def test_ingest_restore_rolls_back_on_read_error(db, monkeypatch) -> None:
    monkeypatch.setattr(ingest, "profile_table", denied)
    connector = GuardedConnector(skippable=False)
    with pytest.raises(duckdb.Error):
        ingest.restore(connector, db)
    assert connector.rolled_back
    assert db.user_tables() == []


@pytest.fixture
def connectors(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeConnector]:
    built: dict[str, FakeConnector] = {}
    calls = []

    def build(kind, dsn, schema=None):
        calls.append((kind, dsn, schema))
        return built[schema]

    monkeypatch.setattr(restore, "build_connector", build)
    built["calls"] = calls
    return built


def test_restore_skips_failures_and_missing_passwords(
    db, store, connectors, profiled, monkeypatch, caplog
) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD__pg_ok", "pw")
    connectors["broken"] = FakeConnector("broken", RuntimeError("сервер недоступен"))
    connectors["odd"] = FakeConnector("odd", KeyError("неожиданно"))
    connectors["sales"] = FakeConnector("orders")
    store.save(record("pg_broken", db_schema="broken", created_at=1))
    store.save(record("pg_nopass", "postgresql://analyst:***@db.local/shop", created_at=2))
    store.save(record("pg_odd", db_schema="odd", created_at=3))
    store.save(
        record("pg_ok", "postgresql://analyst:***@db.local/shop", db_schema="sales", created_at=4)
    )
    with caplog.at_level(logging.WARNING, logger=restore.__name__):
        restored = asyncio.run(restore.restore_saved_sources(db, store))
    assert restored == ["pg_ok"]
    assert db.query("SELECT count(*) AS rows FROM orders") == [{"rows": 1}]
    assert profiled == ["orders"]
    assert connectors["calls"][-1][1].endswith("password=pw")
    messages = caplog.text
    assert "pg_broken" in messages and "сервер недоступен" in messages
    assert "POSTGRES_PASSWORD__pg_nopass" in messages
    assert "pg_odd" in messages and "KeyError" in messages


def test_restore_times_out(db, store, connectors, monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "postgres_restore_timeout_s", 0.2)
    connectors["slow"] = FakeConnector("slow", delay=0.6)
    store.save(record("pg_slow", db_schema="slow"))
    with caplog.at_level(logging.WARNING, logger=restore.__name__):
        assert asyncio.run(restore.restore_saved_sources(db, store)) == []
    assert "pg_slow" in caplog.text and "TimeoutError" in caplog.text


def test_restore_survives_unreadable_store(db, caplog) -> None:
    class Broken(DatasourceStore):
        def list(self):
            raise sqlite3.OperationalError("disk I/O error")

    assert asyncio.run(restore.restore_saved_sources(db, Broken())) == []
    assert "Не удалось прочитать" in caplog.text


class ReleasingConnector:
    def __init__(self) -> None:
        self.released: list[str] = []

    def previous_aliases(self, db) -> list[str]:
        return ["pg_one_aaa", "pg_one_bbb"]

    def views_of(self, db, alias: str) -> list[str]:
        return [f"view_{alias[-3:]}"]

    def release(self, db, alias: str) -> None:
        self.released.append(alias)


def test_forget_releases_aliases_and_passports(db, store, monkeypatch) -> None:
    connector = ReleasingConnector()
    monkeypatch.setattr(restore, "build_connector", lambda kind, dsn, schema: connector)
    ingest.attach(FakeConnector("view_aaa"), db)
    store.save(record())
    assert restore.forget(db, "pg_one", store) == ["view_aaa", "view_bbb"]
    assert connector.released == ["pg_one_aaa", "pg_one_bbb"]
    assert ProfileMemory(db).load_profile("view_aaa") is None
    assert store.list() == []
    assert restore.forget(db, "pg_one", store) is None


def test_forget_finds_aliases_of_real_connector(db, store) -> None:
    connector = PostgresConnector(DSN)
    restore.remember(connector, None, store)
    assert restore.forget(db, connector.identity, store) == []
    assert store.get(connector.identity) is None


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_attach_db_remembers_postgres_source(client, store, monkeypatch) -> None:
    class Attached(PostgresConnector):
        def connect(self, db) -> list[str]:
            db.execute("CREATE OR REPLACE TABLE orders AS SELECT 1 AS id")
            return ["orders"]

    async def summarize(db, tables, llm=None) -> None:
        return None

    monkeypatch.setattr(ingest, "summarize", summarize)
    monkeypatch.setattr(
        datasources, "build_connector", lambda kind, dsn, schema: Attached(dsn, schema)
    )
    response = client.post(
        "/api/datasources/db",
        json={"kind": "postgres", "dsn": DSN, "schema": "sales", "description": "док"},
    )
    assert response.status_code == 200
    saved = store.get(response.json()["datasource_id"])
    assert (saved.db_schema, saved.description) == ("sales", "док")
    assert SECRET not in saved.dsn


def test_delete_endpoint(client, store, monkeypatch) -> None:
    connector = ReleasingConnector()
    monkeypatch.setattr(restore, "build_connector", lambda kind, dsn, schema: connector)
    store.save(record())
    assert client.delete("/api/datasources/pg_one").status_code == 204
    assert connector.released == ["pg_one_aaa", "pg_one_bbb"]
    response = client.delete("/api/datasources/pg_one")
    assert response.status_code == 404
    assert "pg_one" in response.json()["detail"]


def test_lifespan_restores_in_background(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "duckdb_path", str(tmp_path / "main.duckdb"))
    started = threading.Event()
    seen = []

    async def hanging(db):
        seen.append(db)
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(main, "restore_saved_sources", hanging)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert started.wait(5)
        assert seen == [app.state.db]


def test_columns_prefer_temp_view(db) -> None:
    db.execute("CREATE TABLE orders AS SELECT 1 AS a, 2 AS b")
    db.execute("CREATE TEMP VIEW orders AS SELECT 3 AS z")
    db.execute("CREATE TEMP VIEW users AS SELECT 1 AS id, 'x' AS name")
    db.execute("CREATE SCHEMA _meta")
    db.execute("CREATE TABLE _meta.users AS SELECT 1 AS hidden")
    assert db.columns("orders") == ["z"]
    assert db.columns("users") == ["id", "name"]
    assert db.columns("missing") == []
