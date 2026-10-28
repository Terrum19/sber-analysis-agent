import codecs
import csv
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import duckdb

from app.config import settings
from app.core.data.connectors.base import BaseConnector
from app.core.data.connectors.postgres import quote_identifier, quote_literal
from app.core.data.profiling import column_list

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1 << 20
CSV_MARKER = "csv:"
DELIMITERS = (";", ",", "\t", "|")
FALLBACK_ENCODING = "cp1251"
GROUP_SEPARATORS = (" ", " ", " ")
# Шаблоны RE2 для значений текстовых колонок (scan). Разряды отделяются группами
# ровно по 3 цифры: «+7 912 345 67 89» и «4276 1234 5678 9012» числами не считаются.
SEPARATOR = r"[ \x{00A0}\x{202F}]"
INTEGER_PATTERN = r"[+-]?\d+"
NUMBER_PATTERN = rf"[+-]?(\d{{1,3}}({SEPARATOR}\d{{3}})+|\d+)([.,]\d+)?([eE][+-]?\d+)?"
THOUSANDS_PATTERN = r"[+-]?\d{1,3},\d{3}"
ISO_DATE_PATTERN = r"\d{4}-\d{2}-\d{2}"
ISO_TIMESTAMP_PATTERN = r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?"
RU_DATE_PATTERN = r"\d{2}\.\d{2}\.\d{4}"
RU_TIMESTAMP_PATTERN = r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}(:\d{2})?"
RU_DATE_FORMAT = "%d.%m.%Y"
RU_TIMESTAMP_FORMATS = ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M")
# Целые длиннее — номера карт и счетов: в DOUBLE теряют цифры, в BIGINT их суммируют.
MAX_NUMBER_DIGITS = 15
# Вид значения в scan(). CODE — ведущий ноль («007», «007.5») или длинный номер:
# такая колонка остаётся текстом. Литерал пропуска — TOKEN + его номер в списке (с 1).
TEXT, INTEGER, DECIMAL, THOUSANDS, CODE, DATE, TIMESTAMP, BOOLEAN = range(8)
TOKEN = 100
# Виды, которые принимает каждый тип, в порядке попыток.
TARGETS = (
    ("BIGINT", {INTEGER}),
    ("DOUBLE", {INTEGER, DECIMAL, THOUSANDS}),
    ("DATE", {DATE}),
    ("TIMESTAMP", {DATE, TIMESTAMP}),
    ("BOOLEAN", {BOOLEAN}),
)
# Строк в пробном scan(): колонки, где уже есть текст, дальше не проверяются.
SAMPLE_ROWS = 2048
KIND_MACRO = "csv_cell_kind"
LATIN = "a b v g d e e zh z i y k l m n o p r s t u f kh ts ch sh shch - y - e yu ya"
TRANSLIT = {
    letter: latin.strip("-")
    for letter, latin in zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", LATIN.split(), strict=True)
}
STRATEGIES = (
    {},
    {"null_padding": "true", "strict_mode": "false"},
    {
        "all_varchar": "true",
        "null_padding": "true",
        "strict_mode": "false",
        "ignore_errors": "true",
        "store_rejects": "true",
    },
)


class CSVLoadError(ValueError):
    pass


@dataclass
class Cleanup:
    """Что scan() нашёл в таблице и что materialize() с ней сделает."""

    rows: int = 0
    header_rows: int = 0
    retyped: dict[str, str] = field(default_factory=dict)
    # Литералы пропусков, заменённые на NULL (только в приведённых колонках).
    null_tokens: dict[str, int] = field(default_factory=dict)
    unconverted: dict[str, int] = field(default_factory=dict)
    # DOUBLE-колонки, где каждое число с запятой похоже на «1,234»: тысячи или дробь.
    thousands_comma: list[str] = field(default_factory=list)


@dataclass
class CSVReport:
    table: str
    encoding: str
    delimiter: str
    rows: int
    numeric_text_columns: list[str] = field(default_factory=list)
    tolerant: bool = False
    all_text: bool = False
    header_rows_removed: int = 0
    null_tokens: dict[str, int] = field(default_factory=dict)
    retyped: dict[str, str] = field(default_factory=dict)
    rejected_rows: int = 0
    unconverted: dict[str, int] = field(default_factory=dict)
    thousands_comma: list[str] = field(default_factory=list)

    def warnings(self) -> list[str]:
        # Литералы, оставшиеся в текстовых колонках, показывает профайлер (profile_warnings).
        notes = []
        if self.encoding != "utf-8":
            notes.append(f"Файл в кодировке {self.encoding} перекодирован в UTF-8.")
        if self.header_rows_removed > 0:
            notes.append(f"Удалено строк с повторным заголовком: {self.header_rows_removed}.")
        if self.null_tokens:
            tokens_summary = ", ".join(
                f"«{token}» ({count})" for token, count in self.null_tokens.items()
            )
            notes.append(f"Литералы пропусков заменены на NULL: {tokens_summary}.")
        if self.numeric_text_columns:
            names = column_list([f"«{column}»" for column in self.numeric_text_columns])
            notes.append(f"Числа, записанные текстом, приведены к числам в колонках {names}.")
        if self.thousands_comma:
            names = column_list([f"«{column}»" for column in self.thousands_comma])
            notes.append(
                f"В колонках {names} запятая прочитана как десятичная («1,234» = 1.234); "
                "если это разделитель тысяч, значения занижены в 1000 раз."
            )
        other_retyped = [
            column
            for column, kind in self.retyped.items()
            if kind in ("DATE", "TIMESTAMP", "BOOLEAN")
        ]
        if other_retyped:
            names = column_list([f"«{column}»" for column in other_retyped])
            notes.append(f"Даты, время и логические значения приведены к типам в колонках {names}.")
        for column, count in self.unconverted.items():
            target = self.retyped.get(column, "")
            notes.append(
                f"В колонке «{column}» {count} значений не удалось привести к {target}, "
                "они заменены на NULL."
            )
        if self.rejected_rows > 0:
            notes.append(f"Отброшено строк с ошибками структуры: {self.rejected_rows}.")
        if self.all_text and not self.retyped:
            notes.append(
                "Типы колонок определить не удалось: все колонки загружены как текст, "
                "нечитаемые строки отброшены."
            )
        elif self.tolerant and not self.all_text:
            notes.append(
                "В строках с неполным числом полей недостающие значения оставлены пустыми."
            )
        return notes


def table_name(filename: str) -> str:
    stem = Path(filename).stem.lower()
    latin = "".join(TRANSLIT.get(letter, letter) for letter in stem)
    name = re.sub(r"[^a-z0-9]+", "_", latin).strip("_")
    if not name:
        return "csv_table"
    if name[0].isdigit():
        return f"t_{name}"
    return name


def detect_encoding(path: Path) -> str:
    with path.open("rb") as handle:
        head = handle.read(4)
        if head.startswith(codecs.BOM_UTF8):
            return "utf-8"
        if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            return "utf-16"
        handle.seek(0)
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            while chunk := handle.read(CHUNK_SIZE):
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            return FALLBACK_ENCODING
    return "utf-8"


def transcode(source: Path, encoding: str, target: Path) -> None:
    with (
        source.open("r", encoding=encoding, errors="replace", newline="") as reader,
        target.open("w", encoding="utf-8", newline="") as writer,
    ):
        while text := reader.read(CHUNK_SIZE):
            writer.write(text)


def readable_copy(path: Path) -> tuple[Path, str]:
    encoding = detect_encoding(path)
    if encoding == "utf-8":
        return path, encoding
    target = path.with_name(f"{path.stem}.utf8{path.suffix}")
    transcode(path, encoding, target)
    return target, encoding


def guess_delimiter(path: Path) -> str | None:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as reader:
        header = reader.readline()
    counts = {delimiter: header.count(delimiter) for delimiter in DELIMITERS}
    best = max(counts, key=counts.get)
    return best if counts[best] else None


def first_line(path: Path, delimiter: str) -> list[str]:
    """Поля первой строки файла: исходные имена колонок до переименования дублей DuckDB."""
    if not delimiter:
        return []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as reader:
        return next(csv.reader(reader, delimiter=delimiter), [])


def header_names(columns: list[str], raw: list[str]) -> list[str]:
    """Имя каждой колонки, как оно записано в повторном заголовке.

    DuckDB переименовывает дубли («a,a» → a, a_1); тогда берём имя из файла.
    Если DuckDB заголовка не нашёл (column0, ...), первая строка — данные, и она не нужна.
    """
    if len(raw) != len(columns):
        return list(columns)
    return [
        original if re.fullmatch(rf"{re.escape(original)}(_\d+)?", column) else column
        for column, original in zip(columns, raw, strict=True)
    ]


def reader_sql(path: Path, options: dict[str, str]) -> str:
    arguments = [quote_literal(str(path))]
    arguments += [f"{key}={value}" for key, value in options.items()]
    return f"read_csv({', '.join(arguments)})"


def sniffed_delimiter(db, path: Path) -> str:
    try:
        rows = db.query(f"SELECT Delimiter, Columns FROM sniff_csv({quote_literal(str(path))})")
    except duckdb.Error:
        return ""
    sniffed = rows[0]
    if len(sniffed["Columns"]) > 1:
        return sniffed["Delimiter"]
    return guess_delimiter(path) or sniffed["Delimiter"]


def column_types(db, source: str) -> list[tuple[str, str]]:
    rows = db.query(f"DESCRIBE SELECT * FROM {source}")
    return [(row["column_name"], row["column_type"]) for row in rows]


def cell(column: str) -> str:
    return f"NULLIF(trim(CAST({quote_identifier(column)} AS VARCHAR)), '')"


def strip_separators(value: str) -> str:
    for separator in GROUP_SEPARATORS:
        value = f"replace({value}, '{separator}', '')"
    return value


def kind_macro(tokens: list[str]) -> str:
    """Макрос: вид значения (TEXT, INTEGER, ...) по уже обрезанной непустой строке."""
    token_list = f"[{', '.join(quote_literal(token) for token in tokens)}]::VARCHAR[]"
    unsigned = "ltrim(v, '+-')"
    leading_zero = f"({unsigned}[1] = '0' AND {unsigned}[2] BETWEEN '0' AND '9')"
    digits = f"length(regexp_extract({strip_separators('v')}, '[0-9]+'))"
    ru_timestamps = ", ".join(quote_literal(fmt) for fmt in RU_TIMESTAMP_FORMATS)
    return f"""
        CREATE OR REPLACE TEMP MACRO {KIND_MACRO}(v) AS CASE
        WHEN v IS NULL THEN NULL
        WHEN list_contains({token_list}, v) THEN {TOKEN} + list_position({token_list}, v)
        WHEN regexp_full_match(v, '{INTEGER_PATTERN}') THEN CASE
            WHEN {leading_zero} OR length({unsigned}) > {MAX_NUMBER_DIGITS} THEN {CODE}
            ELSE {INTEGER} END
        WHEN regexp_full_match(v, '{NUMBER_PATTERN}') THEN CASE
            WHEN {leading_zero} OR {digits} > {MAX_NUMBER_DIGITS} THEN {CODE}
            WHEN regexp_full_match(v, '{THOUSANDS_PATTERN}') THEN {THOUSANDS}
            ELSE {DECIMAL} END
        WHEN regexp_full_match(v, '{ISO_DATE_PATTERN}') THEN
            CASE WHEN TRY_CAST(v AS DATE) IS NULL THEN {TEXT} ELSE {DATE} END
        WHEN regexp_full_match(v, '{RU_DATE_PATTERN}') THEN
            CASE WHEN try_strptime(v, '{RU_DATE_FORMAT}') IS NULL THEN {TEXT} ELSE {DATE} END
        WHEN regexp_full_match(v, '{ISO_TIMESTAMP_PATTERN}') THEN
            CASE WHEN TRY_CAST(v AS TIMESTAMP) IS NULL THEN {TEXT} ELSE {TIMESTAMP} END
        WHEN regexp_full_match(v, '{RU_TIMESTAMP_PATTERN}') THEN
            CASE WHEN try_strptime(v, [{ru_timestamps}]) IS NULL THEN {TEXT} ELSE {TIMESTAMP} END
        WHEN lower(v) IN ('true', 'false') THEN {BOOLEAN}
        ELSE {TEXT} END
    """


def header_condition(columns: list[str], names: list[str]) -> str:
    """Строка — повторный заголовок: непустые ячейки (хотя бы половина) равны именам колонок.

    Ячейки собираются в список, а не в цепочку AND: на ~1000 колонках цепочка
    упирается в предел глубины выражения DuckDB.
    """
    cells = [f"lower({cell(column)})" for column in columns]
    expected = [quote_literal(name.strip().lower()) for name in names]
    matches = [f"coalesce({value} = {name}, true)" for value, name in zip(cells, expected)]
    # Дешёвый отсев по первой колонке: у обычной строки там не имя колонки.
    return (
        f"CASE WHEN {matches[0]} THEN "
        f"list_aggregate(list_value({', '.join(matches)}), 'bool_and') "
        f"AND list_count(list_value({', '.join(cells)})) * 2 >= {len(columns)} "
        "ELSE false END"
    )


def kind_counts(
    db, table: str, columns: list[str], header: str, limit: int | None
) -> list[dict[int, int]]:
    """Виды значений по колонкам (без строк-заголовков).

    UNPIVOT сводит все колонки в одну: макрос и агрегат в запросе по одному,
    а не на каждую колонку — иначе на сотнях колонок план съедает гигабайты.
    Вид определяется по различным значениям, а не по каждой ячейке.
    """
    kinds: list[dict[int, int]] = [{} for _ in columns]
    if not columns:
        return kinds
    cells = [f"{cell(column)} AS v{position}" for position, column in enumerate(columns)]
    sample = f" LIMIT {limit}" if limit else ""
    rows = db.query(
        f"SELECT position, {KIND_MACRO}(value) AS kind, sum(total)::BIGINT AS total FROM ("
        "SELECT position, value, count(*) AS total FROM ("
        f"UNPIVOT (SELECT {', '.join(cells)} FROM {table} WHERE NOT ({header}){sample}) "
        f"ON {', '.join(f'v{position}' for position in range(len(columns)))} "
        "INTO NAME position VALUE value) GROUP BY ALL) GROUP BY ALL"
    )
    for row in rows:
        kinds[int(row["position"][1:])][row["kind"]] = row["total"]
    return kinds


def row_counts(db, table: str, header: str) -> tuple[int, int]:
    """Строки таблицы и повторные заголовки среди них."""
    row = db.query(
        f"SELECT count(*) AS total, coalesce(count_if({header}), 0) AS headers FROM {table}"
    )[0]
    return row["total"], row["headers"]


def choose_type(kinds: dict[int, int], min_share: float) -> tuple[str, int] | None:
    """Тип колонки по счётчикам видов и число значений, которые к нему не приводятся."""
    candidates = sum(count for kind, count in kinds.items() if kind < TOKEN)
    if candidates == 0 or kinds.get(CODE):
        return None
    for target, accepted in TARGETS:
        if target == "TIMESTAMP" and not kinds.get(TIMESTAMP):
            continue
        valid = sum(kinds.get(kind, 0) for kind in accepted)
        if valid / candidates >= min_share:
            return target, candidates - valid
    return None


def scan(
    db,
    table: str,
    types: list[tuple[str, str]],
    names: list[str],
    null_tokens: list[str] | None = None,
    min_share: float | None = None,
) -> Cleanup:
    """Найти повторные заголовки и типы для текстовых колонок (без изменения таблицы)."""
    tokens = settings.csv_null_tokens if null_tokens is None else null_tokens
    share = settings.csv_retype_min_share if min_share is None else min_share
    columns = [column for column, _ in types]
    header = header_condition(columns, names)
    text_columns = [column for column, kind in types if kind == "VARCHAR"]
    db.execute(kind_macro(tokens))
    if text_columns and share >= 1.0:
        # При пороге 1.0 один текст в пробе уже оставляет колонку текстом.
        sample = kind_counts(db, table, text_columns, header, SAMPLE_ROWS)
        text_columns = [
            column
            for column, kinds in zip(text_columns, sample, strict=True)
            if not kinds.get(TEXT) and not kinds.get(CODE)
        ]
    total, headers = row_counts(db, table, header)
    cleanup = Cleanup(rows=total - headers, header_rows=headers)
    counts = kind_counts(db, table, text_columns, header, None)
    for column, kinds in zip(text_columns, counts, strict=True):
        chosen = choose_type(kinds, share)
        if chosen is None:
            continue
        target, unconverted = chosen
        cleanup.retyped[column] = target
        if unconverted:
            cleanup.unconverted[column] = unconverted
        if target == "DOUBLE" and kinds.get(THOUSANDS) and not kinds.get(DECIMAL):
            cleanup.thousands_comma.append(column)
        for kind, count in kinds.items():
            if kind > TOKEN:
                token = tokens[kind - TOKEN - 1]
                cleanup.null_tokens[token] = cleanup.null_tokens.get(token, 0) + count
    db.execute(f"DROP MACRO IF EXISTS {KIND_MACRO}")
    return cleanup


def retype_expression(column: str, target: str, null_tokens: list[str], strict: bool) -> str:
    """Значение колонки в новом типе; литералы пропусков и (strict) неподходящие — NULL.

    strict нужен, только если в колонке есть неприводимые значения (порог < 1.0):
    без него TRY_CAST принял бы то, что scan() не засчитал («1_000», «inf»).
    """
    value = cell(column)
    patterns = {
        "BIGINT": [INTEGER_PATTERN],
        "DOUBLE": [NUMBER_PATTERN],
        "DATE": [ISO_DATE_PATTERN, RU_DATE_PATTERN],
        "TIMESTAMP": [
            ISO_DATE_PATTERN,
            ISO_TIMESTAMP_PATTERN,
            RU_DATE_PATTERN,
            RU_TIMESTAMP_PATTERN,
        ],
    }
    if target == "BOOLEAN":
        valid, converted = f"lower({value}) IN ('true', 'false')", value
    else:
        valid = " OR ".join(
            f"regexp_full_match({value}, '{pattern}')" for pattern in patterns[target]
        )
        converted = value
    if target == "DOUBLE":
        converted = f"replace({strip_separators(value)}, ',', '.')"
    elif target in ("DATE", "TIMESTAMP"):
        # ISO читает CAST, «дд.мм.гггг [чч:мм[:сс]]» — strptime.
        formats = ", ".join(quote_literal(fmt) for fmt in (*RU_TIMESTAMP_FORMATS, RU_DATE_FORMAT))
        converted = f"coalesce(TRY_CAST({value} AS {target}), try_strptime({value}, [{formats}]))"
    expression = f"TRY_CAST({converted} AS {target})"
    conditions = []
    if null_tokens:
        # list_contains, а не IN (...): IN раскрывается в цепочку сравнений и на сотне
        # колонок пересборка таблицы идёт в разы дольше и съедает сотни мегабайт.
        tokens = ", ".join(quote_literal(token) for token in null_tokens)
        conditions.append(f"WHEN list_contains([{tokens}]::VARCHAR[], {value}) THEN NULL")
    if strict:
        conditions.append(f"WHEN NOT ({valid}) THEN NULL")
    if not conditions:
        return expression
    return f"CASE {' '.join(conditions)} ELSE {expression} END"


def materialize(
    db,
    name: str,
    source: str,
    marker: str,
    raw_header: list[str],
    rejects: tuple[str, str] | None = None,
) -> tuple[Cleanup, int]:
    """Таблица из CSV, очищенная от повторных заголовков и с типами текстовых колонок."""
    table = quote_identifier(name)
    with db.lock:
        db.execute("BEGIN TRANSACTION")
        try:
            legacy = db.query(
                "SELECT view_name FROM duckdb_views() "
                "WHERE NOT internal AND schema_name = 'main' AND lower(view_name) = lower($name)",
                {"name": name},
            )
            if legacy:
                db.execute(f"DROP VIEW {table}")
            db.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM {source}")

            rejected_rows = 0
            if rejects:
                errors, scans = (quote_identifier(item) for item in rejects)
                rejected_rows = db.query(
                    f"SELECT count(DISTINCT (scan_id, file_id, line)) AS cnt FROM {errors}"
                )[0]["cnt"]
                db.execute(f"DROP TABLE {errors}; DROP TABLE {scans}")

            types = column_types(db, table)
            columns = [column for column, _ in types]
            tokens = settings.csv_null_tokens
            names = header_names(columns, raw_header)
            cleanup = scan(db, table, types, names, tokens)
            if cleanup.header_rows or cleanup.retyped:
                select = [quote_identifier(column) for column in columns]
                for position, column in enumerate(columns):
                    if column in cleanup.retyped:
                        strict = column in cleanup.unconverted
                        expression = retype_expression(
                            column, cleanup.retyped[column], tokens, strict
                        )
                        select[position] = f"{expression} AS {select[position]}"
                where = (
                    f" WHERE NOT ({header_condition(columns, names)})"
                    if cleanup.header_rows
                    else ""
                )
                db.execute(
                    f"CREATE OR REPLACE TABLE {table} AS "
                    f"SELECT {', '.join(select)} FROM {table}{where}"
                )

            db.execute(f"COMMENT ON TABLE {table} IS {quote_literal(marker)}")
            db.execute("COMMIT")
        except duckdb.Error:
            db.execute("ROLLBACK")
            raise
    return cleanup, rejected_rows


def source_marker(filename: str, datasource: str | None = None) -> str:
    """Комментарий CSV-таблицы: файл и (если есть) id источника, которому она принадлежит."""
    return f"{CSV_MARKER}{datasource}/{filename}" if datasource else f"{CSV_MARKER}{filename}"


def rejects_options(strategy: dict[str, str]) -> tuple[dict[str, str], tuple[str, str] | None]:
    """Свои имена таблиц ошибок на каждую загрузку: reject_errors в базе не трогаем."""
    if "store_rejects" not in strategy:
        return {}, None
    suffix = uuid4().hex
    rejects = (f"csv_reject_errors_{suffix}", f"csv_reject_scans_{suffix}")
    options = {
        "rejects_table": quote_literal(rejects[0]),
        "rejects_scan": quote_literal(rejects[1]),
    }
    return options, rejects


def load_csv(db, name: str, path: str | Path, datasource: str | None = None) -> CSVReport:
    original = Path(path).resolve()
    if not original.is_file():
        raise CSVLoadError(f"Файл «{original.name}» не найден.")
    if original.stat().st_size == 0:
        raise CSVLoadError(f"Файл «{original.name}» пуст.")
    readable, encoding = readable_copy(original)
    delimiter = sniffed_delimiter(db, readable)
    base = {"delim": quote_literal(delimiter)} if delimiter else {}
    raw_header = first_line(readable, delimiter)
    marker = source_marker(original.name, datasource)
    failure: duckdb.Error | None = None
    for strategy_index, strategy in enumerate(STRATEGIES):
        options, rejects = rejects_options(strategy)
        source = reader_sql(readable, base | strategy | options)
        try:
            cleanup, rejected_rows = materialize(db, name, source, marker, raw_header, rejects)
        except duckdb.Error as error:
            failure = error
            continue
        numeric = [col for col, kind in cleanup.retyped.items() if kind in ("DOUBLE", "BIGINT")]
        report = CSVReport(
            table=name,
            encoding=encoding,
            delimiter=delimiter,
            rows=cleanup.rows,
            numeric_text_columns=numeric,
            tolerant=strategy_index > 0,
            all_text=strategy_index == len(STRATEGIES) - 1,
            header_rows_removed=cleanup.header_rows,
            null_tokens=cleanup.null_tokens,
            retyped=cleanup.retyped,
            rejected_rows=rejected_rows,
            unconverted=cleanup.unconverted,
            thousands_comma=cleanup.thousands_comma,
        )
        log = logger.warning if report.tolerant else logger.info
        log(
            "CSV «%s» -> %s: %d строк, кодировка %s, разделитель %r, приведено типов %s, "
            "удалено заголовков %d, литералов %s, отброшено строк %d, режим %d",
            original.name,
            name,
            report.rows,
            encoding,
            delimiter,
            report.retyped,
            report.header_rows_removed,
            report.null_tokens,
            rejected_rows,
            strategy_index,
        )
        return report
    detail = str(failure).splitlines()[0] if failure else ""
    raise CSVLoadError(f"Не удалось прочитать «{original.name}» как CSV: {detail}")


def csv_tables(db) -> set[str]:
    rows = db.query(
        "SELECT table_name AS name FROM duckdb_tables() "
        "WHERE schema_name = 'main' AND starts_with(comment, $marker) "
        "UNION ALL SELECT view_name FROM duckdb_views() "
        "WHERE NOT internal AND schema_name = 'main' AND contains(sql, 'read_csv(')",
        {"marker": CSV_MARKER},
    )
    return {row["name"].lower() for row in rows}


def datasource_tables(db, datasource: str, legacy_files: Sequence[str] = ()) -> list[str]:
    """CSV-таблицы источника по комментарию «csv:<datasource>/<файл>».

    legacy_files — файлы источника, загруженного до появления id в комментарии:
    его таблицы помечены просто «csv:<файл>».
    """
    markers = [source_marker(filename) for filename in legacy_files]
    rows = db.query(
        "SELECT table_name AS name FROM duckdb_tables() "
        "WHERE schema_name = 'main' AND (starts_with(comment, $prefix) "
        "OR list_contains($legacy::VARCHAR[], comment)) ORDER BY table_name",
        {"prefix": source_marker("", datasource), "legacy": markers},
    )
    return [row["name"] for row in rows]


def taken_names(db) -> set[str]:
    rows = db.query(
        "SELECT view_name AS name FROM duckdb_views() WHERE NOT internal AND schema_name = 'main' "
        "UNION ALL SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main'"
    )
    return {row["name"].lower() for row in rows}


class CSVConnector(BaseConnector):
    def __init__(self, paths: dict[str, str], datasource: str | None = None) -> None:
        self.paths = paths
        self.datasource = datasource
        self.reports: list[CSVReport] = []

    def connect(self, db) -> list[str]:
        replaceable = csv_tables(db)
        taken = taken_names(db) - replaceable
        self.reports = []
        for wanted, path in self.paths.items():
            name = wanted
            suffix = 2
            while name.lower() in taken:
                name = f"{wanted}_{suffix}"
                suffix += 1
            taken.add(name.lower())
            try:
                self.reports.append(load_csv(db, name, path, self.datasource))
            except CSVLoadError:
                self.rollback(db)
                raise
        return [report.table for report in self.reports]

    def column_comments(self, db, table: str) -> dict[str, str]:
        rows = db.query(
            "SELECT column_name, comment FROM duckdb_columns() "
            "WHERE database_name = current_database() AND schema_name = 'main' "
            "AND table_name = $table AND comment IS NOT NULL",
            {"table": table},
        )
        return {row["column_name"]: row["comment"] for row in rows}

    def warnings(self, table: str) -> list[str]:
        for report in self.reports:
            if report.table == table:
                return report.warnings()
        return []

    def rollback(self, db) -> None:
        for report in self.reports:
            db.execute(f"DROP TABLE IF EXISTS {quote_identifier(report.table)}")
        self.reports = []
