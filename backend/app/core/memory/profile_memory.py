"""Память уровня 3: метапамять — паспорт данных и глоссарий.

Хранит сгенерированные при инициализации метаданные: структуру таблиц,
статистику, человекочитаемые алиасы и бизнес-словарь. Избавляет от
сканирования источника заново на каждый вопрос — паспорт всегда в
контексте агента. Сохраняется в JSON/SQLite при первом подключении.
"""

import json
from pathlib import Path

from app.config import settings
from app.core.data.profiling import TableProfile, outlier_summary


class ProfileMemory:
    """Хранилище «паспортов данных» (по одной записи на таблицу)."""

    def __init__(self) -> None:
        self._dir = Path(settings.storage_dir) / "profiles"
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, table: str, passport: dict) -> dict:
        """Сохранить паспорт таблицы."""
        record = dict(passport)
        profile = record.get("profile")
        if isinstance(profile, TableProfile):
            record["profile"] = profile.to_dict()
            record.setdefault("outliers", outlier_summary(profile))
        (self._dir / f"{table}.json").write_text(
            json.dumps(record, ensure_ascii=False, default=str)
        )
        return record

    def load(self, table: str) -> dict:
        """Загрузить паспорт таблицы ({} если ещё не построен)."""
        file = self._dir / f"{table}.json"
        return json.loads(file.read_text()) if file.exists() else {}
