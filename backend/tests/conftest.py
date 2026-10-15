import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in item.path.parts:
            item.add_marker(pytest.mark.integration)
        if "benchmark" in item.path.parts:
            item.add_marker(pytest.mark.benchmark)
