"""Хранение и валидация semantic model таблиц (P1)."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from app.core.memory.profile_memory import ProfileMemory
from app.models.semantic import SemanticModel

logger = logging.getLogger(__name__)


class SemanticRegistry:
    """Semantic model в ``_meta.semantic_model`` с проверкой против колонок."""

    def __init__(self, db) -> None:
        self.db = db
        self.memory = ProfileMemory(db)

    def load(self, table: str) -> SemanticModel | None:
        return self.memory.load_semantic_model(table)

    def save(self, model: SemanticModel) -> None:
        self.memory.save_semantic_model(model)

    def delete(self, table: str) -> None:
        self.memory.delete_semantic_model(table)

    def validate(self, model: SemanticModel, columns: set[str]) -> list[str]:
        """Проверить модель против физической схемы таблицы."""
        errors: list[str] = []
        if not model.table.strip():
            errors.append("не задана таблица")
        metric_names = [metric.name for metric in model.metrics]
        dimension_names = [dimension.name for dimension in model.dimensions]
        if len(set(metric_names)) != len(metric_names):
            errors.append("имена метрик повторяются")
        if len(set(dimension_names)) != len(dimension_names):
            errors.append("имена измерений повторяются")
        for metric in model.metrics:
            if metric.column and metric.column not in columns:
                errors.append(f"метрика {metric.name}: колонки {metric.column!r} нет")
            if not metric.column and not metric.sql and metric.aggregation != "COUNT":
                errors.append(f"метрика {metric.name}: нужна column или sql")
        for dimension in model.dimensions:
            if dimension.column not in columns:
                errors.append(f"измерение {dimension.name}: колонки {dimension.column!r} нет")
            if dimension.kind == "temporal" and not dimension.time_grains:
                errors.append(f"измерение {dimension.name}: для temporal нужны time_grains")
        if model.default_time_dimension is not None:
            default = next(
                (dimension for dimension in model.dimensions if dimension.name == model.default_time_dimension),
                None,
            )
            if default is None or default.kind != "temporal":
                errors.append("default_time_dimension должен ссылаться на temporal-измерение")
        for join in model.joins:
            if not all(
                (join.left_table, join.right_table, join.left_column, join.right_column)
            ):
                errors.append("у join пустая таблица или колонка")
        return errors

    def load_yaml(self, path: Path) -> SemanticModel:
        """Загрузить и провалидировать semantic model из YAML-файла."""
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        model = SemanticModel.model_validate(payload)
        columns: set[str] = set()
        try:
            columns = set(self.db.columns(model.table))
        except Exception:  # каталог недоступен: проверим только синтаксис
            logger.warning("semantic model: колонки %s не прочитаны", model.table, exc_info=True)
        errors = self.validate(model, columns) if columns else []
        if errors:
            raise ValueError("; ".join(errors))
        return model

    def save_yaml(self, model: SemanticModel, path: Path) -> None:
        """Сохранить модель в YAML (для версионирования в git)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(model.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
