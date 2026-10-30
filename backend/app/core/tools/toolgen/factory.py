"""Фабрика инструментов: LLM -> спецификация -> проверки -> регистрация.

Создание идёт по запросу оркестратора, когда планировщик не нашёл
подходящего инструмента. Регистрируется только спека, прошедшая
ToolValidator; при любой ошибке реестр остаётся прежним.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.core.llm.client import LLMClient
from app.core.llm.prompts.toolgen import (
    TOOL_SMITH_SYSTEM,
    TOOLGEN,
    TOOLGEN_FIX,
    TOOLGEN_TILE,
)
from app.core.tools.base import BaseTool, identifier
from app.core.tools.generated import build_tool
from app.core.tools.registry import ToolRegistry, tool_registry
from app.core.tools.toolgen.store import ToolStore
from app.core.tools.toolgen.validator import (
    ToolValidationError,
    ToolValidator,
    ValidationReport,
)
from app.models.generated_tool import GeneratedToolSpec

logger = logging.getLogger(__name__)

SAMPLE_ROWS = 5
_AUTO_STORE = object()


class ToolCreationError(RuntimeError):
    """Инструмент не создан: LLM не смогла или проверки не прошли."""


@dataclass(frozen=True)
class CreatedTool:
    """Успешно созданный инструмент и отчёт его проверки."""

    tool: BaseTool
    spec: GeneratedToolSpec
    report: ValidationReport


class ToolFactory:
    """Создание инструмента по вопросу и схеме входной таблицы."""

    def __init__(
        self,
        db,
        llm: LLMClient | None = None,
        registry: ToolRegistry | None = None,
        store: ToolStore | None | object = _AUTO_STORE,
        validator: ToolValidator | None = None,
        sandbox_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.db = db
        self.llm = llm
        self.registry = registry or tool_registry
        self.store = (
            (ToolStore() if settings.toolgen_store else None) if store is _AUTO_STORE else store
        )
        self.validator = validator or ToolValidator(db)
        self.sandbox_factory = sandbox_factory

    async def create(
        self,
        question: str,
        input_table: str,
        purpose: str = "",
        tile_title: str = "",
        tile_intent: str = "",
    ) -> CreatedTool:
        """Спроектировать, проверить и зарегистрировать инструмент."""
        if not settings.toolgen_enabled:
            raise ToolCreationError("toolgen is disabled")
        if self.llm is None:
            raise ToolCreationError("toolgen has no LLM client")
        if len(self.registry.generated_tools()) >= settings.toolgen_max_tools:
            raise ToolCreationError(f"generated tools limit reached ({settings.toolgen_max_tools})")
        schema, sample = self._describe(input_table)
        tile = (
            TOOLGEN_TILE.format(title=tile_title, intent=tile_intent or "auto")
            if tile_title
            else ""
        )
        example_column = next(iter(schema), "column")
        prompt = TOOLGEN.format(
            question=question,
            purpose=purpose or question,
            schema=schema,
            sample=sample,
            tools=", ".join(sorted(self.registry.names())),
            tile=tile,
            example_column=example_column,
        )
        errors: list[str] = []
        for attempt in range(max(1, settings.toolgen_attempts)):
            try:
                spec = await self.llm.complete_structured(
                    TOOL_SMITH_SYSTEM, prompt, GeneratedToolSpec
                )
            except Exception as exc:
                raise ToolCreationError(f"tool specification failed: {exc}") from exc
            if not spec.feasible:
                raise ToolCreationError(f"tool is not feasible: {spec.reason or 'no reason given'}")
            try:
                report = await asyncio.to_thread(
                    self.validator.validate,
                    spec,
                    input_table,
                    self._sandbox(spec),
                    self.registry.names(),
                )
                return self._register(spec, report)
            except ToolValidationError as exc:
                errors = exc.errors
                logger.warning(
                    "toolgen: попытка %d для %r не прошла: %s",
                    attempt + 1,
                    spec.name,
                    "; ".join(errors),
                )
                prompt = TOOLGEN_FIX.format(
                    spec=json.dumps(spec.model_dump(mode="json"), ensure_ascii=False),
                    errors="\n".join(f"- {error}" for error in errors),
                    schema=schema,
                    sample=sample,
                )
            except ValueError as exc:
                errors = [str(exc)]
                prompt = TOOLGEN_FIX.format(
                    spec=json.dumps(spec.model_dump(mode="json"), ensure_ascii=False),
                    errors=f"- {exc}",
                    schema=schema,
                    sample=sample,
                )
        raise ToolCreationError(
            "tool was not created after "
            f"{max(1, settings.toolgen_attempts)} attempts: {'; '.join(errors)}"
        )

    def _register(self, spec: GeneratedToolSpec, report: ValidationReport) -> CreatedTool:
        tool = build_tool(spec)
        try:
            self.registry.register_generated(tool, spec)
        except ValueError as exc:
            raise ToolValidationError([str(exc)]) from exc
        if self.store is not None:
            try:
                self.store.save(spec)
            except Exception:  # инструмент уже проверен, ход не роняем
                logger.warning("toolgen: спека %s не сохранена", spec.name, exc_info=True)
        logger.info(
            "toolgen: инструмент %s создан и проверен за %d мс "
            "(строк результата %d, checks %d, reference %s)",
            spec.name,
            report.elapsed_ms,
            report.result_rows,
            report.checks_passed,
            report.reference_checked,
        )
        return CreatedTool(tool=tool, spec=spec, report=report)

    def _sandbox(self, spec: GeneratedToolSpec):
        if spec.language != "python" or self.sandbox_factory is None:
            return None
        return self.sandbox_factory()

    def _describe(self, input_table: str) -> tuple[str, str]:
        """Схема и пример строк входной таблицы для промпта."""
        try:
            schema_rows = self.db.query(f"DESCRIBE {identifier(input_table)}")
            sample_rows = self.db.query(
                f"SELECT * FROM {identifier(input_table)} LIMIT {SAMPLE_ROWS}"
            )
        except Exception as exc:
            raise ToolCreationError(f"cannot read input table {input_table!r}: {exc}") from exc
        schema = {str(row["column_name"]): str(row["column_type"]) for row in schema_rows}
        return (
            json.dumps(schema, ensure_ascii=False),
            json.dumps(sample_rows, ensure_ascii=False, default=str),
        )


def load_generated_tools(
    registry: ToolRegistry | None = None, store: ToolStore | None = None
) -> list[str]:
    """Загрузить проверенные спеки с диска; сломанные/невалидные пропускаются."""
    registry = registry or tool_registry
    store = store or ToolStore()
    if not settings.toolgen_enabled:
        return []
    loaded: list[str] = []
    for spec in store.load_all():
        if len(registry.generated_tools()) >= settings.toolgen_max_tools:
            logger.warning("toolgen: лимит инструментов достигнут, загрузка остановлена")
            break
        try:
            from app.core.tools.toolgen.validator import validate_static

            validate_static(spec, registry.names())
            tool = build_tool(spec)
            registry.register_generated(tool, spec)
        except (ValueError, ToolValidationError) as exc:
            logger.warning("toolgen: спека %s не загружена: %s", spec.name, exc)
            continue
        loaded.append(spec.name)
    if loaded:
        logger.info("toolgen: загружено инструментов: %s", ", ".join(loaded))
    return loaded
