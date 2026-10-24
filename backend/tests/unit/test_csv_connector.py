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
    assert column_types(db, "drift") == {"id": "BIGINT", "amount": "VARCHAR"}


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


def test_connector_keeps_other_objects_and_replaces_csv_tables(tmp_path, db) -> None:
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
    assert db.query("SELECT table_name FROM duckdb_tables()") == []
    assert db.query("SELECT view_name FROM duckdb_views() WHERE NOT internal") == []


def test_csv_is_copied_into_a_marked_table(tmp_path, db) -> None:
    path = write(tmp_path, "export.csv", RUSSIAN_EXPORT.encode("cp1251"))
    load_csv(db, "export", path)
    path.unlink()
    tmp_path.joinpath("export.utf8.csv").unlink()
    tables = db.query("SELECT table_name, comment FROM duckdb_tables()")
    assert tables == [{"table_name": "export", "comment": "csv:export.csv"}]
    assert db.query("SELECT view_name FROM duckdb_views() WHERE NOT internal") == []
    assert db.query('SELECT round(sum("Сумма"), 2) AS total FROM export') == [{"total": 1156.16}]


def test_reload_replaces_legacy_csv_view(tmp_path, db) -> None:
    path = write(tmp_path, "items.csv", b"x\n7\n")
    db.execute(f"CREATE VIEW items AS SELECT * FROM read_csv('{path}')")
    assert CSVConnector({"items": str(path)}).connect(db) == ["items"]
    assert db.query("SELECT view_name FROM duckdb_views() WHERE NOT internal") == []
    assert db.query("SELECT table_name FROM duckdb_tables()") == [{"table_name": "items"}]


def test_failed_reload_keeps_previous_table(tmp_path, db, monkeypatch) -> None:
    load_csv(db, "items", write(tmp_path, "items.csv", b"x\n1\n"))

    def broken_scan(*args, **kwargs):
        raise duckdb.InvalidInputException("broken")

    monkeypatch.setattr(csv_module, "scan", broken_scan)
    with pytest.raises(CSVLoadError, match="broken"):
        load_csv(db, "items", write(tmp_path, "other.csv", b"x\n2\n"))
    assert db.query("SELECT x FROM items") == [{"x": 1}]
    assert db.query("SELECT comment FROM duckdb_tables()") == [{"comment": "csv:items.csv"}]


def test_column_comments_come_from_duckdb_catalog(tmp_path, db) -> None:
    connector = CSVConnector({"items": str(write(tmp_path, "items.csv", b"x,y\n1,2\n"))})
    connector.connect(db)
    assert connector.column_comments(db, "items") == {}
    db.execute("COMMENT ON COLUMN items.x IS 'Количество, шт.'")
    db.execute("CREATE SCHEMA other")
    db.execute("CREATE TABLE other.items (y INTEGER)")
    db.execute("COMMENT ON COLUMN other.items.y IS 'чужая таблица'")
    assert connector.column_comments(db, "items") == {"x": "Количество, шт."}
    assert connector.column_comments(db, "missing") == {}


def test_stitched_csv_with_repeated_header_and_null_literals(tmp_path, db) -> None:
    content = "id,amount,score\n1,100,5\nid,amount,score\nNULL,NULL,NULL\n2,200,10\n"
    path = write(tmp_path, "stitched.csv", content.encode())
    report = load_csv(db, "stitched", path)

    assert report.header_rows_removed == 1
    assert report.rows == 3
    assert report.null_tokens == {"NULL": 3}
    assert column_types(db, "stitched") == {
        "id": "BIGINT",
        "amount": "BIGINT",
        "score": "BIGINT",
    }
    rows = db.query("SELECT * FROM stitched ORDER BY id NULLS LAST")
    assert rows[0] == {"id": 1, "amount": 100, "score": 5}
    assert rows[1] == {"id": 2, "amount": 200, "score": 10}
    assert rows[2] == {"id": None, "amount": None, "score": None}
    assert any("Удалено строк с повторным заголовком: 1" in w for w in report.warnings())
    assert any("Литералы пропусков заменены на NULL" in w for w in report.warnings())


def test_country_column_with_na_stays_text(tmp_path, db) -> None:
    content = "country,users\nUS,100\nNA,24\nDE,50\n"
    path = write(tmp_path, "countries.csv", content.encode())
    load_csv(db, "countries", path)

    assert column_types(db, "countries")["country"] == "VARCHAR"
    assert column_types(db, "countries")["users"] == "BIGINT"
    rows = db.query("SELECT country FROM countries ORDER BY users")
    assert [r["country"] for r in rows] == ["NA", "DE", "US"]


def test_leading_zero_codes_stay_text(tmp_path, db) -> None:
    content = "code,rank\n007,1\n08,2\n15,3\n"
    path = write(tmp_path, "codes_leading.csv", content.encode())
    load_csv(db, "codes_leading", path)

    assert column_types(db, "codes_leading")["code"] == "VARCHAR"
    assert column_types(db, "codes_leading")["rank"] == "BIGINT"
    rows = db.query("SELECT code FROM codes_leading ORDER BY rank")
    assert [r["code"] for r in rows] == ["007", "08", "15"]


def test_decimal_comma_numbers_converted_to_double(tmp_path, db) -> None:
    content = "val;factor\n1 234,56;2\n-78,90;3\n0,5;4\n"
    path = write(tmp_path, "decimals.csv", content.encode())
    load_csv(db, "decimals", path)

    assert column_types(db, "decimals")["val"] == "DOUBLE"
    assert column_types(db, "decimals")["factor"] == "BIGINT"
    rows = db.query("SELECT val FROM decimals ORDER BY factor")
    assert [r["val"] for r in rows] == [1234.56, -78.9, 0.5]


def test_rejected_rows_counted_with_store_rejects(tmp_path, db) -> None:
    lines = [f"{i},{i}" for i in range(25000)]
    lines.append("1,drift_to_trigger_strategy_3")
    lines.append("bad_line,\xff\xfe\xfd")
    lines.append("99999,99999")
    content = codecs.BOM_UTF8 + ("a,b\n" + "\n".join(lines) + "\n").encode("latin1")
    path = tmp_path / "broken_rows.csv"
    path.write_bytes(content)
    report = load_csv(db, "broken_rows", path)

    assert report.all_text
    assert report.rejected_rows == 1
    assert any("Отброшено строк с ошибками структуры: 1" in w for w in report.warnings())


def test_iso_dates_and_timestamps_retyped(tmp_path, db) -> None:
    content = (
        "dt,ts,note\n"
        "2024-01-02,2024-01-02 10:15:00,a\n"
        "2024-03-15,2024-03-15 11:00:00,b\n"
        "NULL,None,c\n"
    )
    path = write(tmp_path, "dates.csv", content.encode())
    report = load_csv(db, "dates", path)

    assert column_types(db, "dates")["dt"] == "DATE"
    assert column_types(db, "dates")["ts"] == "TIMESTAMP"
    assert column_types(db, "dates")["note"] == "VARCHAR"
    assert report.rows == 3


def test_retype_min_share_threshold(tmp_path, db, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "csv_retype_min_share", 0.75)
    # 4 значения: 3 числа и 1 опечатка 'xyz' (доля 75%)
    content = "col\n10\n20\n30\nxyz\n"
    path = write(tmp_path, "share.csv", content.encode())
    report = load_csv(db, "share", path)

    assert column_types(db, "share")["col"] == "BIGINT"
    assert report.unconverted == {"col": 1}
    rows = db.query("SELECT col FROM share ORDER BY col NULLS LAST")
    assert [r["col"] for r in rows] == [10, 20, 30, None]
    assert any("1 значений не удалось привести к BIGINT" in w for w in report.warnings())


def test_null_in_text_column_counted_in_report(tmp_path, db) -> None:
    content = "country,note\nNA,NULL\nUS,comment\n"
    path = write(tmp_path, "text_nulls.csv", content.encode())
    report = load_csv(db, "text_nulls", path)

    assert column_types(db, "text_nulls")["country"] == "VARCHAR"
    assert column_types(db, "text_nulls")["note"] == "VARCHAR"
    assert report.null_tokens == {"NULL": 1}
    assert report.retyped == {}
    rows = db.query("SELECT note FROM text_nulls ORDER BY country")
    assert [r["note"] for r in rows] == ["NULL", "comment"]
    assert any("Обнаружены литералы пропусков: «NULL» (1)" in w for w in report.warnings())


def test_nbsp_and_narrow_nbsp_without_decimal_comma_converted_to_double(tmp_path, db) -> None:
    content = "val;rank\n1\u00a0234;1\n5\u202f678;2\n"
    path = write(tmp_path, "nbsp_nums.csv", content.encode())
    load_csv(db, "nbsp_nums", path)

    assert column_types(db, "nbsp_nums")["val"] == "DOUBLE"
    assert column_types(db, "nbsp_nums")["rank"] == "BIGINT"
    rows = db.query("SELECT val FROM nbsp_nums ORDER BY rank")
    assert [r["val"] for r in rows] == [1234.0, 5678.0]


def test_openpsych_structure_simulation(tmp_path, db) -> None:
    # Полная симуляция структуры openPsych: ровно 105 колонок,
    # QA/QI/QE/VCL/метаданные/age, country с NA, повторный заголовок, строки NULL
    qa = [f"Q{i}A" for i in range(1, 21)]
    qi = [f"Q{i}I" for i in range(1, 21)]
    qe = [f"Q{i}E" for i in range(1, 21)]
    vcl = [f"VCL{i}" for i in range(1, 17)]
    meta = [
        "introelapse",
        "testelapse",
        "surveyelapse",
        "age",
        "gender",
        "engnat",
        "hand",
        "religion",
        "orientation",
        "race",
        "voted",
        "married",
        "familysize",
    ]
    extra = [f"scale_{i}" for i in range(15)]
    cols = qa + qi + qe + vcl + meta + extra + ["country"]
    assert len(cols) == 105

    header = ",".join(cols)
    rows_data = [
        ",".join(["1"] * 104 + ["US"]),
        ",".join(["2"] * 104 + ["NA"]),
        header,  # повторный заголовок
        ",".join(["NULL"] * 104 + ["DE"]),  # строка с литералами NULL
        ",".join(["5"] * 104 + ["GB"]),
    ]
    content = "\n".join([header] + rows_data) + "\n"
    path = write(tmp_path, "openpsych_105.csv", content.encode())
    report = load_csv(db, "openpsych_105", path)

    assert report.header_rows_removed == 1
    assert report.rows == 4
    types = column_types(db, "openpsych_105")
    assert types["Q1A"] == "BIGINT"
    assert types["Q1I"] == "BIGINT"
    assert types["Q1E"] == "BIGINT"
    assert types["VCL2"] == "BIGINT"
    assert types["age"] == "BIGINT"
    assert types["country"] == "VARCHAR"
    assert len(report.retyped) == 104
    assert report.null_tokens.get("NULL") == 104
    max_vcl2 = db.query("SELECT max(VCL2) AS m FROM openpsych_105")[0]["m"]
    assert max_vcl2 == 5
    rows = db.query("SELECT country FROM openpsych_105 ORDER BY country")
    assert [r["country"] for r in rows] == ["DE", "GB", "NA", "US"]
