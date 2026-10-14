"""Генератор дашбордов по запросу.

Сценарий: «Создай дашборд/панель с ежеквартальной статистикой...».
Собирает несколько визуализаций в дашборд, сохраняет в реестр,
возвращает ID для повторного доступа из чата и UI.
"""
from app.core.dashboards.models import Dashboard, DashboardTile
from app.core.memory.artifact_registry import ArtifactRegistry


class DashboardGenerator:
    """Сборка и сохранение дашборда."""

    def __init__(self, registry: ArtifactRegistry | None = None) -> None:
        self.registry = registry or ArtifactRegistry()

    async def generate(self, session_id: str, specs: list[dict], title: str = "Дашборд") -> str:
        """Собрать дашборд из визуализаций, сохранить, вернуть ID."""
        tiles = [DashboardTile(title=s.get("title", f"График {i}"), spec=s) for i, s in enumerate(specs)]
        dashboard = Dashboard(title=title, tiles=tiles)
        return self.registry.save(session_id, dashboard.model_dump())
