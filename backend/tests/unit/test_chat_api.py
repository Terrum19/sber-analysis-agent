from fastapi.testclient import TestClient

from app.main import app


def test_chat_stream_forbids_proxy_buffering() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"session_id": "s1", "question": "Что в данных?"},
        headers={"Accept-Encoding": "gzip"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert "content-encoding" not in response.headers
    assert response.text.startswith("data: ")
