"""Подготовка модели на Ollama: скачивание, вариант с контекстом, не-Ollama, недоступность."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.llm import ollama_setup
from app.core.llm.client import LLMClient

ROOT = "http://ollama.test"


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_base_url", f"{ROOT}/v1")
    monkeypatch.setattr(settings, "llm_model", "qwen2.5-coder:7b")
    monkeypatch.setattr(settings, "llm_num_ctx", 16384)
    monkeypatch.setattr(ollama_setup, "_active_model", None)
    monkeypatch.setattr(ollama_setup, "_missing_model", None)
    monkeypatch.setattr(ollama_setup, "_status", ollama_setup.LLMStatus("connecting", "m"))


def fake_ollama(
    models: list[str],
    *,
    version: int = 200,
    pull: list[dict] | None = None,
    create_status: list[int] | None = None,
) -> tuple[httpx.AsyncClient, list[tuple[str, dict | None]]]:
    """Фейковый сервер Ollama; calls — (путь, тело) каждого запроса."""
    calls: list[tuple[str, dict | None]] = []
    statuses = list(create_status or [200])

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.url.path, body))
        if request.url.path == "/api/version":
            return httpx.Response(version, json={"version": "0.35.1"} if version == 200 else {})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": name} for name in models]})
        if request.url.path == "/api/pull":
            lines = "\n".join(json.dumps(event) for event in pull or [{"status": "success"}])
            return httpx.Response(200, content=lines.encode())
        if request.url.path == "/api/create":
            return httpx.Response(statuses.pop(0) if statuses else 200, json={})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def paths(calls: list[tuple[str, dict | None]]) -> list[str]:
    return [path for path, _ in calls]


def test_names() -> None:
    assert ollama_setup.variant_name("qwen2.5-coder:7b", 16384) == "qwen2.5-coder:7b-ctx16384"
    assert ollama_setup.variant_name("llama3", 8192) == "llama3:latest-ctx8192"
    assert ollama_setup.server_root("http://h:11434/v1/") == "http://h:11434"
    assert ollama_setup.server_root("http://h:11434") == "http://h:11434"


async def test_creates_context_variant_of_present_model() -> None:
    http, calls = fake_ollama(["qwen2.5-coder:7b"])
    assert await ollama_setup.prepare(http)
    assert "/api/pull" not in paths(calls)
    create = [body for path, body in calls if path == "/api/create"]
    assert create == [
        {
            "model": "qwen2.5-coder:7b-ctx16384",
            "from": "qwen2.5-coder:7b",
            "parameters": {"num_ctx": 16384},
            "stream": False,
        }
    ]
    assert ollama_setup.active_model() == "qwen2.5-coder:7b-ctx16384"
    assert ollama_setup.status()["state"] == "ok"


async def test_ready_variant_is_reused() -> None:
    http, calls = fake_ollama(["qwen2.5-coder:7b", "qwen2.5-coder:7b-ctx16384"])
    assert await ollama_setup.prepare(http)
    assert paths(calls) == ["/api/version", "/api/tags"]
    assert ollama_setup.active_model() == "qwen2.5-coder:7b-ctx16384"


async def test_pulls_missing_model_with_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float | None] = []
    original = ollama_setup._set

    def spy(state, progress=None, detail=None):
        original(state, progress, detail)
        if state == "pulling":
            seen.append(progress)

    monkeypatch.setattr(ollama_setup, "_set", spy)
    http, calls = fake_ollama(
        [],
        pull=[
            {"status": "pulling manifest"},
            {"status": "pulling a", "digest": "a", "total": 300, "completed": 0},
            {"status": "pulling b", "digest": "b", "total": 100, "completed": 100},
            {"status": "pulling a", "digest": "a", "total": 300, "completed": 300},
            {"status": "success"},
        ],
    )
    assert await ollama_setup.prepare(http)
    assert ("/api/pull", {"model": "qwen2.5-coder:7b"}) in calls
    assert seen == [0.0, 0.0, 0.25, 1.0]
    assert "/api/create" in paths(calls)
    assert ollama_setup.status()["state"] == "ok"


async def test_pull_error_is_retried_later() -> None:
    http, _ = fake_ollama([], pull=[{"error": "pull model manifest: no internet"}])
    assert not await ollama_setup.prepare(http)
    status = ollama_setup.status()
    assert status["state"] == "pulling"
    assert "no internet" in status["detail"]
    assert ollama_setup.active_model() == "qwen2.5-coder:7b"


async def test_create_falls_back_to_modelfile() -> None:
    http, calls = fake_ollama(["qwen2.5-coder:7b"], create_status=[400, 200])
    assert await ollama_setup.prepare(http)
    create = [body for path, body in calls if path == "/api/create"]
    assert create[1]["modelfile"] == "FROM qwen2.5-coder:7b\nPARAMETER num_ctx 16384"
    assert ollama_setup.active_model() == "qwen2.5-coder:7b-ctx16384"


async def test_failed_create_keeps_base_model() -> None:
    http, _ = fake_ollama(["qwen2.5-coder:7b"], create_status=[400, 500])
    assert await ollama_setup.prepare(http)
    assert ollama_setup.active_model() == "qwen2.5-coder:7b"
    assert ollama_setup.status()["state"] == "ok"


async def test_not_ollama_server_is_used_as_is() -> None:
    http, calls = fake_ollama([], version=404)
    assert await ollama_setup.prepare(http)
    assert paths(calls) == ["/api/version"]
    assert ollama_setup.active_model() == "qwen2.5-coder:7b"
    assert ollama_setup.status()["state"] == "ok"


@pytest.mark.parametrize("version", [401, 403])
async def test_server_refusing_version_is_not_ollama(version: int) -> None:
    http, calls = fake_ollama([], version=version)
    assert await ollama_setup.prepare(http)
    assert paths(calls) == ["/api/version"]
    assert ollama_setup.active_model() == "qwen2.5-coder:7b"
    assert ollama_setup.status()["state"] == "ok"


@pytest.mark.parametrize("version", [500, 502, 503])
async def test_server_error_on_version_is_retried(version: int) -> None:
    http, calls = fake_ollama(["qwen2.5-coder:7b"], version=version)
    assert not await ollama_setup.prepare(http)
    assert paths(calls) == ["/api/version"]
    assert ollama_setup.status()["state"] == "connecting"
    assert ollama_setup.active_model() == "qwen2.5-coder:7b"

    http, _ = fake_ollama(["qwen2.5-coder:7b"])
    assert await ollama_setup.prepare(http)
    assert ollama_setup.active_model() == "qwen2.5-coder:7b-ctx16384"


async def test_unknown_model_is_missing_and_not_pulled_again() -> None:
    models: list[str] = []
    error = "pull model manifest: file does not exist"
    http, calls = fake_ollama(models, pull=[{"status": "pulling manifest"}, {"error": error}])
    assert not await ollama_setup.prepare(http)
    assert ollama_setup.status()["state"] == "missing"
    assert ollama_setup.status()["detail"] == error

    calls.clear()
    assert not await ollama_setup.prepare(http)
    assert paths(calls) == ["/api/version", "/api/tags"]
    assert ollama_setup.status()["state"] == "missing"
    assert ollama_setup.status()["detail"] == error

    # Модель создали или скачали на сервере вручную — подготовка продолжается.
    models.append("qwen2.5-coder:7b")
    assert await ollama_setup.prepare(http)
    assert ollama_setup.active_model() == "qwen2.5-coder:7b-ctx16384"


async def test_unknown_model_with_error_status_is_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.35.1"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(500, json={"error": "pull model manifest: file does not exist"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert not await ollama_setup.prepare(http)
    assert ollama_setup.status()["state"] == "missing"


async def test_pull_server_error_is_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.35.1"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(500, json={"error": "dial tcp: lookup registry.ollama.ai"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert not await ollama_setup.prepare(http)
    status = ollama_setup.status()
    assert status["state"] == "pulling"
    assert status["detail"] == "dial tcp: lookup registry.ollama.ai"


async def test_broken_pull_line_is_retried_later() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.35.1"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, content=b'{"status": "pulling a", "dig')

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert not await ollama_setup.prepare_once(http)
    status = ollama_setup.status()
    assert status["state"] == "pulling"
    assert status["detail"]


async def test_html_instead_of_tags_is_retried_later() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.35.1"})
        return httpx.Response(200, content=b"<html>proxy</html>")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert not await ollama_setup.prepare_once(http)
    status = ollama_setup.status()
    assert status["state"] == "connecting"
    assert status["detail"]


async def test_unreachable_ollama_is_connecting() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    http = httpx.AsyncClient(transport=httpx.MockTransport(down))
    assert not await ollama_setup.prepare(http)
    assert ollama_setup.status()["state"] == "connecting"
    assert ollama_setup.status()["detail"] == "connection refused"


def test_llm_client_uses_prepared_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("USE_FAKE_LLM", raising=False)
    monkeypatch.setattr(ollama_setup, "_active_model", "qwen2.5-coder:7b-ctx16384")
    assert LLMClient().model == "qwen2.5-coder:7b-ctx16384"


def test_status_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app

    monkeypatch.setattr(
        ollama_setup, "_status", ollama_setup.LLMStatus("pulling", "qwen2.5-coder:7b", 0.37)
    )
    response = TestClient(app).get("/api/llm/status")
    assert response.status_code == 200
    assert response.json() == {
        "state": "pulling",
        "model": "qwen2.5-coder:7b",
        "progress": 0.37,
        "detail": None,
    }
