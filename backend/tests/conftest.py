import pytest

from app.config import settings


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in item.path.parts:
            item.add_marker(pytest.mark.integration)
        if "benchmark" in item.path.parts:
            item.add_marker(pytest.mark.benchmark)


@pytest.fixture(autouse=True)
def _isolated_storage(request: pytest.FixtureRequest, tmp_path_factory, monkeypatch):
    """Хранилище каждого теста — во временной папке.

    Пути по умолчанию относительные (backend/storage/...) и считаются от cwd:
    из backend/ тесты писали дашборды, DuckDB и сессии в backend/backend/storage.
    Тесты дефолтов конфигурации проверяют сами значения — их не трогаем.
    """
    if request.module.__name__.endswith("test_config_env"):
        return
    root = tmp_path_factory.mktemp("storage")
    monkeypatch.setattr(settings, "storage_dir", str(root))
    monkeypatch.setattr(settings, "duckdb_path", str(root / "duckdb" / "main.duckdb"))
    monkeypatch.setattr(settings, "sqlite_path", str(root / "sessions" / "sessions.sqlite"))


@pytest.fixture
def codegen_available(monkeypatch):
    """Тул run_python виден, даже если node/Pyodide на машине не установлены."""
    monkeypatch.setattr(settings, "codegen_enabled", True)
    monkeypatch.setattr("app.core.tools.codegen.sandbox_available", lambda: True)
