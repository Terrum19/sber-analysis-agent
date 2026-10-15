"""Память уровня 4: реестр артефактов и дашбордов.

Хранит сгенерированные дашборды, наборы SQL-запросов, построивших их,
и JSON-конфиги визуализаций (Vega-Lite). Обеспечивает повторный доступ
из чата и из UI + перетаскивание элементов на фронтенд-канвасе.
"""

import json
import time
import uuid
from pathlib import Path

from app.config import settings


class ArtifactRegistry:
    """Реестр дашбордов и артефактов сессии (ID -> повторный доступ)."""

    def __init__(self) -> None:
        self._dir = Path(settings.storage_dir) / "dashboards"
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, session_id: str, config: dict) -> str:
        """Сохранить конфиг дашборда, вернуть его ID."""
        dashboard_id = uuid.uuid4().hex[:12]
        record = {"id": dashboard_id, "session_id": session_id, "created_at": time.time(), **config}
        (self._dir / f"{dashboard_id}.json").write_text(json.dumps(record, ensure_ascii=False))
        return dashboard_id

    def get(self, dashboard_id: str) -> dict:
        """Получить конфиг дашборда по ID."""
        file = self._dir / f"{dashboard_id}.json"
        return json.loads(file.read_text()) if file.exists() else {}

    def list(self, session_id: str) -> list[dict]:
        """Список дашбордов сессии."""
        out = []
        for file in self._dir.glob("*.json"):
            record = json.loads(file.read_text())
            if record.get("session_id") == session_id:
                out.append(
                    {
                        "id": record["id"],
                        "title": record.get("title", ""),
                        "created_at": record["created_at"],
                    }
                )
        return out
