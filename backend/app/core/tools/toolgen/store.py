"""JSON-хранилище проверенных спек инструментов.

Спека — данные, поэтому её можно хранить и загружать при старте без импорта
кода. Пишем атомарно (tmp + os.replace), битые файлы при загрузке
пропускаем: реестр не должен падать из-за одного испорченного файла.
"""

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.models.generated_tool import GeneratedToolSpec

logger = logging.getLogger(__name__)


class ToolStore:
    """Каталог ``<storage>/tools/generated/<name>.json``."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        base = Path(storage_dir or settings.storage_dir)
        self.directory = base / "tools" / "generated"

    def path(self, name: str) -> Path:
        return self.directory / f"{name}.json"

    def save(self, spec: GeneratedToolSpec) -> Path:
        """Атомарно записать спеку; вернуть путь файла."""
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "spec_version": spec.spec_version,
            "created_at": datetime.now(UTC).isoformat(),
            "verified_at": datetime.now(UTC).isoformat(),
            "spec": spec.model_dump(mode="json"),
        }
        target = self.path(spec.name)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{spec.name}.", suffix=".tmp", dir=self.directory
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary, target)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return target

    def load_all(self) -> list[GeneratedToolSpec]:
        """Все валидные по форме спеки; битые файлы пропускаются с логом."""
        if not self.directory.is_dir():
            return []
        specs: list[GeneratedToolSpec] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                specs.append(GeneratedToolSpec.model_validate(payload["spec"]))
            except Exception as exc:  # noqa: BLE001 — один файл не ломает старт
                logger.warning("toolgen: спека %s пропущена: %s", path.name, exc)
        return specs

    def records(self) -> list[dict]:
        """Служебная информация о сохранённых спеках (для диагностики/тестов)."""
        if not self.directory.is_dir():
            return []
        records = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("toolgen: запись %s пропущена: %s", path.name, exc)
                continue
            records.append(payload)
        return records
