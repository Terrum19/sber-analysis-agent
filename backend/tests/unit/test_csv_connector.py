import codecs
import datetime

import duckdb
import pytest

from app.core.data.connectors import csv as csv_module
from app.core.data.connectors.csv import (
    CSVConnector,
    CSVLoadError,
    detect_encoding,
    load_csv,
    table_name,
)
from app.core.data.duckdb_manager import DuckDBManager

RUSSIAN_EXPORT = (
    "Дата операции;Сумма;Клиент;Комментарий\n"
    "01.02.2024;1 234,56;Иванов;первый\n"
    "15.03.2024;-78,90;Петров;\n"
    "31.12.2024;0,5;Сидорова;последний\n"
)


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    yield manager
    manager.close()


def write(tmp_path, name: str, content: bytes):
    path = tmp_path / name
    path.write_bytes(content)
    return path


def column_types(db, table: str) -> dict[str, str]:
    rows = db.query(f'DESCRIBE "{table}"')
    return {row["column_name"]: row["column_type"] for row in rows}


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Продажи за 2024 г.csv", "prodazhi_za_2024_g"),
        ("Объём_Съёмок.CSV", "obem_semok"),
        ("2024 sales.csv", "t_2024_sales"),
        ("...csv", "csv_table"),
        ("", "csv_table"),
        ("orders.csv", "orders"),
    ],
)
def test_table_name(filename: str, expected: str) -> None:
    assert table_name(filename) == expected


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"a,b\n1,2\n", "utf-8"),
        (codecs.BOM_UTF8 + "имя\nзначение\n".encode(), "utf-8"),
        ("имя\nзначение\n".encode("utf-16"), "utf-16"),
        ("имя;сумма\nИванов;1\n".encode("cp1251"), "cp1251"),
    ],
)
def test_detect_encoding(tmp_path, content: bytes, expected: str) -> None:
    assert detect_encoding(write(tmp_path, "data.csv", content)) == expected


def test_detect_encoding_reads_past_first_chunk(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(csv_module, "CHUNK_SIZE", 8)
    content = ("a" * 13 + "\n").encode() + "ё\n".encode("cp1251")
    assert detect_encoding(write(tmp_path, "data.csv", content)) == "cp1251"


def test_cp1251_semicolon_decimal_comma_and_russian_dates(tmp_path, db) -> None:
    path = write(tmp_path, "export.csv", RUSSIAN_EXPORT.encode("cp1251"))
    report = load_csv(db, "export", path)
    assert report.encoding == "cp1251"
    assert report.delimiter == ";"
    assert report.rows == 3
    assert report.numeric_text_columns == ["Сумма"]
    assert not report.tolerant
    assert column_types(db, "export") == {
        "Дата операции": "DATE",
        "Сумма": "DOUBLE",
        "Клиент": "VARCHAR",
        "Комментарий": "VARCHAR",
    }
    rows = db.query('SELECT * FROM export ORDER BY "Дата операции"')
    assert [row["Сумма"] for row in rows] == [1234.56, -78.9, 0.5]
    assert rows[0]["Дата операции"] == datetime.date(2024, 2, 1)
    assert rows[0]["Клиент"] == "Иванов"
    assert path.read_bytes() == RUSSIAN_EXPORT.encode("cp1251")


def test_utf16_file_is_transcoded(tmp_path, db) -> None:
    path = write(tmp_path, "export.csv", RUSSIAN_EXPORT.encode("utf-16"))
    report = load_csv(db, "export", path)
    assert report.encoding == "utf-16"
    assert db.query("SELECT count(*) AS total FROM export") == [{"total": 3}]


def test_bom_timestamps_and_non_breaking_group_separator(tmp_path, db) -> None:
    content = "id;ts;v\n1;01.02.2024 10:15:00;1 234,5\n2;03.02.2024 11:00:00;7\n"
    load_csv(db, "events", write(tmp_path, "events.csv", codecs.BOM_UTF8 + content.encode()))
    assert column_types(db, "events") == {"id": "BIGINT", "ts": "TIMESTAMP", "v": "DOUBLE"}
    assert [row["v"] for row in db.query("SELECT v FROM events ORDER BY id")] == [1234.5, 7.0]


@pytest.mark.parametrize(
    "values",
    [
        ["A-001", "B-002"],
        ["007", "08"],
        ["1,5", "много"],
        ["1.5", "2.25e3x"],
    ],
)
def test_text_that_is_not_a_number_stays_text(tmp_path, db, values: list[str]) -> None:
    content = "code;n\n" + "".join(f"{value};{index}\n" for index, value in enumerate(values))
    report = load_csv(db, "codes", write(tmp_path, "codes.csv", content.encode()))
    assert report.numeric_text_columns == []
    assert column_types(db, "codes")["code"] != "DOUBLE"


def test_ragged_rows_are_padded(tmp_path, db) -> None:
    report = load_csv(db, "ragged", write(tmp_path, "ragged.csv", b"a,b\n1,2\n3,4,5\n6\n"))
    assert report.tolerant
    assert not report.all_text
    assert report.delimiter == ","
    rows = db.query("SELECT a, b FROM ragged ORDER BY a")
    assert rows == [{"a": 1, "b": 2}, {"a": 3, "b": 4}, {"a": 6, "b": None}]


def test_type_drift_after_sniff_sample_falls_back_to_text(tmp_path, db) -> None:
    lines = ["id,amount"] + [f"{index},{index}" for index in range(300000)]
    lines.append("300000,нет данных")
    path = write(tmp_path, "drift.csv", ("\n".join(lines) + "\n").encode())
    report = load_csv(db, "drift", path)
    assert report.all_text
    assert report.rows == 300001
    assert db.query("SELECT amount FROM drift WHERE id = '300000'") == [{"amount": "нет данных"}]
    assert column_types(db, "drift") == {"id": "VARCHAR", "amount": "VARCHAR"}


def test_single_column_and_header_only_files(tmp_path, db) -> None:
    single = load_csv(db, "single", write(tmp_path, "single.csv", b"name\nalpha\nbeta\n"))
    assert single.rows == 2
    header = load_csv(db, "header", write(tmp_path, "header.csv", b"a;b\n"))
    assert header.rows == 0
    assert list(column_types(db, "header")) == ["a", "b"]


def test_empty_and_missing_files_are_rejected(tmp_path, db) -> None:
    with pytest.raises(CSVLoadError, match="пуст"):
        load_csv(db, "empty", write(tmp_path, "empty.csv", b""))
    with pytest.raises(CSVLoadError, match="не найден"):
        load_csv(db, "missing", tmp_path / "missing.csv")


def test_unreadable_file_reports_reason(tmp_path, db, monkeypatch) -> None:
    def broken(db, source):
        raise duckdb.InvalidInputException("CSV Error on Line: 7\nдетали")

    monkeypatch.setattr(csv_module, "column_types", broken)
    with pytest.raises(CSVLoadError, match="Line: 7$"):
        load_csv(db, "broken", write(tmp_path, "broken.csv", b"a,b\n1,2\n"))


def test_sniffer_failure_still_loads(tmp_path, db, monkeypatch) -> None:
    query = db.query

    def failing_sniff(sql, params=None):
        if "sniff_csv" in sql:
            raise duckdb.InvalidInputException("sniffer failed")
        return query(sql, params)

    monkeypatch.setattr(db, "query", failing_sniff)
    report = load_csv(db, "plain", write(tmp_path, "plain.csv", b"a,b\n1,2\n"))
    assert report.delimiter == ""
    assert report.rows == 1


def test_path_and_name_are_quoted(tmp_path, db) -> None:
    folder = tmp_path / "it's here"
    folder.mkdir()
    path = write(folder, "data.csv", b"a\n1\n")
    load_csv(db, 'odd "name"', path)
    assert db.query('SELECT a FROM "odd ""name"""') == [{"a": 1}]


def test_attach_csv_uses_loader(tmp_path, db) -> None:
    db.attach_csv("export", str(write(tmp_path, "export.csv", RUSSIAN_EXPORT.encode("cp1251"))))
    assert column_types(db, "export")["Сумма"] == "DOUBLE"


def test_connector_keeps_other_objects_and_replaces_csv_views(tmp_path, db) -> None:
    db.execute("CREATE TABLE orders AS SELECT 1 AS id")
    first = write(tmp_path, "first.csv", b"x\n1\n")
    second = write(tmp_path, "second.csv", b"x\n2\n")
    assert CSVConnector({"orders": str(first), "items": str(first)}).connect(db) == [
        "orders_2",
        "items",
    ]
    assert CSVConnector({"items": str(second)}).connect(db) == ["items"]
    assert db.query("SELECT x FROM items") == [{"x": 2}]
    assert db.query("SELECT id FROM orders") == [{"id": 1}]


def test_connector_rolls_back_on_failure(tmp_path, db) -> None:
    good = write(tmp_path, "good.csv", b"x\n1\n")
    empty = write(tmp_path, "empty.csv", b"")
    connector = CSVConnector({"good": str(good), "bad": str(empty)})
    with pytest.raises(CSVLoadError):
        connector.connect(db)
    assert connector.reports == []
    views = db.query("SELECT view_name FROM duckdb_views() WHERE NOT internal")
    assert views == []
