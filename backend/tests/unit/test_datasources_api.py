import threading
import time
from concurrent.futures import ThreadPoolExecutor

import duckdb
import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.routes import datasources
from app.api.schemas.datasource import DatasourceCreate
from app.config import settings
from app.core.data import ingest
from app.core.data.connectors.base import BaseConnector
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.profile_memory import ProfileMemory
from app.main import app


@pytest.fixture
def client():
    db = DuckDBManager(":memory:")
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()
    db.close()


def test_lifespan_opens_shared_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "storage" / "duckdb" / "main.duckdb"
    monkeypatch.setattr(settings, "duckdb_path", str(path))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert isinstance(app.state.db, DuckDBManager)
        assert app.state.db.query("SELECT 1 AS one") == [{"one": 1}]
    assert path.exists()


class RecordingConnector(BaseConnector):
    identity = "pg_recorded"

    def __init__(self) -> None:
        self.guard = threading.Lock()
        self.active = 0
        self.peak = 0
        self.databases: list = []

    def connect(self, db) -> list[str]:
        with self.guard:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.databases.append(db)
        time.sleep(0.05)
        db.execute("CREATE OR REPLACE TABLE orders AS SELECT 1 AS id")
        with self.guard:
            self.active -= 1
        return ["orders"]


def test_endpoint_uses_shared_db_from_lifespan(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "duckdb_path", str(tmp_path / "main.duckdb"))
    connector = RecordingConnector()
    monkeypatch.setattr(datasources, "build_connector", lambda kind, dsn, schema: connector)
    with TestClient(app) as client:
        response = client.post(
            "/api/datasources/db", json={"kind": "postgres", "dsn": "host=x", "schema": "s"}
        )
        assert connector.databases == [app.state.db]
    assert response.status_code == 200
    assert response.json()["datasource_id"] == "pg_recorded"
    assert response.json()["tables"] == ["orders"]


def test_concurrent_attach_requests_are_serialised(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = RecordingConnector()
    monkeypatch.setattr(datasources, "build_connector", lambda kind, dsn, schema: connector)
    db = DuckDBManager(":memory:")
    payload = DatasourceCreate(kind="postgres", dsn="host=x")
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(lambda _: datasources.attach_db(payload, db, BackgroundTasks()), range(4))
            )
    finally:
        db.close()
    assert [result.tables for result in results] == [["orders"]] * 4
    assert connector.peak == 1


def test_invalid_dsn_is_400(client: TestClient) -> None:
    response = client.post(
        "/api/datasources/db", json={"kind": "postgres", "dsn": "postgres://user:TopSecret@/db"}
    )
    assert response.status_code == 400
    assert "хост" in response.json()["detail"]
    assert "TopSecret" not in response.text


def test_unknown_kind_is_422(client: TestClient) -> None:
    response = client.post("/api/datasources/db", json={"kind": "mysql", "dsn": "x"})
    assert response.status_code == 422


def test_clickhouse_is_501(client: TestClient) -> None:
    response = client.post(
        "/api/datasources/db", json={"kind": "clickhouse", "dsn": "clickhouse://localhost"}
    )
    assert response.status_code == 501


def test_unreachable_postgres_is_502(client: TestClient) -> None:
    response = client.post(
        "/api/datasources/db",
        json={"kind": "postgres", "dsn": "postgresql://user:TopSecret@127.0.0.1:1/db"},
    )
    assert response.status_code in (502, 503)
    assert "TopSecret" not in response.text


def test_missing_extension_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def offline(self, sql: str, params: dict | None = None) -> None:
        raise duckdb.IOException("offline")

    monkeypatch.setattr(DuckDBManager, "execute", offline)
    response = client.post(
        "/api/datasources/db", json={"kind": "postgres", "dsn": "postgresql://u@localhost/db"}
    )
    assert response.status_code == 503
    assert "postgres_scanner" in response.json()["detail"]


@pytest.fixture
def storage(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    return tmp_path / "uploads"


def test_csv_upload_attaches_every_file(client, storage) -> None:
    export = "Дата;Сумма\n01.02.2024;1 234,56\n02.02.2024;7,5\n".encode("cp1251")
    response = client.post(
        "/api/datasources/csv",
        files=[
            ("files", ("Продажи.csv", export, "text/csv")),
            ("files", ("users.csv", b"id,name\n1,Ann\n", "text/csv")),
            ("files", ("users.csv", b"id,name\n2,Bob\n", "text/csv")),
        ],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "csv"
    assert body["tables"] == ["prodazhi", "users", "users_2"]
    folder = storage / body["datasource_id"]
    assert (folder / "prodazhi.csv").read_bytes() == export
    db = app.dependency_overrides[get_db]()
    assert db.query('SELECT sum("Сумма") AS total FROM prodazhi') == [{"total": 1242.06}]
    assert db.query("SELECT name FROM users_2") == [{"name": "Bob"}]


def test_csv_upload_rejects_empty_file_and_cleans_up(client, storage) -> None:
    response = client.post(
        "/api/datasources/csv",
        files=[
            ("files", ("good.csv", b"a\n1\n", "text/csv")),
            ("files", ("empty.csv", b"", "text/csv")),
        ],
    )
    assert response.status_code == 400
    assert "пуст" in response.json()["detail"]
    assert list(storage.iterdir()) == []
    db = app.dependency_overrides[get_db]()
    assert db.query("SELECT table_name FROM duckdb_tables()") == []
    assert db.query("SELECT view_name FROM duckdb_views() WHERE NOT internal") == []


def test_csv_upload_enforces_size_limit(client, storage, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "csv_max_upload_mb", 1)
    monkeypatch.setattr(datasources, "UPLOAD_CHUNK", 1024)
    payload = b"a\n" + b"1\n" * (1024 * 1024)
    response = client.post(
        "/api/datasources/csv", files=[("files", ("big.csv", payload, "text/csv"))]
    )
    assert response.status_code == 413
    assert "CSV_MAX_UPLOAD_MB" in response.json()["detail"]
    assert list(storage.iterdir()) == []


def test_csv_upload_without_files_is_rejected(client, storage) -> None:
    assert client.post("/api/datasources/csv").status_code == 422


def test_csv_upload_profiles_tables_at_once(client, storage, monkeypatch) -> None:
    calls = []

    async def describe(connector, db, tables, document=None, llm=None):
        calls.append((tables, document, ProfileMemory(db).load_profile(tables[0]).row_count))
        return {}

    monkeypatch.setattr(ingest, "describe", describe)
    response = client.post(
        "/api/datasources/csv", files=[("files", ("orders.csv", b"id,amount\n1,10\n2,20\n"))]
    )
    assert response.status_code == 200
    assert calls == [(["orders"], None, 2)]


def test_csv_description_reaches_enricher(client, storage, monkeypatch) -> None:
    calls = []

    async def describe(connector, db, tables, document=None, llm=None):
        calls.append((tables, document))
        return {}

    monkeypatch.setattr(ingest, "describe", describe)
    response = client.post(
        "/api/datasources/csv",
        files=[
            ("files", ("orders.csv", b"id,amount\n1,10\n")),
            ("description", ("README.md", "- amount — сумма заказа".encode(), "text/markdown")),
        ],
    )
    assert response.status_code == 200
    assert calls == [(["orders"], "- amount — сумма заказа")]


def test_csv_without_description_does_not_build_enricher(client, storage, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("энричер без описания не нужен")

    monkeypatch.setattr(ingest, "SchemaEnricher", forbidden)
    response = client.post("/api/datasources/csv", files=[("files", ("orders.csv", b"id\n1\n"))])
    assert response.status_code == 200


def test_failed_description_does_not_fail_attach(client, storage, monkeypatch, caplog) -> None:
    async def broken(connector, db, tables, document=None, llm=None):
        raise RuntimeError("LLM упала")

    monkeypatch.setattr(ingest, "describe", broken)
    response = client.post(
        "/api/datasources/csv",
        files=[("files", ("orders.csv", b"id\n1\n")), ("description", ("d.md", b"doc"))],
    )
    assert response.status_code == 200
    assert "не перенесено в паспорт" in caplog.text


def test_too_large_description_is_413(client, storage) -> None:
    response = client.post(
        "/api/datasources/csv",
        files=[
            ("files", ("orders.csv", b"id\n1\n")),
            ("description", ("d.md", b"x" * (datasources.DESCRIPTION_MAX_BYTES + 1))),
        ],
    )
    assert response.status_code == 413


def test_db_description_reaches_enricher(monkeypatch) -> None:
    connector = RecordingConnector()
    monkeypatch.setattr(datasources, "build_connector", lambda kind, dsn, schema: connector)
    db = DuckDBManager(":memory:")
    background = BackgroundTasks()
    payload = DatasourceCreate(kind="postgres", dsn="host=x", description="orders — заказы")
    try:
        datasources.attach_db(payload, db, background)
        assert ProfileMemory(db).load_profile("orders").row_count == 1
    finally:
        db.close()
    [task] = background.tasks
    assert task.args == (connector, db, ["orders"], "orders — заказы")


class UnreadableConnector(RecordingConnector):
    """Подключение удалось, а профиль упал — как SELECT по таблице без прав."""

    def __init__(self) -> None:
        super().__init__()
        self.rolled_back = False

    def connect(self, db) -> list[str]:
        db.execute("CREATE OR REPLACE TEMP VIEW orders AS SELECT 1 AS id")
        return ["orders"]

    def rollback(self, db) -> None:
        self.rolled_back = True
        db.execute("DROP VIEW IF EXISTS temp.main.orders")

    def scrub(self, text: str) -> str:
        return text.replace("TopSecret", "***")


def test_db_profile_failure_rolls_back_and_is_422(client, monkeypatch) -> None:
    connector = UnreadableConnector()
    monkeypatch.setattr(datasources, "build_connector", lambda kind, dsn, schema: connector)

    def broken(db, table, **kwargs):
        raise duckdb.IOException("Out of Memory near TopSecret")

    monkeypatch.setattr(ingest, "profile_table", broken)
    response = client.post("/api/datasources/db", json={"kind": "postgres", "dsn": "host=x"})
    assert response.status_code == 422
    assert "Не удалось прочитать таблицы источника" in response.json()["detail"]
    assert "TopSecret" not in response.text
    assert connector.rolled_back
    assert client.get("/api/datasources/tables").json() == []


def test_db_too_large_description_is_413(client, monkeypatch) -> None:
    connector = RecordingConnector()
    monkeypatch.setattr(datasources, "build_connector", lambda kind, dsn, schema: connector)
    response = client.post(
        "/api/datasources/db",
        json={"kind": "postgres", "dsn": "host=x", "description": "я" * (1 << 20)},
    )
    assert response.status_code == 413
    assert connector.databases == []
