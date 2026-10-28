"""Исполнитель шагов: применяет тулы к таблицам в DuckDB.

По умолчанию шаг берёт результат последнего transform-шага ($prev);
шаг может сослаться на исходную таблицу ($source) или на результат
любого выполненного шага ($step_N) — и во входе, и в аргументах.
Если $prev уже не содержит ни одной из колонок шага (например, после
агрегата), а $source содержит их все, шаг читает $source: это новая
ветка плана (плитка дашборда), а не продолжение конвейера.
Финальная таблица — основание для инсайта/графика/дашборда.
"""

import difflib
import logging
import re
from typing import Any

from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.client import LLMClient
from app.core.sandbox.wasm_runtime import WASMSandbox
from app.core.tools.base import ToolContext, ToolKind
from app.core.tools.registry import tool_registry
from app.models.research_step import PREVIOUS, SOURCE, ResearchStep

logger = logging.getLogger(__name__)

_STEP_REF = re.compile(r"^\$step_(\d+)$")


def tool_kind(step: ResearchStep) -> ToolKind:
    """Вид шага: transform продолжает конвейер, analysis/lookup — нет."""
    try:
        return tool_registry.get(step.tool).kind
    except KeyError:
        return "transform"


def _read_columns(step: ResearchStep, tool: Any, resolve: "_Resolver") -> list[str]:
    """Колонки входной таблицы, которые читает шаг (по аргументам тула)."""
    try:
        args = tool.parse_args(resolve.value(step.columns), resolve.value(step.arguments))
    except (KeyError, ValueError):
        # Синтаксис аргументов проверит сам тул; для выбора входа он не нужен.
        return []
    names: list[str] = []
    for field_name in tool.column_fields():
        value = getattr(args, field_name, None)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and not item.startswith("$"):
                names.append(item)
    return names


class StepExecutor:
    """Исполнение ResearchStep над таблицей в DuckDB."""

    def __init__(
        self,
        db: DuckDBManager | None = None,
        llm: LLMClient | None = None,
        sandbox: WASMSandbox | None = None,
    ) -> None:
        self.db = db or DuckDBManager()
        self.context = ToolContext(llm=llm, sandbox=sandbox)

    async def execute(
        self,
        step: ResearchStep,
        input_table: str,
        step_tables: dict[int, str] | None = None,
        source_table: str | None = None,
    ) -> str:
        """Выполнить один шаг, вернуть имя результирующей temp-таблицы.

        Args:
            input_table: таблица для ссылки $prev.
            step_tables: результаты уже выполненных шагов (для $step_N).
            source_table: исходная таблица (для $source), по умолчанию input_table.

        Raises:
            ValueError: если входная таблица не существует, ссылка указывает
                на невыполненный шаг или инструмент не зарегистрирован.
        """
        resolve = _resolver(input_table, step_tables, source_table)
        table = resolve.table(step.input)

        existing = self.db.query(
            "SELECT table_name FROM information_schema.tables WHERE table_name = $tbl",
            {"tbl": table},
        )
        if not existing:
            msg = f"Таблица '{table}' не найдена в базе данных"
            logger.error(msg)
            raise ValueError(msg)

        logger.debug(
            ">>> StepExecutor.execute() шаг purpose=%r tool=%s args=%s columns=%s input_table=%s",
            step.purpose,
            step.tool,
            step.arguments,
            step.columns,
            table,
        )
        if not step.tool:
            raise ValueError("Не указан инструмент шага")
        try:
            tool = tool_registry.get(step.tool)
        except KeyError:
            msg = f"Инструмент '{step.tool}' не зарегистрирован в реестре"
            logger.error(msg)
            raise ValueError(msg) from None

        # $prev после агрегата может уже не содержать колонок шага: если все
        # его колонки есть только в источнике, это новая ветка (плитка
        # дашборда), а не продолжение конвейера — читаем $source.
        table = self._source_for_isolated_step(
            step, tool, resolve, table, source_table or input_table
        )

        # Preflight колонок: ошибка ловится до выполнения, fix_step не тратится.
        # Для $prev/$step_N проверяем фактическую схему их результата: колонки
        # могли быть переименованы или вычислены предыдущими шагами.
        self._validate_columns(step, tool, resolve, table)

        result = await tool.arun(
            self.context,
            self.db,
            table,
            resolve.value(step.columns),
            **resolve.value(step.arguments),
        )
        logger.debug("<<< StepExecutor.execute() УСПЕШНО result_table=%s", result)
        return result

    def _source_for_isolated_step(
        self,
        step: ResearchStep,
        tool: Any,
        resolve: "_Resolver",
        table: str,
        source: str,
    ) -> str:
        """Таблица шага: $prev теряет колонки после агрегата — читаем $source.

        Если шаг с input=$prev ссылается только на колонки, которых в $prev
        нет, но все они есть в источнике, шаг начинает новую ветку плана
        (например, независимую плитку дашборда), а не продолжает предыдущий
        агрегат. Вернуть $prev здесь — Binder Error уже в DuckDB; отдаём
        источник, чтобы шаг выполнился по назначению.
        """
        if step.input != PREVIOUS or table == source:
            return table
        try:
            requested = _read_columns(step, tool, resolve)
            available = set(self.db.columns(table))
            source_columns = set(self.db.columns(source))
        except Exception:  # noqa: BLE001 — каталог недоступен, решает SQL-ошибка
            return table
        # Часть колонок уже есть в $prev — это продолжение ветки с новыми
        # колонками: не подменяем вход, ошибку (если есть) исправит fix_step.
        if not requested or any(name in available for name in requested):
            return table
        if not source_columns or any(name not in source_columns for name in requested):
            return table
        logger.warning(
            "    шаг %d: колонок %s нет в %s (есть: %s), читаю $source %s",
            step.step_id,
            ", ".join(requested),
            table,
            ", ".join(sorted(available)) or "пусто",
            source,
        )
        return source

    def _validate_columns(
        self, step: ResearchStep, tool: Any, resolve: "_Resolver", table: str
    ) -> None:
        """Проверить колоночные аргументы шага против схемы входной таблицы."""
        try:
            available = set(self.db.columns(table))
        except Exception:  # noqa: BLE001 — каталог недоступен, preflight не блокирует шаг
            # Каталог недоступен — не блокируем шаг: точную ошибку даст SQL.
            logger.warning(
                "    шаг %d: колонки %s не прочитаны, preflight пропущен", step.step_id, table
            )
            return
        if not available:
            return
        args = tool.parse_args(resolve.value(step.columns), resolve.value(step.arguments))
        for name in tool.column_fields():
            value = getattr(args, name, None)
            for item in value if isinstance(value, list) else [value]:
                if not isinstance(item, str) or item.startswith("$") or item in available:
                    continue
                hint = difflib.get_close_matches(item, sorted(available), n=1)
                suffix = f"; возможно, имелось в виду {hint[0]!r}" if hint else ""
                raise ValueError(
                    f"шаг {step.step_id}: колонки {item!r} нет в таблице {table}{suffix}"
                )


def resolve_input(
    step: ResearchStep,
    input_table: str,
    step_tables: dict[int, str] | None = None,
    source_table: str | None = None,
) -> str:
    """Имя таблицы, из которой шаг берёт данные (ValueError — шаг не выполнен)."""
    return _resolver(input_table, step_tables, source_table).table(step.input)


def _resolver(
    input_table: str, step_tables: dict[int, str] | None, source_table: str | None
) -> "_Resolver":
    refs = {PREVIOUS: input_table, SOURCE: source_table or input_table}
    return _Resolver(refs, step_tables or {})


class _Resolver:
    """Подстановка ссылок $prev/$source/$step_N на имена temp-таблиц."""

    def __init__(self, refs: dict[str, str], step_tables: dict[int, str]) -> None:
        self.refs, self.step_tables = refs, step_tables

    def table(self, ref: str) -> str:
        # Во входе допускаем и «step_N» без $ — колонкой это быть не может.
        if re.fullmatch(r"step_\d+", ref):
            ref = f"${ref}"
        return self.value(ref)

    def value(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {key: self.value(item) for key, item in value.items()}
        if not isinstance(value, str):
            return value
        if value in self.refs:
            return self.refs[value]
        match = _STEP_REF.fullmatch(value)
        if match is None:
            return value
        step_id = int(match.group(1))
        if step_id not in self.step_tables:
            raise ValueError(f"Ссылка {value}: шаг {step_id} ещё не выполнен")
        return self.step_tables[step_id]
