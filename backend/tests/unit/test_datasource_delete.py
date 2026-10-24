"""DELETE /api/datasources/{id} для CSV-источника (#146)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.profile_memory import ProfileMemory
from app.main import app


def open_client(db: DuckDBManager) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    yield manager
    app.dependency_overrides.clear()
    manager.close()


@pytest.fixture
def client(db) -> TestClient:
    return open_client(db)


def uploads() -> Path:
    return Path(settings.storage_dir, "uploads")


def upload(client: TestClient, *files: tuple[str, bytes]) -> dict:
    response = client.post(
        "/api/datasources/csv", files=[("files", (name, data, "text/csv")) for name, data in files]
    )
    assert response.status_code == 200
    return response.json()


def test_delete_csv_source_removes_tables_passports_and_files(client, db) -> None:
    body = upload(client, ("orders.csv", b"id,amount\n1,10\n"), ("users.csv", b"id\n1\n"))
    source = body["datasource_id"]
    assert (uploads() / source).is_dir()

    assert client.delete(f"/api/datasources/{source}").status_code == 204

    assert client.get("/api/datasources/tables").json() == []
    memory = ProfileMemory(db)
    assert memory.load_profile("orders") is None
    assert memory.load_profile("users") is None
    assert db.query("SELECT count(*) AS n FROM _meta.column_passport") == [{"n": 0}]
    assert not (uploads() / source).exists()
    response = client.delete(f"/api/datasources/{source}")
    assert response.status_code == 404
    assert source in response.json()["detail"]


def test_delete_csv_source_keeps_other_sources(client) -> None:
    first = upload(client, ("orders.csv", b"id\n1\n"), ("users.csv", b"id\n1\n"))
    second = upload(client, ("users.csv", b"id\n2\n"), ("items.csv", b"sku\nA\n"))

    assert client.delete(f"/api/datasources/{first['datasource_id']}").status_code == 204

    # users перезагружена вторым источником и принадлежит ему.
    assert client.get("/api/datasources/tables").json() == ["items", "users"]
    assert (uploads() / second["datasource_id"]).is_dir()
    assert client.delete(f"/api/datasources/{second['datasource_id']}").status_code == 204
    assert client.get("/api/datasources/tables").json() == []


def test_deleted_csv_source_does_not_return_after_restart(tmp_path) -> None:
    path = str(tmp_path / "main.duckdb")
    db = DuckDBManager(path)
    client = open_client(db)
    source = upload(client, ("orders.csv", b"id\n1\n"))["datasource_id"]
    kept = upload(client, ("users.csv", b"id\n1\n"))["datasource_id"]
    assert client.delete(f"/api/datasources/{source}").status_code == 204
    db.close()

    reopened = DuckDBManager(path)
    try:
        assert reopened.user_tables() == ["users"]
        assert ProfileMemory(reopened).load_profile("orders") is None
        assert ProfileMemory(reopened).load_profile("users") is not None
        assert sorted(folder.name for folder in uploads().iterdir()) == [kept]
    finally:
        app.dependency_overrides.clear()
        reopened.close()


def test_delete_legacy_csv_source_without_id_in_comment(client, db) -> None:
    folder = uploads() / "csv_0123456789"
    folder.mkdir(parents=True)
    (folder / "orders.csv").write_bytes(b"id\n1\n")
    db.execute("CREATE TABLE orders AS SELECT 1 AS id")
    db.execute("COMMENT ON TABLE orders IS 'csv:orders.csv'")

    assert client.delete("/api/datasources/csv_0123456789").status_code == 204

    assert client.get("/api/datasources/tables").json() == []
    assert not folder.exists()


def test_legacy_file_shared_with_other_source_is_kept(client, db) -> None:
    for name in ("csv_aaaaaaaaaa", "csv_bbbbbbbbbb"):
        (uploads() / name).mkdir(parents=True)
        (uploads() / name / "orders.csv").write_bytes(b"id\n1\n")
    db.execute("CREATE TABLE orders AS SELECT 1 AS id")
    db.execute("COMMENT ON TABLE orders IS 'csv:orders.csv'")

    assert client.delete("/api/datasources/csv_aaaaaaaaaa").status_code == 204

    assert client.get("/api/datasources/tables").json() == ["orders"]
    assert not (uploads() / "csv_aaaaaaaaaa").exists()


@pytest.mark.parametrize("identity", ["csv_feedfeed00", "csv_..", "orders"])
def test_unknown_source_is_404(client, db, identity) -> None:
    db.execute("CREATE TABLE orders AS SELECT 1 AS id")
    response = client.delete(f"/api/datasources/{identity}")
    assert response.status_code == 404
    assert client.get("/api/datasources/tables").json() == ["orders"]
