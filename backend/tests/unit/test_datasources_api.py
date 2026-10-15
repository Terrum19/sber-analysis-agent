import threading
import time
from concurrent.futures import ThreadPoolExecutor

import duckdb
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.routes import datasources
from app.api.schemas.datasource import DatasourceCreate
from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
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


class RecordingConnector:
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
            results = list(pool.map(lambda _: datasources.attach_db(payload, db), range(4)))
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
