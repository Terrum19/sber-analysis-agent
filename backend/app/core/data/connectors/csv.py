import codecs
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from app.config import settings
from app.core.data.connectors.base import BaseConnector
from app.core.data.connectors.postgres import quote_identifier, quote_literal

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1 << 20
CSV_MARKER = "csv:"
DELIMITERS = (";", ",", "\t", "|")
FALLBACK_ENCODING = "cp1251"
NUMBER_PATTERN = r"[+-]?(\d{1,3}([ \x{00A0}\x{202F}]\d{3})+|\d+)([.,]\d+)?"
NUMBER_MARKERS = r"[, \x{00A0}\x{202F}]"
GROUP_SEPARATORS = (" ", " ", " ")
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

    def warnings(self) -> list[str]:
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
            names = ", ".join(f"«{column}»" for column in self.numeric_text_columns)
            notes.append(f"Числа, записанные текстом, приведены к числам в колонках {names}.")
        other_retyped = [
            column for column, kind in self.retyped.items() if kind in ("DATE", "TIMESTAMP")
        ]
        if other_retyped:
            names = ", ".join(f"«{column}»" for column in other_retyped)
            notes.append(f"Даты и время приведены к типам в колонках {names}.")
        if self.unconverted:
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


def scan(
    db,
    source: str,
    text_columns: list[str],
    null_tokens: list[str] | None = None,
    min_share: float | None = None,
) -> tuple[dict[str, str], dict[str, int], dict[str, int]]:
    if not text_columns:
        return {}, {}, {}

    tokens = settings.csv_null_tokens if null_tokens is None else null_tokens
    share_threshold = settings.csv_retype_min_share if min_share is None else min_share
    quoted_tokens = ", ".join(quote_literal(t) for t in tokens)

    probes = []
    for position, column in enumerate(text_columns):
        quoted = quote_identifier(column)
        trimmed = f"trim({quoted})"
        is_token = f"({trimmed} IN ({quoted_tokens}))" if quoted_tokens else "FALSE"
        is_filled = f"({quoted} IS NOT NULL AND {trimmed} <> '')"
        is_candidate = f"({is_filled} AND NOT {is_token})"

        cleaned_double = trimmed
        for separator in GROUP_SEPARATORS:
            cleaned_double = f"replace({cleaned_double}, '{separator}', '')"
        cleaned_double = f"replace({cleaned_double}, ',', '.')"

        probes += [
            f"count({quoted}) FILTER (WHERE {trimmed} <> '') AS filled_{position}",
            f"count_if({is_filled} AND {is_token}) AS tokens_{position}",
            f"count_if({is_candidate} AND regexp_matches({trimmed}, '^[+-]?0[0-9]+$')) AS leading_zero_{position}",
            f"count_if({is_candidate} AND regexp_full_match({trimmed}, '^[+-]?[0-9]+$') AND TRY_CAST({trimmed} AS BIGINT) IS NOT NULL) AS bigint_{position}",
            f"count_if({is_candidate} AND TRY_CAST({cleaned_double} AS DOUBLE) IS NOT NULL) AS double_{position}",
            f"count_if({is_candidate} AND regexp_full_match({trimmed}, '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$') AND TRY_CAST({trimmed} AS DATE) IS NOT NULL) AS date_{position}",
            f"count_if({is_candidate} AND NOT regexp_full_match({trimmed}, '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$') AND TRY_CAST({trimmed} AS TIMESTAMP) IS NOT NULL) AS timestamp_{position}",
        ]

    row = db.query(f"SELECT {', '.join(probes)} FROM {source}")[0]

    retyped: dict[str, str] = {}
    unconverted: dict[str, int] = {}
    columns_with_tokens: list[str] = []

    for position, column in enumerate(text_columns):
        filled = row[f"filled_{position}"] or 0
        tokens_cnt = row[f"tokens_{position}"] or 0
        candidates = filled - tokens_cnt
        if candidates <= 0:
            continue

        if tokens_cnt > 0:
            columns_with_tokens.append(column)

        leading_zero = row[f"leading_zero_{position}"] or 0
        bigint_cnt = row[f"bigint_{position}"] or 0
        double_cnt = row[f"double_{position}"] or 0
        date_cnt = row[f"date_{position}"] or 0
        timestamp_cnt = row[f"timestamp_{position}"] or 0

        if leading_zero == 0 and bigint_cnt / candidates >= share_threshold:
            retyped[column] = "BIGINT"
            if bigint_cnt < candidates:
                unconverted[column] = candidates - bigint_cnt
        elif leading_zero == 0 and double_cnt / candidates >= share_threshold:
            retyped[column] = "DOUBLE"
            if double_cnt < candidates:
                unconverted[column] = candidates - double_cnt
        elif date_cnt / candidates >= share_threshold:
            retyped[column] = "DATE"
            if date_cnt < candidates:
                unconverted[column] = candidates - date_cnt
        elif timestamp_cnt / candidates >= share_threshold:
            retyped[column] = "TIMESTAMP"
            if timestamp_cnt < candidates:
                unconverted[column] = candidates - timestamp_cnt

    counted_tokens: dict[str, int] = {}
    retyped_with_tokens = [col for col in retyped if col in columns_with_tokens]
    if retyped_with_tokens and tokens:
        token_probes = []
        for i, token in enumerate(tokens):
            sub = [
                f"count_if(trim({quote_identifier(col)}) = {quote_literal(token)})"
                for col in retyped_with_tokens
            ]
            token_probes.append(f"({' + '.join(sub)}) AS token_{i}")
        token_row = db.query(f"SELECT {', '.join(token_probes)} FROM {source}")[0]
        counted_tokens = {
            tokens[i]: token_row[f"token_{i}"]
            for i in range(len(tokens))
            if token_row[f"token_{i}"] > 0
        }

    return retyped, counted_tokens, unconverted


def retype_expression(column: str, target_type: str, null_tokens: list[str]) -> str:
    quoted = quote_identifier(column)
    trimmed = f"trim({quoted})"
    quoted_tokens = ", ".join(quote_literal(t) for t in null_tokens)
    null_cond = (
        f"{trimmed} = '' OR {trimmed} IN ({quoted_tokens})" if quoted_tokens else f"{trimmed} = ''"
    )

    if target_type == "BIGINT":
        return f"TRY_CAST(CASE WHEN {null_cond} THEN NULL ELSE {trimmed} END AS BIGINT)"
    if target_type == "DOUBLE":
        cleaned = trimmed
        for separator in GROUP_SEPARATORS:
            cleaned = f"replace({cleaned}, '{separator}', '')"
        cleaned = f"replace({cleaned}, ',', '.')"
        return f"TRY_CAST(CASE WHEN {null_cond} THEN NULL ELSE {cleaned} END AS DOUBLE)"
    if target_type == "DATE":
        return f"TRY_CAST(CASE WHEN {null_cond} THEN NULL ELSE {trimmed} END AS DATE)"
    if target_type == "TIMESTAMP":
        return f"TRY_CAST(CASE WHEN {null_cond} THEN NULL ELSE {trimmed} END AS TIMESTAMP)"
    return quoted


def number_expression(column: str) -> str:
    cleaned = f"trim({quote_identifier(column)})"
    for separator in GROUP_SEPARATORS:
        cleaned = f"replace({cleaned}, '{separator}', '')"
    return f"TRY_CAST(replace({cleaned}, ',', '.') AS DOUBLE)"


def materialize(
    db, name: str, source: str, marker: str
) -> tuple[int, dict[str, str], int, dict[str, int], int, dict[str, int]]:
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
            try:
                has_rejects = db.query(
                    "SELECT table_name FROM duckdb_tables() WHERE table_name = 'reject_errors'"
                )
                if has_rejects:
                    rejected_rows = db.query("SELECT count(*) AS cnt FROM reject_errors")[0]["cnt"]
                    db.execute(
                        "DROP TABLE IF EXISTS reject_errors; DROP TABLE IF EXISTS reject_scans"
                    )
            except duckdb.Error:
                pass

            types = column_types(db, table)
            all_columns = [col for col, _ in types]
            header_rows_removed = 0
            if all_columns:
                header_condition = " AND ".join(
                    f"trim(lower(CAST({quote_identifier(col)} AS VARCHAR))) = {quote_literal(col.strip().lower())}"
                    for col in all_columns
                )
                header_rows_removed = db.query(
                    f"SELECT count(*) AS cnt FROM {table} WHERE {header_condition}"
                )[0]["cnt"]
                if header_rows_removed > 0:
                    db.execute(f"DELETE FROM {table} WHERE {header_condition}")

            rows = db.query(f"SELECT count(*) AS total FROM {table}")[0]["total"]

            types = column_types(db, table)
            text_columns = [column for column, kind in types if kind == "VARCHAR"]
            tokens = settings.csv_null_tokens
            retyped, null_tokens_map, unconverted = scan(
                db, table, text_columns, null_tokens=tokens
            )

            for column, target_type in retyped.items():
                expr = retype_expression(column, target_type, tokens)
                db.execute(
                    f"ALTER TABLE {table} ALTER {quote_identifier(column)} "
                    f"TYPE {target_type} USING {expr}"
                )

            db.execute(f"COMMENT ON TABLE {table} IS {quote_literal(marker)}")
            db.execute("COMMIT")
        except duckdb.Error:
            db.execute("ROLLBACK")
            raise
    return rows, retyped, header_rows_removed, null_tokens_map, rejected_rows, unconverted


def load_csv(db, name: str, path: str | Path) -> CSVReport:
    original = Path(path).resolve()
    if not original.is_file():
        raise CSVLoadError(f"Файл «{original.name}» не найден.")
    if original.stat().st_size == 0:
        raise CSVLoadError(f"Файл «{original.name}» пуст.")
    readable, encoding = readable_copy(original)
    delimiter = sniffed_delimiter(db, readable)
    base = {"delim": quote_literal(delimiter)} if delimiter else {}
    marker = f"{CSV_MARKER}{original.name}"
    failure: duckdb.Error | None = None
    for strategy_index, strategy in enumerate(STRATEGIES):
        source = reader_sql(readable, base | strategy)
        try:
            (
                rows,
                retyped,
                header_rows_removed,
                null_tokens_map,
                rejected_rows,
                unconverted,
            ) = materialize(db, name, source, marker)
        except duckdb.Error as error:
            failure = error
            continue
        numeric = [col for col, kind in retyped.items() if kind in ("DOUBLE", "BIGINT")]
        report = CSVReport(
            table=name,
            encoding=encoding,
            delimiter=delimiter,
            rows=rows,
            numeric_text_columns=numeric,
            tolerant=strategy_index > 0,
            all_text=strategy_index == len(STRATEGIES) - 1,
            header_rows_removed=header_rows_removed,
            null_tokens=null_tokens_map,
            retyped=retyped,
            rejected_rows=rejected_rows,
            unconverted=unconverted,
        )
        log = logger.warning if report.tolerant else logger.info
        log(
            "CSV «%s» -> %s: %d строк, кодировка %s, разделитель %r, приведено типов %s, "
            "удалено заголовков %d, литералов %s, отброшено строк %d, режим %d",
            original.name,
            name,
            rows,
            encoding,
            delimiter,
            retyped,
            header_rows_removed,
            null_tokens_map,
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


def taken_names(db) -> set[str]:
    rows = db.query(
        "SELECT view_name AS name FROM duckdb_views() WHERE NOT internal AND schema_name = 'main' "
        "UNION ALL SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main'"
    )
    return {row["name"].lower() for row in rows}


class CSVConnector(BaseConnector):
    def __init__(self, paths: dict[str, str]) -> None:
        self.paths = paths
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
                self.reports.append(load_csv(db, name, path))
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
