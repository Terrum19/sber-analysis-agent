"""Проверка декларативной спеки до регистрации инструмента.

Каскад: статические правила -> сборка Args и парсинг примеров -> smoke на
выборке входной таблицы -> независимый reference SQL / SQL-checks.
Инструмент регистрируется только если прошли все проверки.

SQL проверяется по AST DuckDB (json_serialize_sql): разрешён только один
SELECT/SET-OPERATION, только входная таблица и CTE, без table functions,
DDL/DML, SAMPLE и недетерминированных функций.
"""

import ast
import json
import logging
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from datetime import time as dt_time
from decimal import Decimal
from functools import cache
from typing import Any

import duckdb

from app.config import settings
from app.core.sandbox.wasm_runtime import SandboxError
from app.core.tools.base import identifier, literal
from app.core.tools.generated import (
    build_tool,
    render_parameter,
    render_sql,
    render_template,
    sample_values,
    wrap_python_code,
)
from app.models.generated_tool import GeneratedToolSpec

logger = logging.getLogger(__name__)

PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_CHECK_RESULT = re.compile(r"\b__tool_result\b")
_PARSER_LOCK = threading.Lock()

_FORBIDDEN_FUNCTIONS = frozenset(
    {
        "getenv",
        "current_setting",
        "getvariable",
        "random",
        "uuid",
        "now",
        "today",
        "current_date",
        "current_time",
        "current_timestamp",
        "localtimestamp",
        "setseed",
        "nextval",
        "currval",
        "txid_current",
    }
)
_FORBIDDEN_PREFIXES = (
    "read_csv",
    "read_parquet",
    "read_json",
    "read_text",
    "read_blob",
    "glob",
    "parquet_",
    "csv_",
    "delta_scan",
    "iceberg_scan",
    "postgres_scan",
    "sqlite_scan",
    "mysql_scan",
    "query",
    "query_table",
    "st_read",
)
_FORBIDDEN_NODE_TYPES = frozenset(
    {
        "TABLE_FUNCTION",
        "INSERT_NODE",
        "UPDATE_NODE",
        "DELETE_NODE",
        "CREATE_NODE",
        "DROP_NODE",
        "COPY_NODE",
        "PRAGMA_NODE",
        "ATTACH_NODE",
        "DETACH_NODE",
        "LOAD_NODE",
        "INSTALL_NODE",
        "CALL_NODE",
        "EXPORT_NODE",
    }
)
_NON_DETERMINISTIC_COLUMNS = frozenset(
    {"current_date", "current_time", "current_timestamp", "localtimestamp", "today"}
)

_ALLOWED_IMPORTS = frozenset(
    {
        "pandas",
        "numpy",
        "scipy",
        "sklearn",
        "statsmodels",
        "math",
        "statistics",
        "datetime",
        "json",
        "re",
        "itertools",
        "collections",
        "decimal",
        "functools",
        "typing",
        "warnings",
    }
)
_FORBIDDEN_CALLS = frozenset(
    {
        "open",
        "eval",
        "exec",
        "compile",
        "__import__",
        "breakpoint",
        "input",
        "exit",
        "quit",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "help",
    }
)
_FORBIDDEN_ATTRIBUTES = frozenset(
    {
        "__globals__",
        "__builtins__",
        "__subclasses__",
        "__getattribute__",
        "__dict__",
        "__class__",
        "__mro__",
        "__code__",
        "__closure__",
        "__self__",
        "__import__",
    }
)


class ToolValidationError(ValueError):
    """Спека не прошла проверки; errors — что именно не так."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class ValidationReport:
    """Что именно проверили у созданного инструмента."""

    sample_rows: int = 0
    result_rows: int = 0
    reference_checked: bool = False
    checks_passed: int = 0
    elapsed_ms: int = 0
    details: list[str] = field(default_factory=list)


@cache
def _parser() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


def parse_select(sql: str) -> dict:
    """AST одного SELECT/SET-оператора DuckDB (ValueError при ошибке разбора)."""
    with _PARSER_LOCK:
        try:
            serialized = (
                _parser().execute("SELECT json_serialize_sql($sql)", {"sql": sql}).fetchone()
            )
        except duckdb.Error as exc:
            raise ValueError(f"SQL does not parse: {exc}") from exc
    tree = json.loads(serialized[0])
    if tree.get("error"):
        raise ValueError(f"SQL is not a SELECT: {tree.get('error_message', 'invalid')}")
    statements = tree.get("statements") or []
    if len(statements) != 1:
        raise ValueError("exactly one SQL statement is required")
    return statements[0]["node"]


def _walk(node: object, allowed_tables: set[str], errors: list[str], context: str) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, allowed_tables, errors, context)
        return
    if not isinstance(node, dict):
        return
    node_type = node.get("type")
    if not isinstance(node_type, str):
        # У VALUE_CONSTANT поле type — описание типа, а не имя узла.
        node_type = None
    if node_type in _FORBIDDEN_NODE_TYPES:
        errors.append(f"{context}: {node_type.lower()} is not allowed")
    if node_type == "BASE_TABLE":
        if node.get("schema_name") or node.get("catalog_name"):
            errors.append(f"{context}: schema-qualified table names are not allowed")
        table = str(node.get("table_name") or "")
        if table not in allowed_tables:
            errors.append(f"{context}: table {table!r} is not allowed")
    if node.get("sample") is not None:
        errors.append(f"{context}: SAMPLE/TABLESAMPLE is not allowed")
    if node_type == "FUNCTION":
        function = str(node.get("function_name") or "").lower()
        if function in _FORBIDDEN_FUNCTIONS or function.startswith(_FORBIDDEN_PREFIXES):
            errors.append(f"{context}: function {function!r} is not allowed")
    if node_type == "COLUMN_REF":
        names = [str(name).lower() for name in node.get("column_names") or []]
        if len(names) == 1 and names[0] in _NON_DETERMINISTIC_COLUMNS:
            errors.append(f"{context}: {names[0]!r} is not deterministic")
    for item in node.values():
        _walk(item, allowed_tables, errors, context)


def _cte_names(node: object) -> set[str]:
    names: set[str] = set()
    if isinstance(node, list):
        for item in node:
            names |= _cte_names(item)
    elif isinstance(node, dict):
        cte_map = node.get("cte_map") or {}
        for entry in cte_map.get("map") or []:
            names.add(str(entry.get("key") or ""))
        for item in node.values():
            names |= _cte_names(item)
    return names


def check_sql(
    sql: str, *, allowed_tables: set[str], context: str = "SQL", scan_comments: bool = True
) -> None:
    """Проверить один SELECT по AST и белому списку таблиц."""
    if not sql.strip():
        raise ValueError(f"{context}: SQL is empty")
    if scan_comments:
        if ";" in sql:
            raise ValueError(f"{context}: multiple statements are not allowed (';')")
        if "--" in sql or "/*" in sql:
            raise ValueError(f"{context}: SQL comments are not allowed")
    node = parse_select(sql)
    if node.get("type") not in {"SELECT_NODE", "SET_OPERATION_NODE"}:
        raise ValueError(f"{context}: only SELECT statements are allowed")
    errors: list[str] = []
    tables = set(allowed_tables) | _cte_names(node)
    _walk(node, tables, errors, context)
    if errors:
        raise ValueError("; ".join(dict.fromkeys(errors)))


def check_rendered_sql(sql: str, *, allowed_tables: set[str]) -> None:
    """Проверка уже отрендеренного SQL: параметры не могут добавить statements."""
    check_sql(sql, allowed_tables=allowed_tables, context="rendered SQL", scan_comments=False)


def check_python_code(code: str, parameters: list[str] | None = None) -> None:
    """Статическая проверка Python-кода инструмента (исполняется в песочнице)."""
    if not code.strip():
        raise ValueError("Python code is empty")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"Python syntax error: {exc.msg} (line {exc.lineno})") from exc
    errors: list[str] = []
    names: set[str] = set()
    assigned_result = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in _ALLOWED_IMPORTS:
                    errors.append(f"import {alias.name!r} is not allowed")
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")[0]
            if module not in _ALLOWED_IMPORTS:
                errors.append(f"import from {node.module!r} is not allowed")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                errors.append(f"call {node.func.id}() is not allowed")
            if isinstance(node.func, ast.Attribute) and node.func.attr in _FORBIDDEN_ATTRIBUTES:
                errors.append(f"attribute {node.func.attr} is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ATTRIBUTES:
            errors.append(f"attribute {node.attr} is not allowed")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            errors.append("global/nonlocal is not allowed")
        elif isinstance(node, ast.Name):
            names.add(node.id)
            if node.id == "__builtins__":
                errors.append("__builtins__ is not allowed")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "result" for target in targets):
                assigned_result = True
    if not assigned_result:
        errors.append("code must assign the result variable")
    unused = [name for name in parameters or [] if name not in names]
    if unused:
        errors.append(f"unused parameters in Python code: {', '.join(unused)}")
    if errors:
        raise ValueError("; ".join(dict.fromkeys(errors)))


def _placeholder_value(parameter) -> str:
    if parameter.type == "column":
        return "__tool_column__"
    if parameter.type == "columns":
        return "__tool_columns__"
    if parameter.type == "integer":
        return "0"
    if parameter.type == "number":
        return "0.0"
    if parameter.type == "boolean":
        return "TRUE"
    if parameter.type == "enum":
        return literal(parameter.values[0])
    return "'__tool_string__'"


def _placeholders(template: str, context: str) -> set[str]:
    names = {match.group(1) for match in PLACEHOLDER.finditer(template)}
    stripped = PLACEHOLDER.sub("", template)
    if "{" in stripped or "}" in stripped:
        raise ValueError(f"{context}: invalid placeholder syntax")
    return names


def _render_for_check(template: str, spec: GeneratedToolSpec, input_name: str) -> str:
    values = {"input": identifier(input_name)}
    for parameter in spec.parameters:
        values[parameter.name] = _placeholder_value(parameter)
    return render_template(template, values)


def _check_template(
    template: str,
    spec: GeneratedToolSpec,
    context: str,
    allowed: set[str],
    *,
    require_all: bool = True,
) -> list[str]:
    errors: list[str] = []
    try:
        names = _placeholders(template, context)
        declared = {parameter.name for parameter in spec.parameters}
        unknown = names - declared - {"input"}
        if unknown:
            errors.append(f"{context}: unknown placeholders {sorted(unknown)}")
        if "input" not in names:
            errors.append(f"{context}: {{input}} placeholder is required")
        unused = declared - names
        if require_all and unused:
            errors.append(f"{context}: unused parameters {sorted(unused)}")
        rendered = _render_for_check(template, spec, "__tool_input__")
        check_sql(rendered, allowed_tables=allowed, context=context)
    except ValueError as exc:
        errors.append(str(exc))
    return errors


def static_errors(spec: GeneratedToolSpec, existing_names: set[str] | None = None) -> list[str]:
    """Все статические нарушения спеки ([] — можно проверять на данных)."""
    errors: list[str] = []
    if not spec.feasible:
        return [f"tool is not feasible: {spec.reason or 'no reason given'}"]
    if existing_names and spec.name in existing_names:
        errors.append(f"tool name {spec.name!r} is already registered")
    if spec.language == "sql":
        if spec.sql and len(spec.sql) > settings.toolgen_max_sql_chars:
            errors.append(
                f"SQL is too long ({len(spec.sql)} > {settings.toolgen_max_sql_chars} chars)"
            )
        if spec.sql:
            errors.extend(_check_template(spec.sql, spec, f"{spec.name}: sql", {"__tool_input__"}))
    else:
        if spec.code and len(spec.code) > settings.toolgen_max_code_chars:
            errors.append(
                f"Python code is too long ({len(spec.code)} > "
                f"{settings.toolgen_max_code_chars} chars)"
            )
        if spec.code:
            try:
                check_python_code(spec.code, [parameter.name for parameter in spec.parameters])
            except ValueError as exc:
                errors.append(f"{spec.name}: python: {exc}")
    if spec.reference_sql:
        if len(spec.reference_sql) > settings.toolgen_max_sql_chars:
            errors.append("reference_sql is too long")
        errors.extend(
            _check_template(
                spec.reference_sql,
                spec,
                f"{spec.name}: reference_sql",
                {"__tool_input__"},
                require_all=False,
            )
        )
    for index, check in enumerate(spec.checks, 1):
        context = f"{spec.name}: check {index}"
        if "{" in check or "}" in check:
            errors.append(f"{context}: placeholders are not allowed")
            continue
        if not _CHECK_RESULT.search(check):
            errors.append(f"{context}: check must read __tool_result")
            continue
        try:
            check_sql(check, allowed_tables={"__tool_result"}, context=context)
        except ValueError as exc:
            errors.append(str(exc))
    if not spec.reference_sql and not spec.checks:
        errors.append(f"{spec.name}: reference_sql or at least one SQL check is required")
    tool = build_tool(spec)
    try:
        tool.parse_args(arguments=sample_values(spec))
    except ValueError as exc:
        errors.append(f"{spec.name}: sample arguments: {exc}")
    return errors


def validate_static(spec: GeneratedToolSpec, existing_names: set[str] | None = None) -> None:
    """Проверить спеку без исполнения; ошибки — ToolValidationError."""
    errors = static_errors(spec, existing_names)
    if errors:
        raise ToolValidationError(errors)


def _table_columns(db, table: str) -> list[str]:
    rows = db.query(f"DESCRIBE {identifier(table)}")
    return [str(row["column_name"]) for row in rows]


def _table_rows(db, table: str) -> list[dict]:
    return db.query(f"SELECT * FROM {identifier(table)}")


def _value_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float, Decimal)) and isinstance(right, (int, float, Decimal)):
        left_f, right_f = float(left), float(right)
        if math.isnan(left_f) or math.isnan(right_f):
            return math.isnan(left_f) and math.isnan(right_f)
        return math.isclose(left_f, right_f, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(left, (date, datetime, dt_time)) or isinstance(right, (date, datetime, dt_time)):
        return left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _value_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _value_equal(left[key], right[key]) for key in left
        )
    return left == right


def _row_equal(left: dict, right: dict) -> bool:
    return left.keys() == right.keys() and all(_value_equal(left[key], right[key]) for key in left)


def _rows_equal(left: list[dict], right: list[dict]) -> bool:
    """Сравнить наборы строк без учёта порядка (числа — с допуском)."""
    if len(left) != len(right):
        return False
    unmatched = list(right)
    for row in left:
        for index, candidate in enumerate(unmatched):
            if _row_equal(row, candidate):
                unmatched.pop(index)
                break
        else:
            return False
    return True


def _row_count(db, table: str) -> int:
    return int(db.query(f"SELECT COUNT(*) AS n FROM {identifier(table)}")[0]["n"])


class ToolValidator:
    """Проверка спеки на реальной выборке входной таблицы."""

    def __init__(self, db=None) -> None:
        self.db = db

    def validate_static(
        self, spec: GeneratedToolSpec, existing_names: set[str] | None = None
    ) -> None:
        validate_static(spec, existing_names)

    def validate(
        self,
        spec: GeneratedToolSpec,
        input_table: str,
        sandbox=None,
        existing_names: set[str] | None = None,
    ) -> ValidationReport:
        """Полный каскад проверок; ToolValidationError, если хоть одна не прошла."""
        started_at = time.perf_counter()
        validate_static(spec, existing_names)
        if self.db is None:
            raise ToolValidationError(["validator has no DuckDB connection"])
        tool = build_tool(spec)
        try:
            args = tool.parse_args(arguments=sample_values(spec))
        except ValueError as exc:
            raise ToolValidationError([f"{spec.name}: sample arguments: {exc}"]) from exc
        uid = uuid.uuid4().hex[:10]
        sample_table = f"_toolgen_sample_{uid}"
        result_table = f"_toolgen_result_{uid}"
        created: list[str] = []
        try:
            sample_rows = self._make_sample(input_table, sample_table)
            created.append(sample_table)
            values = args.model_dump()
            self._materialize(spec, values, sample_table, result_table, sandbox)
            created.append(result_table)
            columns = _table_columns(self.db, result_table)
            if not columns:
                raise ToolValidationError([f"{spec.name}: result has no columns"])
            if len(columns) != len(set(columns)):
                raise ToolValidationError([f"{spec.name}: result has duplicate column names"])
            result_rows = _row_count(self.db, result_table)
            if result_rows == 0 and not spec.allow_empty:
                raise ToolValidationError([f"{spec.name}: result is empty on the sample"])
            checks_passed = self._run_checks(spec, result_table)
            reference_checked = self._run_reference(spec, values, sample_table, result_table)
            return ValidationReport(
                sample_rows=sample_rows,
                result_rows=result_rows,
                reference_checked=reference_checked,
                checks_passed=checks_passed,
                elapsed_ms=max(1, round((time.perf_counter() - started_at) * 1000)),
            )
        except ToolValidationError:
            raise
        except (duckdb.Error, ValueError, SandboxError) as exc:
            raise ToolValidationError([f"{spec.name}: {exc}"]) from exc
        finally:
            for table in reversed(created):
                try:
                    self.db.execute(f"DROP TABLE IF EXISTS {identifier(table)}")
                except duckdb.Error:
                    logger.warning("toolgen: не удалось удалить %s", table, exc_info=True)

    def _make_sample(self, input_table: str, sample_table: str) -> int:
        self.db.execute(
            f"CREATE TEMP TABLE {identifier(sample_table)} AS "
            f"SELECT * FROM {identifier(input_table)} LIMIT {max(1, settings.toolgen_sample_rows)}"
        )
        return _row_count(self.db, sample_table)

    def _materialize(
        self,
        spec: GeneratedToolSpec,
        values: dict[str, Any],
        sample_table: str,
        result_table: str,
        sandbox,
    ) -> None:
        limit = max(1000, settings.toolgen_sample_rows * 4)
        if spec.language == "sql":
            sql = render_sql(spec, sample_table, values)
            check_rendered_sql(sql, allowed_tables={sample_table})
            # Быстрый отлов отсутствующих колонок/таблиц до создания результата.
            self.db.query(f"EXPLAIN {sql}")
            self.db.execute(
                f"CREATE TEMP TABLE {identifier(result_table)} AS "
                f"SELECT * FROM ({sql}) AS __toolgen_limited LIMIT {limit}"
            )
            return
        from app.core.sandbox.wasm_runtime import RESULT_FILE, WASMSandbox

        sandbox = sandbox or WASMSandbox()
        if not sandbox.available():
            raise ToolValidationError([f"{spec.name}: WASM sandbox is not available"])
        if not spec.code:
            raise ToolValidationError([f"{spec.name}: Python tool has no code"])
        check_python_code(spec.code, [parameter.name for parameter in spec.parameters])
        with sandbox.workspace() as workdir:
            input_path = workdir / "input.csv"
            with self.db.lock:
                self.db.execute(
                    f"COPY (SELECT * FROM {identifier(sample_table)}) TO "
                    f"{literal(str(input_path))} (HEADER)"
                )
            sandbox.run(wrap_python_code(spec.code, values), workdir)
            result_path = workdir / RESULT_FILE
            if not result_path.exists():
                raise ToolValidationError([f"{spec.name}: sandbox did not produce {RESULT_FILE}"])
            self.db.execute(
                f"CREATE TEMP TABLE {identifier(result_table)} AS "
                f"SELECT * FROM read_csv_auto({literal(str(result_path))}) LIMIT {limit}"
            )

    def _run_checks(self, spec: GeneratedToolSpec, result_table: str) -> int:
        passed = 0
        for index, check in enumerate(spec.checks, 1):
            rewritten = _CHECK_RESULT.sub(identifier(result_table), check)
            try:
                rows = self.db.query(rewritten)
            except duckdb.Error as exc:
                raise ToolValidationError([f"{spec.name}: check {index} failed: {exc}"]) from exc
            if len(rows) != 1 or len(rows[0]) != 1:
                raise ToolValidationError(
                    [f"{spec.name}: check {index} must return exactly one value"]
                )
            if not bool(next(iter(rows[0].values()))):
                raise ToolValidationError([f"{spec.name}: check {index} is false"])
            passed += 1
        return passed

    def _run_reference(
        self,
        spec: GeneratedToolSpec,
        values: dict[str, Any],
        sample_table: str,
        result_table: str,
    ) -> bool:
        if not spec.reference_sql:
            return False
        reference = _render_for_values(spec, spec.reference_sql, sample_table, values)
        check_rendered_sql(reference, allowed_tables={sample_table})
        reference_table = f"_toolgen_reference_{uuid.uuid4().hex[:10]}"
        try:
            self.db.execute(
                f"CREATE TEMP TABLE {identifier(reference_table)} AS "
                f"SELECT * FROM ({reference}) AS __toolgen_reference "
                f"LIMIT {max(1000, settings.toolgen_sample_rows * 4)}"
            )
            reference_rows = _table_rows(self.db, reference_table)
            result_rows = _table_rows(self.db, result_table)
            result_columns = set(_table_columns(self.db, result_table))
            reference_columns_list = _table_columns(self.db, reference_table)
            reference_columns = set(reference_columns_list)
            if len(reference_columns_list) != len(reference_columns):
                raise ToolValidationError(
                    [f"{spec.name}: reference_sql has duplicate column names"]
                )
        except duckdb.Error as exc:
            raise ToolValidationError([f"{spec.name}: reference_sql failed: {exc}"]) from exc
        finally:
            try:
                self.db.execute(f"DROP TABLE IF EXISTS {identifier(reference_table)}")
            except duckdb.Error:
                logger.warning("toolgen: не удалось удалить %s", reference_table, exc_info=True)
        if result_columns != reference_columns:
            raise ToolValidationError(
                [
                    (
                        f"{spec.name}: reference columns differ: "
                        f"{sorted(result_columns)} != {sorted(reference_columns)}"
                    )
                ]
            )
        if not _rows_equal(result_rows, reference_rows):
            raise ToolValidationError([f"{spec.name}: reference_sql returned a different result"])
        return True


def _render_for_values(
    spec: GeneratedToolSpec, template: str, input_table: str, values: dict[str, Any]
) -> str:
    """Отрендерить reference SQL теми же аргументами, что и основной запрос."""
    substitutions = {"input": identifier(input_table)}
    for parameter in spec.parameters:
        if parameter.name in values:
            substitutions[parameter.name] = render_parameter(parameter, values[parameter.name])
        elif parameter.default is not None:
            substitutions[parameter.name] = render_parameter(parameter, parameter.default)
        else:
            substitutions[parameter.name] = "NULL"
    return render_template(template, substitutions)
