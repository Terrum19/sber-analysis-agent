import codecs
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from app.core.data.connectors.base import BaseConnector
from app.core.data.connectors.postgres import quote_identifier, quote_literal

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1 << 20
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


def scan(db, source: str, text_columns: list[str]) -> tuple[int, list[str]]:
    probes = ["count(row(*COLUMNS(*))) AS total"]
    for position, column in enumerate(text_columns):
        quoted = quote_identifier(column)
        trimmed = f"trim({quoted})"
        probes += [
            f"count({quoted}) FILTER (WHERE {trimmed} <> '') AS filled_{position}",
            f"count_if(regexp_full_match({trimmed}, '{NUMBER_PATTERN}')) AS numeric_{position}",
            f"count_if(regexp_matches({trimmed}, '{NUMBER_MARKERS}')) AS marked_{position}",
        ]
    row = db.query(f"SELECT {', '.join(probes)} FROM {source}")[0]
    numeric = [
        column
        for position, column in enumerate(text_columns)
        if row[f"filled_{position}"]
        and row[f"numeric_{position}"] == row[f"filled_{position}"]
        and row[f"marked_{position}"]
    ]
    return row["total"], numeric


def number_expression(column: str) -> str:
    cleaned = f"trim({quote_identifier(column)})"
    for separator in GROUP_SEPARATORS:
        cleaned = f"replace({cleaned}, '{separator}', '')"
    return f"TRY_CAST(replace({cleaned}, ',', '.') AS DOUBLE) AS {quote_identifier(column)}"


def view_sql(source: str, numeric: list[str]) -> str:
    if not numeric:
        return f"SELECT * FROM {source}"
    replaced = ", ".join(number_expression(column) for column in numeric)
    return f"SELECT * REPLACE ({replaced}) FROM {source}"


def load_csv(db, name: str, path: str | Path) -> CSVReport:
    original = Path(path).resolve()
    if not original.is_file():
        raise CSVLoadError(f"Файл «{original.name}» не найден.")
    if original.stat().st_size == 0:
        raise CSVLoadError(f"Файл «{original.name}» пуст.")
    readable, encoding = readable_copy(original)
    delimiter = sniffed_delimiter(db, readable)
    base = {"delim": quote_literal(delimiter)} if delimiter else {}
    failure: duckdb.Error | None = None
    for strategy_index, strategy in enumerate(STRATEGIES):
        source = reader_sql(readable, base | strategy)
        try:
            types = column_types(db, source)
            text_columns = [column for column, kind in types if kind == "VARCHAR"]
            rows, numeric = scan(db, source, text_columns)
        except duckdb.Error as error:
            failure = error
            continue
        db.execute(
            f"CREATE OR REPLACE VIEW {quote_identifier(name)} AS {view_sql(source, numeric)}"
        )
        report = CSVReport(
            table=name,
            encoding=encoding,
            delimiter=delimiter,
            rows=rows,
            numeric_text_columns=numeric,
            tolerant=strategy_index > 0,
            all_text=strategy_index == len(STRATEGIES) - 1,
        )
        log = logger.warning if report.tolerant else logger.info
        log(
            "CSV «%s» -> %s: %d строк, кодировка %s, разделитель %r, числа из текста %s, режим %d",
            original.name,
            name,
            rows,
            encoding,
            delimiter,
            numeric,
            strategy_index,
        )
        return report
    detail = str(failure).splitlines()[0] if failure else ""
    raise CSVLoadError(f"Не удалось прочитать «{original.name}» как CSV: {detail}")


def csv_tables(db) -> set[str]:
    rows = db.query(
        "SELECT view_name FROM duckdb_views() "
        "WHERE NOT internal AND schema_name = 'main' AND contains(sql, 'read_csv(')"
    )
    return {row["view_name"].lower() for row in rows}


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

    def rollback(self, db) -> None:
        for report in self.reports:
            db.execute(f"DROP VIEW IF EXISTS {quote_identifier(report.table)}")
        self.reports = []
