import json
import math
import re
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, date, datetime, time
from decimal import Decimal

from app.config import settings

INTEGER_TYPES = {
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
}
FLOAT_TYPES = {"FLOAT", "DOUBLE"}
TEMPORAL_TYPES = {
    "DATE",
    "TIMESTAMP",
    "TIMESTAMP WITH TIME ZONE",
    "TIMESTAMP_S",
    "TIMESTAMP_MS",
    "TIMESTAMP_NS",
}
TEXT_TYPES = {"VARCHAR", "UUID"}
QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)
OUTLIER_FENCE = 1.5
IDENTIFIER_NOTES = {
    "unique": "целые числа без повторов — похоже на ключ, считается категорией",
    "fixed_width": "коды одной длины из плотного диапазона — считается категорией",
}
IDENTIFIER_TOKENS = {"id", "код"}
IDENTIFIER_PREFIX = "идентификатор"
CODE_MIN_WIDTH = 4
CODE_MIN_DISTINCT = 10
CODE_MIN_DENSITY = 0.5
UNIQUE_KEY_MIN_ROWS = 100
NUMERIC_TEXT_MIN_SHARE = 0.95
FLAG_NOTES = {
    "numeric_as_text": "числа записаны текстом",
    "null_tokens": "литералы пропусков в тексте",
    "header_value": "среди значений есть имя колонки",
}
WARNING_COLUMNS = 5
WARNING_PHRASES = {
    "numeric_as_text": "Числа записаны текстом",
    "null_tokens": "Литералы пропусков (NULL, N/A и т. п.)",
    "header_value": "Значение, равное имени колонки (похоже на повторённый заголовок),",
}
MEASURE_PREFIXES = (
    "сумм",
    "цен",
    "стоим",
    "руб",
    "доход",
    "выручк",
    "колич",
    "кол",
    "шт",
    "вес",
    "кг",
    "объем",
    "объём",
    "показ",
    "клик",
    "просмотр",
    "год",
    "лет",
    "возраст",
    "врем",
    "длит",
    "мс",
    "сек",
    "мин",
    "час",
    "дн",
    "балл",
    "оценк",
    "процент",
    "amount",
    "price",
    "cost",
    "revenue",
    "qty",
    "quantity",
    "count",
    "total",
    "sum",
    "year",
    "age",
    "duration",
    "score",
)
IDENTIFIER_MEASURES = (
    "min",
    "max",
    "mean",
    "median",
    "std",
    "q1",
    "q3",
    "iqr",
    "p05",
    "p95",
    "skewness",
    "kurtosis",
    "outlier_count",
)
ROW_NAMES = {
    "name": "column_name",
    "distinct": "distinct_count",
    "min": "min_value",
    "max": "max_value",
    "mode": "mode_value",
}
JSON_FIELDS = {"min", "max", "mode", "min_date", "max_date"}


@dataclass
class ColumnStats:
    name: str
    dtype: str
    kind: str
    total: int = 0
    nulls: int = 0
    null_pct: float | None = None
    nan_pct: float | None = None
    distinct: int | None = None
    distinct_pct: float | None = None
    min: object = None
    max: object = None
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    q1: float | None = None
    q3: float | None = None
    iqr: float | None = None
    p05: float | None = None
    p95: float | None = None
    skewness: float | None = None
    kurtosis: float | None = None
    mode: object = None
    top_values: list[dict] = field(default_factory=list)
    min_len: int | None = None
    max_len: int | None = None
    min_date: object = None
    max_date: object = None
    is_constant: bool = False
    is_unique: bool = False
    outlier_count: int | None = None
    identifier_by: str | None = None
    flags: list[str] = field(default_factory=list)

    def fences(self) -> tuple[float, float] | None:
        if self.q1 is None or self.q3 is None:
            return None
        spread = OUTLIER_FENCE * (self.q3 - self.q1)
        return self.q1 - spread, self.q3 + spread

    def to_row(self) -> dict:
        """Строка _meta.column_passport (без table_name и position).

        Поля с зарезервированными в SQL именами переименовываются (ROW_NAMES),
        значения без фиксированного типа, top_values и flags сериализуются в JSON.
        """
        row = {}
        for name, value in asdict(self).items():
            if name in JSON_FIELDS:
                value = dump_json(value)
            elif name in ("top_values", "flags"):
                value = json.dumps(to_json(value), ensure_ascii=False)
            row[ROW_NAMES.get(name, name)] = value
        row["lower_fence"], row["upper_fence"] = self.fences() or (None, None)
        return row

    @classmethod
    def from_row(cls, row: dict) -> "ColumnStats":
        """Обратное к to_row: типы значений восстанавливаются по dtype."""
        dtype = row["dtype"]
        values = {}
        for item in fields(cls):
            value = row[ROW_NAMES.get(item.name, item.name)]
            if item.name in JSON_FIELDS:
                value = restore(load_json(value), dtype)
            elif item.name == "top_values":
                value = [
                    {"value": restore(entry["value"], dtype), "count": entry["count"]}
                    for entry in json.loads(value)
                ]
            elif item.name == "flags":
                value = load_json(value) or []
            values[item.name] = value
        return cls(**values)


@dataclass
class TableProfile:
    table: str
    row_count: int
    column_count: int
    columns: list[ColumnStats]
    profiled_at: str
    empty_rows: int = 0

    def column(self, name: str) -> ColumnStats | None:
        return next((column for column in self.columns if column.name == name), None)

    def to_dict(self) -> dict:
        return {
            "table": self.table,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "columns": [to_json(asdict(column)) for column in self.columns],
            "profiled_at": self.profiled_at,
            "empty_rows": self.empty_rows,
        }

    @classmethod
    def from_rows(cls, table_row: dict, column_rows: list[dict]) -> "TableProfile":
        """Собрать профиль из строк _meta.table_passport и _meta.column_passport."""
        ordered = sorted(column_rows, key=lambda row: row["position"])
        return cls(
            table=table_row["table_name"],
            row_count=table_row["row_count"],
            column_count=table_row["column_count"],
            columns=[ColumnStats.from_row(row) for row in ordered],
            profiled_at=table_row["profiled_at"],
            empty_rows=table_row.get("empty_rows") or 0,
        )


def name_tokens(name: str) -> list[str]:
    return [token for token in re.split(r"[\W_]+", name.lower()) if token]


def is_identifier_name(name: str) -> bool:
    return any(
        token in IDENTIFIER_TOKENS or token.startswith(IDENTIFIER_PREFIX)
        for token in name_tokens(name)
    )


def is_measure_name(name: str) -> bool:
    return any(token.startswith(MEASURE_PREFIXES) for token in name_tokens(name))


def identifier_evidence(column: ColumnStats) -> str | None:
    if column.kind != "numeric" or column.dtype.upper() not in INTEGER_TYPES:
        return None
    if column.min is None or column.min < 0 or not column.distinct:
        return None
    if is_measure_name(column.name):
        return None
    if column.is_unique and column.nulls == 0 and column.distinct >= UNIQUE_KEY_MIN_ROWS:
        return "unique"
    width = len(str(column.min))
    density = column.distinct / (column.max - column.min + 1)
    if (
        width >= CODE_MIN_WIDTH
        and width == len(str(column.max))
        and column.distinct >= CODE_MIN_DISTINCT
        and density >= CODE_MIN_DENSITY
    ):
        return "fixed_width"
    return None


def mark_identifier(column: ColumnStats, evidence: str) -> None:
    column.kind = "identifier"
    column.identifier_by = evidence
    for measure in IDENTIFIER_MEASURES:
        setattr(column, measure, None)


def classify(dtype: str, name: str = "") -> str:
    normalized = dtype.upper()
    if normalized in INTEGER_TYPES or normalized in FLOAT_TYPES or normalized.startswith("DECIMAL"):
        return "identifier" if is_identifier_name(name) else "numeric"
    if normalized in TEMPORAL_TYPES:
        return "temporal"
    if normalized in TEXT_TYPES or normalized.startswith("ENUM"):
        return "text"
    if normalized == "BOOLEAN":
        return "bool"
    return "other"


def to_json(value: object) -> object:
    if isinstance(value, dict):
        return {key: to_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_json(item) for item in value]
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def dump_json(value: object) -> str | None:
    """Значение -> текст для JSON-колонки (None остаётся SQL NULL)."""
    return None if value is None else json.dumps(to_json(value), ensure_ascii=False)


def load_json(text: str | None) -> object:
    return None if text is None else json.loads(text)


def restore(value: object, dtype: str) -> object:
    """Обратное к to_json: вернуть значению из JSON тип колонки dtype."""
    if not isinstance(value, str):
        return value
    normalized = dtype.upper()
    if normalized == "DATE":
        return date.fromisoformat(value)
    if normalized in TEMPORAL_TYPES:
        return datetime.fromisoformat(value)
    if normalized == "UUID":
        return uuid.UUID(value)
    return value


ROW_FIELDS = tuple(ColumnStats(name="", dtype="", kind="").to_row())


def plain(value: object) -> object:
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def number(value: object) -> float | None:
    value = plain(value)
    return None if value is None else float(value)


def percent(part: int, whole: int) -> float | None:
    if not whole:
        return None
    return round(100.0 * part / whole, 4)


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def quote_table(table: str) -> str:
    return ".".join(quote_identifier(part) for part in table.split("."))


def is_float(column: ColumnStats) -> bool:
    return column.dtype.upper() in FLOAT_TYPES


def clean_filter(column: ColumnStats, identifier: str) -> str:
    return f" FILTER (WHERE NOT isnan({identifier}))" if is_float(column) else ""


def stats_expressions(index: int, column: ColumnStats, approx: bool) -> list[str]:
    identifier = quote_identifier(column.name)
    prefix = f"c{index}"
    expressions = [f'COUNT({identifier}) AS "{prefix}_count"']
    if column.kind == "numeric":
        value_filter = clean_filter(column, identifier)
        as_double = f"CAST({identifier} AS DOUBLE)"
        quantile_function = "approx_quantile" if approx else "quantile_cont"
        quantile_list = ", ".join(str(level) for level in QUANTILES)
        if is_float(column):
            expressions.append(f'COALESCE(count_if(isnan({identifier})), 0) AS "{prefix}_nan"')
        expressions += [
            f'MIN({identifier}){value_filter} AS "{prefix}_min"',
            f'MAX({identifier}){value_filter} AS "{prefix}_max"',
            f'AVG({as_double}){value_filter} AS "{prefix}_mean"',
            f'STDDEV_POP({as_double}){value_filter} AS "{prefix}_std"',
            f'SKEWNESS({as_double}){value_filter} AS "{prefix}_skewness"',
            f'KURTOSIS({as_double}){value_filter} AS "{prefix}_kurtosis"',
            (
                f"{quantile_function}({as_double}, [{quantile_list}]){value_filter} "
                f'AS "{prefix}_quantiles"'
            ),
        ]
    elif column.kind in ("temporal", "text"):
        expressions += [
            f'MIN({identifier}) AS "{prefix}_min"',
            f'MAX({identifier}) AS "{prefix}_max"',
        ]
        if column.kind == "text":
            length = f"LENGTH(CAST({identifier} AS VARCHAR))"
            expressions += [
                f'MIN({length}) AS "{prefix}_min_len"',
                f'MAX({length}) AS "{prefix}_max_len"',
            ]
    return expressions


def has_quality_flags(column: ColumnStats) -> bool:
    return column.kind == "text" and column.dtype.upper() == "VARCHAR"


def quality_fields(column: ColumnStats) -> str:
    """Счётчики флагов качества текстовой колонки (см. quality_flags).

    Считаются по различным значениям частотного подзапроса с весом frequency:
    у колонки с несколькими значениями это почти бесплатно.
    """
    trimmed = "trim(value)"
    tokens = ", ".join(quote_literal(token) for token in settings.csv_null_tokens)
    is_token = f"{trimmed} IN ({tokens})" if tokens else "false"
    is_number = f"isfinite(TRY_CAST({trimmed} AS DOUBLE)) AND NOT ({is_token})"
    is_header = f"lower({trimmed}) = {quote_literal(column.name.strip().lower())}"
    is_digits = f"regexp_full_match({trimmed}, '[0-9]+')"
    weighted = {
        "null_tokens": is_token,
        "headers": is_header,
        "numbers": is_number,
        "digits": is_digits,
        "leading_zeros": f"regexp_full_match({trimmed}, '0[0-9]+')",
    }
    entries = [
        f"'{name}': COALESCE(sum(frequency) FILTER (WHERE {condition}), 0)"
        for name, condition in weighted.items()
    ]
    entries += [
        f"'digits_min_len': MIN(LENGTH({trimmed})) FILTER (WHERE {is_digits})",
        f"'digits_max_len': MAX(LENGTH({trimmed})) FILTER (WHERE {is_digits})",
    ]
    return ", ".join(entries)


def is_text_code(column: ColumnStats, values: int, counts: dict) -> bool:
    """Числа в тексте, которые на деле коды: признаки те же, что у числовых
    идентификаторов (имя, unique, fixed_width), плюс ведущие нули («007»)."""
    if counts["leading_zeros"] or is_identifier_name(column.name):
        return True
    if is_measure_name(column.name):
        return False
    if values >= UNIQUE_KEY_MIN_ROWS and (column.distinct or 0) >= values:
        return True
    width = counts["digits_min_len"]
    return (
        counts["digits"] == counts["numbers"]
        and width is not None
        and width >= CODE_MIN_WIDTH
        and width == counts["digits_max_len"]
        and (column.distinct or 0) >= CODE_MIN_DISTINCT
    )


def quality_flags(column: ColumnStats, frequency: dict | None) -> list[str]:
    """Флаги качества текстовой колонки по счётчикам частотного подзапроса.

    numeric_as_text — не меньше NUMERIC_TEXT_MIN_SHARE значений (без NULL,
    литералов пропусков и значений-заголовков) приводятся к DOUBLE, и это не коды;
    null_tokens:N — N литералов из settings.csv_null_tokens;
    header_value — среди значений есть имя колонки.
    """
    if not frequency or "numbers" not in frequency:
        return []
    tokens = frequency["null_tokens"]
    headers = frequency["headers"]
    values = column.total - column.nulls - tokens - headers
    flags = []
    if (
        values > 0
        and frequency["numbers"] >= NUMERIC_TEXT_MIN_SHARE * values
        and not is_text_code(column, values, frequency)
    ):
        flags.append("numeric_as_text")
    if tokens:
        flags.append(f"null_tokens:{tokens}")
    if headers:
        flags.append("header_value")
    return flags


def flag_notes(column: ColumnStats) -> list[str]:
    """Флаги качества колонки словами: «литералы пропусков в тексте: 6»."""
    notes = []
    for flag in column.flags:
        name, _, count = flag.partition(":")
        notes.append(f"{FLAG_NOTES[name]}: {count}" if count else FLAG_NOTES[name])
    return notes


def apply_stats(index: int, column: ColumnStats, row: dict, row_count: int) -> None:
    prefix = f"c{index}"
    column.total = row_count
    column.nulls = row_count - row[f"{prefix}_count"]
    column.null_pct = percent(column.nulls, row_count)
    if column.kind == "numeric":
        if is_float(column):
            column.nan_pct = percent(row[f"{prefix}_nan"], row_count)
        column.min = plain(row[f"{prefix}_min"])
        column.max = plain(row[f"{prefix}_max"])
        column.mean = number(row[f"{prefix}_mean"])
        column.std = number(row[f"{prefix}_std"])
        column.skewness = None if column.std == 0 else number(row[f"{prefix}_skewness"])
        column.kurtosis = number(row[f"{prefix}_kurtosis"])
        levels = row[f"{prefix}_quantiles"] or [None] * len(QUANTILES)
        column.p05, column.q1, column.median, column.q3, column.p95 = (
            number(level) for level in levels
        )
        if column.q1 is not None and column.q3 is not None:
            column.iqr = column.q3 - column.q1
    elif column.kind == "temporal":
        column.min = column.min_date = row[f"{prefix}_min"]
        column.max = column.max_date = row[f"{prefix}_max"]
    elif column.kind == "text":
        column.min = plain(row[f"{prefix}_min"])
        column.max = plain(row[f"{prefix}_max"])
        column.min_len = row[f"{prefix}_min_len"]
        column.max_len = row[f"{prefix}_max_len"]


def frequency_expression(
    index: int, column: ColumnStats, source: str, top_values_limit: int
) -> str:
    identifier = quote_identifier(column.name)
    condition = f"{identifier} IS NOT NULL"
    if is_float(column):
        condition += f" AND NOT isnan({identifier})"
    quality = f", {quality_fields(column)}" if has_quality_flags(column) else ""
    return (
        "(SELECT {'distinct': COUNT(*), "
        f"'top': max_by({{'value': value, 'count': frequency}}, frequency, {top_values_limit})"
        f"{quality}}} "
        f"FROM (SELECT {identifier} AS value, COUNT(*) AS frequency FROM {source} "
        f'WHERE {condition} GROUP BY 1)) AS "c{index}_frequency"'
    )


def outlier_expression(index: int, column: ColumnStats) -> str | None:
    bounds = column.fences()
    if bounds is None:
        return None
    lower, upper = bounds
    identifier = quote_identifier(column.name)
    condition = f"({identifier} < {lower!r} OR {identifier} > {upper!r})"
    if is_float(column):
        condition += f" AND NOT isnan({identifier})"
    return f'COALESCE(count_if({condition}), 0) AS "c{index}_outliers"'


def sort_key(entry: dict) -> tuple:
    return -entry["count"], str(entry["value"])


def apply_frequency(index: int, column: ColumnStats, row: dict) -> None:
    prefix = f"c{index}"
    if f"{prefix}_outliers" in row:
        column.outlier_count = row[f"{prefix}_outliers"]
    elif column.kind == "numeric":
        column.outlier_count = 0
    frequency = row.get(f"{prefix}_frequency")
    if frequency is None:
        return
    column.distinct = frequency["distinct"]
    column.distinct_pct = percent(column.distinct, column.total)
    entries = [
        {"value": plain(entry["value"]), "count": entry["count"]}
        for entry in frequency["top"] or []
    ]
    column.top_values = sorted(entries, key=sort_key)
    column.is_constant = column.distinct == 1
    column.is_unique = column.distinct == column.total
    if column.top_values and (column.top_values[0]["count"] > 1 or column.is_constant):
        column.mode = column.top_values[0]["value"]


def profile_table(
    db, table: str, *, approx: bool = True, top_values_limit: int = 20
) -> TableProfile:
    source = quote_table(table)
    schema = db.query(f"DESCRIBE SELECT * FROM {source}")
    columns = [
        ColumnStats(
            name=entry["column_name"],
            dtype=entry["column_type"],
            kind=classify(entry["column_type"], entry["column_name"]),
        )
        for entry in schema
    ]
    for column in columns:
        if column.kind == "identifier":
            column.identifier_by = "name"
    profile = TableProfile(
        table=table,
        row_count=0,
        column_count=len(columns),
        columns=columns,
        profiled_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    if not columns:
        return profile
    use_approx = False
    if approx and any(column.kind == "numeric" for column in columns):
        counted = db.query(f"SELECT COUNT(*) AS row_count FROM {source}")
        use_approx = counted[0]["row_count"] >= settings.profile_approx_min_rows
    all_null = " AND ".join(f"{quote_identifier(column.name)} IS NULL" for column in columns)
    expressions = ["COUNT(*) AS row_count", f"COALESCE(count_if({all_null}), 0) AS empty_rows"]
    for index, column in enumerate(columns):
        expressions += stats_expressions(index, column, use_approx)
    stats = db.query(f"SELECT {', '.join(expressions)} FROM {source}")[0]
    profile.row_count = stats["row_count"]
    profile.empty_rows = stats["empty_rows"]
    for index, column in enumerate(columns):
        apply_stats(index, column, stats, profile.row_count)
    if profile.row_count == 0:
        return profile
    expressions = ["COUNT(*) AS row_count"]
    for index, column in enumerate(columns):
        if column.kind == "numeric":
            outliers = outlier_expression(index, column)
            if outliers:
                expressions.append(outliers)
        if column.kind != "other":
            expressions.append(frequency_expression(index, column, source, top_values_limit))
    frequencies = db.query(f"SELECT {', '.join(expressions)} FROM {source}")[0]
    for index, column in enumerate(columns):
        apply_frequency(index, column, frequencies)
        evidence = identifier_evidence(column)
        if evidence:
            mark_identifier(column, evidence)
        column.flags = quality_flags(column, frequencies.get(f"c{index}_frequency"))
    return profile


def outlier_summary(profile: TableProfile) -> dict[str, dict]:
    summary = {}
    for column in profile.columns:
        bounds = column.fences()
        if column.kind != "numeric" or bounds is None:
            continue
        summary[column.name] = {
            "method": f"iqr_{OUTLIER_FENCE}",
            "lower_fence": bounds[0],
            "upper_fence": bounds[1],
            "count": column.outlier_count,
        }
    return summary


def schema_overview(profile: TableProfile) -> str:
    lines = []
    for column in profile.columns:
        nulls = "—" if column.null_pct is None else f"{column.null_pct:g}%"
        lines.append(f"- {column.name}: {column.dtype} ({column.kind}), пропусков {nulls}")
    return "\n".join(lines)


def quality_notes(profile: TableProfile) -> list[str]:
    if profile.row_count == 0:
        return ["таблица пуста"]
    notes = []
    if profile.empty_rows and profile.column_count > 1:
        notes.append(f"пустых строк (все колонки NULL): {profile.empty_rows}")
    for column in profile.columns:
        label = f"«{column.name}»"
        if column.nulls == column.total:
            notes.append(f"{label}: все значения пустые")
            continue
        if column.nulls:
            notes.append(f"{label}: пропусков {column.null_pct:g}%")
        notes += [f"{label}: {note}" for note in flag_notes(column)]
        if column.nan_pct:
            notes.append(f"{label}: NaN {column.nan_pct:g}%")
        if column.is_constant and profile.row_count > 1:
            notes.append(f"{label}: константа ({column.mode})")
        if column.outlier_count:
            notes.append(f"{label}: выбросов за {OUTLIER_FENCE}·IQR — {column.outlier_count}")
        if column.identifier_by in IDENTIFIER_NOTES:
            notes.append(f"{label}: {IDENTIFIER_NOTES[column.identifier_by]}")
        if column.kind == "text" and column.is_unique and profile.row_count > 1:
            notes.append(f"{label}: все значения уникальны — похоже на идентификатор")
    return notes


def format_quality(profile: TableProfile) -> str:
    notes = quality_notes(profile)
    if not notes:
        return "- замечаний нет"
    return "\n".join(f"- {note}" for note in notes)


def column_list(labels: list[str]) -> str:
    shown = ", ".join(labels[:WARNING_COLUMNS])
    rest = len(labels) - WARNING_COLUMNS
    return f"{shown} и ещё {rest}" if rest > 0 else shown


def profile_warnings(profile: TableProfile) -> list[str]:
    """Флаги качества для карточки таблицы: одна фраза на вид флага, а не на колонку.

    Колонки идут в порядке таблицы, у литералов пропусков — по убыванию их числа.
    """
    found: dict[str, list[tuple[int, str]]] = {name: [] for name in WARNING_PHRASES}
    for column in profile.columns:
        for flag in column.flags:
            name, _, count = flag.partition(":")
            label = f"«{column.name}» ({count})" if count else f"«{column.name}»"
            found[name].append((int(count or 0), label))
    warnings = []
    for name, entries in found.items():
        if entries:
            labels = [label for _, label in sorted(entries, key=lambda entry: -entry[0])]
            warnings.append(f"{WARNING_PHRASES[name]} в колонках {column_list(labels)}.")
    if profile.empty_rows and profile.column_count > 1:
        warnings.append(f"Пустых строк (все колонки пустые): {profile.empty_rows}.")
    return warnings
