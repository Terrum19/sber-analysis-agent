import glob
import os

import pytest
from app.config import settings


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in item.path.parts:
            item.add_marker(pytest.mark.integration)
        if "benchmark" in item.path.parts:
            item.add_marker(pytest.mark.benchmark)


@pytest.fixture(autouse=True)
def _cleanup_session_files():
    """Удаляет мусорные JSON-файлы сессий после каждого теста.

    SessionMemory пишет состояние в файлы sessions.<session_id>.json
    рядом с sqlite_path. Без очистки они остаются в репозитории после прогона.
    """
    yield
    session_dir = os.path.dirname(settings.sqlite_path)
    for pattern in ("sessions.*.json", "sessions.sqlite*"):
        for path in glob.glob(os.path.join(session_dir, pattern)):
            try:
                os.remove(path)
            except OSError:
                pass
