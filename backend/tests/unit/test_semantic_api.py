"""CRUD semantic model через API датасорсов."""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.data.duckdb_manager import DuckDBManager
from app.main import app


@pytest.fixture
def client():
    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE sales AS SELECT 1 AS amount, DATE '2024-01-01' AS event_date")
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()
    db.close()


def payload(version: int = 1) -> dict:
    return {
        "table": "sales",
        "version": version,
        "metrics": [
            {
                "name": "revenue",
                "title": "Выручка",
                "aggregation": "SUM",
                "column": "amount",
            }
        ],
        "dimensions": [
            {
                "name": "month",
                "column": "event_date",
                "kind": "temporal",
                "time_grains": ["month"],
            }
        ],
        "default_time_dimension": "month",
    }


def test_semantic_model_crud(client: TestClient) -> None:
    assert client.get("/api/datasources/tables/sales/semantic-model").status_code == 404
    created = client.put("/api/datasources/tables/sales/semantic-model", json=payload())
    assert created.status_code == 200
    assert created.json()["version"] == 1

    loaded = client.get("/api/datasources/tables/sales/semantic-model")
    assert loaded.status_code == 200
    assert loaded.json()["metrics"][0]["name"] == "revenue"

    updated = client.put("/api/datasources/tables/sales/semantic-model", json=payload())
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    assert client.delete("/api/datasources/tables/sales/semantic-model").status_code == 204
    assert client.get("/api/datasources/tables/sales/semantic-model").status_code == 404


def test_invalid_semantic_model_is_rejected(client: TestClient) -> None:
    body = payload()
    body["metrics"][0]["column"] = "nope"
    response = client.put("/api/datasources/tables/sales/semantic-model", json=body)
    assert response.status_code == 422
    assert "nope" in response.json()["detail"]
