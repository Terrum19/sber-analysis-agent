"""Тесты экспорта исследований и дашбордов в Excel (openpyxl).

Проверяет:
1. Одиночные исследования: стили Сбера, автоширина, формулы, типы графиков, метаданные.
2. Дашборды: лист «Сводка» с KPI-карточками, ссылки, отдельные листы по каждой плитке.
3. Безопасность: Formula Injection, спецсимволы в именах листов.
4. API-эндпоинты: POST /api/artifacts/export (result_ref и dashboard_id),
   GET /api/dashboards/{id}/export, обработку ошибок 400, 404, 501.
"""

from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient
from openpyxl.chart import BarChart, LineChart, PieChart

from app.core.dashboards.models import Dashboard, DashboardTile
from app.core.dashboards.store import DashboardStore
from app.core.data.duckdb_manager import DuckDBManager
from app.core.export.excel import (
    SBER_GREEN,
    ExcelExporter,
    _detect_chart_type,
    _detect_column_roles,
    _format_kpi_number,
    _safe_sheet_title,
    _sanitize_value,
)
from app.main import app


@pytest.fixture
def exporter() -> ExcelExporter:
    return ExcelExporter()


@pytest.fixture
def test_db(tmp_path: Path) -> DuckDBManager:
    return DuckDBManager(str(tmp_path / "test.duckdb"))


@pytest.fixture
def client(test_db: DuckDBManager):
    from app.api.deps import get_db

    app.dependency_overrides[get_db] = lambda: test_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Unit tests: Утилиты и безопасность
# ---------------------------------------------------------------------------


def test_sanitize_value_formula_injection() -> None:
    # Опасные строковые формулы экранируются ведущим апострофом
    assert _sanitize_value("=1+1") == "'=1+1"
    assert _sanitize_value("@malicious") == "'@malicious"
    assert _sanitize_value("+cmd|' /C calc'!A0") == "'+cmd|' /C calc'!A0"
    assert _sanitize_value("-something") == "'-something"

    # Обычные числа (включая отрицательные) остаются безопасными числами
    assert _sanitize_value("-42.5") == -42.5
    assert _sanitize_value("+10") == 10
    assert _sanitize_value(123) == 123
    assert _sanitize_value(45.67) == 45.67
    assert _sanitize_value(True) is True
    assert _sanitize_value(None) == ""

    # Списки и словари сериализуются в JSON
    assert _sanitize_value({"a": 1}) == '{"a": 1}'
    assert _sanitize_value([1, 2]) == "[1, 2]"


def test_safe_sheet_title_cleaning_and_uniqueness() -> None:
    used: set[str] = set()

    # Удаление запрещенных символов \ / ? * [ ] :
    t1 = _safe_sheet_title("Продажи [2024] / Q1: факт?", used)
    assert "[" not in t1 and "]" not in t1 and "/" not in t1 and "?" not in t1
    assert len(t1) <= 31

    # Уникальность одинаковых имен
    t2 = _safe_sheet_title("Выручка", used)
    assert t2 == "Выручка"
    t3 = _safe_sheet_title("Выручка", used)
    assert t3 == "Выручка_2"
    t4 = _safe_sheet_title("Выручка", used)
    assert t4 == "Выручка_3"

    # Пустая строка -> Лист
    t_empty = _safe_sheet_title("   ", used)
    assert t_empty.startswith("Лист")


def test_format_kpi_number() -> None:
    assert _format_kpi_number(1_500_000_000) == "1.5 млрд"
    assert _format_kpi_number(25_400_000) == "25.4 млн"
    assert _format_kpi_number(50_000) == "50 000"
    assert _format_kpi_number(1234.56) == "1 234.56"
    assert _format_kpi_number("Текст") == "Текст"
    assert _format_kpi_number(None) == "—"


def test_detect_column_roles_and_chart_types() -> None:
    records = [
        {"Категория": "А", "Выручка": 100, "Заказы": 10},
        {"Категория": "Б", "Выручка": 200, "Заказы": 20},
    ]
    cat_col, num_cols = _detect_column_roles(records, ["Категория", "Выручка", "Заказы"])
    assert cat_col == 1
    assert num_cols == [2, 3]

    # Исключение pure ID
    records_with_id = [
        {"id": 1, "Категория": "А", "Выручка": 100},
        {"id": 2, "Категория": "Б", "Выручка": 200},
    ]
    cat_col, num_cols = _detect_column_roles(records_with_id, ["id", "Категория", "Выручка"])
    assert cat_col == 2  # 'Категория' выбрана как X
    assert num_cols == [3]  # 'id' исключен, осталась 'Выручка'

    # Определение типа диаграммы
    assert _detect_chart_type({"usermeta": {"chart_type": "line"}}, "x") == "line"
    assert _detect_chart_type({"usermeta": {"chart_type": "pie"}}, "x") == "pie"
    assert _detect_chart_type({"usermeta": {"chart_type": "area"}}, "x") == "area"
    assert _detect_chart_type({"mark": "bar"}, "x") == "bar"
    assert _detect_chart_type(None, "Дата заказа") == "line"
    assert _detect_chart_type(None, "Город") == "bar"


# ---------------------------------------------------------------------------
# Unit tests: Экспорт отдельной таблицы (исследования)
# ---------------------------------------------------------------------------


def test_export_single_table_basic(tmp_path: Path, exporter: ExcelExporter) -> None:
    out_file = str(tmp_path / "single_basic.xlsx")
    records = [
        {"Город": "Москва", "Выручка": 1500000.5, "Дата": date(2024, 1, 15)},
        {"Город": "СПб", "Выручка": 800000.0, "Дата": date(2024, 2, 20)},
    ]
    path = exporter.export(records, out_file, title="Продажи по городам")
    assert Path(path).exists()

    wb = openpyxl.load_workbook(path)
    ws = wb.active
    assert ws.title == "Продажи по городам"

    # Проверка шапки таблицы
    header_cells = [ws.cell(row=1, column=c) for c in range(1, 4)]
    assert [c.value for c in header_cells] == ["Город", "Выручка", "Дата"]
    assert header_cells[0].fill.start_color.rgb.endswith(SBER_GREEN)
    assert (
        header_cells[0].font.color.rgb == "00FFFFFF" or header_cells[0].font.color.rgb == "FFFFFF"
    )

    # Проверка автофильтра и фиксации шапки
    assert ws.auto_filter.ref == "A1:C3"
    assert ws.freeze_panes == "A2"

    # Проверка наличия диаграммы справа от таблицы (столбец E = 5)
    assert len(ws._charts) == 1
    chart = ws._charts[0]
    assert isinstance(chart, BarChart)
    assert chart.title is not None


def test_export_single_table_with_metadata_card(tmp_path: Path, exporter: ExcelExporter) -> None:
    out_file = str(tmp_path / "single_meta.xlsx")
    records = [{"Товар": "Ноутбук", "Количество": 5}]

    path = exporter.export(
        records,
        out_file,
        title="Остатки",
        question="Сколько ноутбуков на складе?",
        headline="Отчет по остаткам техники",
        details=["Склад заполнен на 40%", "Требуется пополнение"],
    )

    wb = openpyxl.load_workbook(path)
    ws = wb.active

    # В верхней части находится карточка отчёта
    assert ws["A1"].value == "Отчет по остаткам техники"
    assert "Сколько ноутбуков" in ws["A2"].value
    assert "Склад заполнен" in ws["A3"].value
    assert "Требуется пополнение" in ws["A4"].value
    assert "Сформировано:" in ws["A5"].value

    # Таблица начинается ниже (строка 7)
    assert ws["A7"].value == "Товар"
    assert ws["B7"].value == "Количество"
    assert ws.freeze_panes == "A8"


def test_export_single_table_empty(tmp_path: Path, exporter: ExcelExporter) -> None:
    out_file = str(tmp_path / "single_empty.xlsx")
    path = exporter.export([], out_file, title="Пустой отчет")
    assert Path(path).exists()

    wb = openpyxl.load_workbook(path)
    ws = wb.active
    assert ws["A1"].value == "Нет данных для отображения"
    assert len(ws._charts) == 0


def test_export_single_table_without_numeric_columns(
    tmp_path: Path, exporter: ExcelExporter
) -> None:
    out_file = str(tmp_path / "single_no_numbers.xlsx")
    records = [
        {"ФИО": "Иванов И.И.", "Должность": "Аналитик", "Отдел": "Риски"},
        {"ФИО": "Петров П.П.", "Должность": "Разработчик", "Отдел": "ИТ"},
    ]
    path = exporter.export(records, out_file)
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    assert ws["A1"].value == "ФИО"
    # График не должен строиться для чисто текстовых таблиц
    assert len(ws._charts) == 0


def test_export_single_table_line_chart_dates(tmp_path: Path, exporter: ExcelExporter) -> None:
    out_file = str(tmp_path / "single_line.xlsx")
    records = [
        {"Дата": "2024-01-01", "Значение": 10},
        {"Дата": "2024-01-02", "Значение": 20},
    ]
    path = exporter.export(records, out_file, spec={"usermeta": {"chart_type": "line"}})
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    assert len(ws._charts) == 1
    assert isinstance(ws._charts[0], LineChart)


def test_export_single_table_pie_chart(tmp_path: Path, exporter: ExcelExporter) -> None:
    out_file = str(tmp_path / "single_pie.xlsx")
    records = [
        {"Сегмент": "B2B", "Доля": 60},
        {"Сегмент": "B2C", "Доля": 40},
    ]
    path = exporter.export(records, out_file, spec={"mark": "arc"})
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    assert len(ws._charts) == 1
    assert isinstance(ws._charts[0], PieChart)


# ---------------------------------------------------------------------------
# Unit tests: Экспорт дашборда (Dashboard Export)
# ---------------------------------------------------------------------------


def test_export_dashboard_full_structure(tmp_path: Path, exporter: ExcelExporter) -> None:
    out_file = str(tmp_path / "dashboard_full.xlsx")

    # 3 плитки: 1 KPI, 1 со столбчатой диаграммой, 1 с линейным графиком
    kpi_tile = DashboardTile(
        title="Общая выручка",
        is_kpi=True,
        snapshot_data=[{"total": 12500000}],
    )
    bar_tile = DashboardTile(
        title="Продажи по регионам",
        is_kpi=False,
        spec={"usermeta": {"chart_type": "bar"}},
        snapshot_data=[
            {"Регион": "Центр", "Продажи": 5000000},
            {"Регион": "Северо-Запад", "Продажи": 3000000},
        ],
        insight="Центральный регион лидирует по продажам",
    )
    line_tile = DashboardTile(
        title="Динамика по месяцам",
        is_kpi=False,
        spec={"usermeta": {"chart_type": "line"}},
        snapshot_data=[
            {"Месяц": "Январь", "Выручка": 1000000},
            {"Месяц": "Февраль", "Выручка": 1200000},
        ],
        insight="Стабильный рост выручки",
    )

    dashboard = Dashboard(
        id="dash-123",
        session_id="sess-abc",
        title="Квартальный дашборд продаж",
        tiles=[kpi_tile, bar_tile, line_tile],
    )

    path = exporter.export_dashboard(dashboard, out_file)
    assert Path(path).exists()

    wb = openpyxl.load_workbook(path)
    # Ровно 4 листа: Сводка + 3 плитки (включая KPI)
    assert wb.sheetnames == [
        "Сводка",
        "Общая выручка",
        "Продажи по регионам",
        "Динамика по месяцам",
    ]

    # Проверка листа «Сводка»
    summary_ws = wb["Сводка"]
    assert summary_ws["A1"].value == "Квартальный дашборд продаж"
    assert "sess-abc" in summary_ws["A2"].value

    # Проверка KPI карточки на сводке
    kpi_val = summary_ws["A5"].value
    assert "12.5 млн" in str(kpi_val)
    kpi_label = summary_ws["A7"].value
    assert kpi_label == "Общая выручка"

    # Проверка реестра состава дашборда на сводке
    assert "Состав дашборда" in [summary_ws.cell(row=r, column=1).value for r in range(1, 20)]

    # Проверка листа с графиком (Продажи по регионам)
    bar_ws = wb["Продажи по регионам"]
    assert bar_ws["A1"].value == "Продажи по регионам"
    assert bar_ws["A2"].value == "← Вернуться в Сводку"
    assert "Инсайт: Центральный регион" in bar_ws["A3"].value
    assert bar_ws["A5"].value == "Регион"
    assert bar_ws["B5"].value == "Продажи"
    assert len(bar_ws._charts) == 1
    assert isinstance(bar_ws._charts[0], BarChart)

    # Проверка листа с динамикой (Динамика по месяцам)
    line_ws = wb["Динамика по месяцам"]
    assert len(line_ws._charts) == 1
    assert isinstance(line_ws._charts[0], LineChart)


def test_export_dashboard_fallback_to_duckdb(
    tmp_path: Path, exporter: ExcelExporter, test_db: DuckDBManager
) -> None:
    # Плитка без сохраненного snapshot_data, но с result_table в DuckDB
    test_db.query("CREATE TABLE temp_result_v9 AS SELECT 'Планшет' AS товар, 35000 AS цена")

    tile = DashboardTile(
        title="Спецзаказ",
        is_kpi=False,
        result_table="temp_result_v9",
        snapshot_data=None,
    )
    dashboard = Dashboard(
        id="dash-db-fallback",
        session_id="sess-1",
        title="Дашборд с DuckDB",
        tiles=[tile],
    )

    out_file = str(tmp_path / "dash_duckdb.xlsx")
    path = exporter.export_dashboard(dashboard, out_file, db=test_db)
    wb = openpyxl.load_workbook(path)
    assert "Спецзаказ" in wb.sheetnames
    ws = wb["Спецзаказ"]
    assert ws["A4"].value == "товар"
    assert ws["B4"].value == "цена"
    assert ws["A5"].value == "Планшет"
    assert ws["B5"].value == 35000


def test_export_dashboard_without_data(tmp_path: Path, exporter: ExcelExporter) -> None:
    tile = DashboardTile(
        title="Пустая плитка",
        is_kpi=False,
        snapshot_data=None,
    )
    dashboard = Dashboard(
        id="dash-empty",
        session_id="sess-1",
        title="Пустой дашборд",
        tiles=[tile],
    )
    out_file = str(tmp_path / "dash_empty.xlsx")
    path = exporter.export_dashboard(dashboard, out_file)
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Сводка"]


# ---------------------------------------------------------------------------
# API tests: Эндпоинты экспорта
# ---------------------------------------------------------------------------


def test_api_export_artifact_single_table(client: TestClient, test_db: DuckDBManager) -> None:
    from app.core.memory.session_memory import SessionMemory

    # Подготовим таблицу в DuckDB и сессию в SessionMemory
    test_db.query("CREATE TABLE temp_result_v1 AS SELECT 'Москва' AS city, 100 AS val")
    memory = SessionMemory("test-session", test_db)
    memory.set_table("raw")
    memory.start_turn("Вопрос")
    memory.finish_turn("ok", result_table="temp_result_v1")

    from app.api.deps import get_db

    app.dependency_overrides[get_db] = lambda: test_db
    try:
        response = client.post(
            "/api/artifacts/export",
            json={
                "session_id": "test-session",
                "format": "xlsx",
                "result_ref": "temp_result_v1",
                "question": "Какой результат?",
                "headline": "Отчет по городам",
            },
        )
        assert response.status_code == 200
        assert (
            response.headers["content-type"]
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert len(response.content) > 0
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_api_export_artifact_dashboard(client: TestClient, test_db: DuckDBManager) -> None:
    store = DashboardStore()
    dash = Dashboard(
        session_id="sess-dash",
        title="Дашборд через API",
        tiles=[
            DashboardTile(
                title="Метрика",
                is_kpi=True,
                snapshot_data={"value": "99.9%", "label": "Доступность"},
            )
        ],
    )
    store.create(dash)

    from app.api.deps import get_db

    app.dependency_overrides[get_db] = lambda: test_db
    try:
        response = client.post(
            "/api/artifacts/export",
            json={
                "session_id": "sess-dash",
                "format": "xlsx",
                "dashboard_id": dash.id,
            },
        )
        assert response.status_code == 200
        assert (
            response.headers["content-type"]
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_api_export_errors(client: TestClient) -> None:
    store = DashboardStore()
    dash = Dashboard(
        session_id="s1",
        title="Тест ошибок",
        tiles=[],
    )
    store.create(dash)

    # Экспорт дашборда в PDF теперь поддержан (200 OK)
    res_pdf = client.post(
        "/api/artifacts/export",
        json={"session_id": "s1", "format": "pdf", "dashboard_id": dash.id},
    )
    assert res_pdf.status_code == 200
    assert res_pdf.headers["content-type"] == "application/pdf"

    # 400 без session_id
    res_no_sess = client.post(
        "/api/artifacts/export",
        json={"session_id": "", "format": "xlsx"},
    )
    assert res_no_sess.status_code == 400

    # 400 без result_ref и без dashboard_id
    res_empty = client.post(
        "/api/artifacts/export",
        json={"session_id": "s1", "format": "xlsx"},
    )
    assert res_empty.status_code == 400

    # 400 с некорректным result_ref
    res_bad_ref = client.post(
        "/api/artifacts/export",
        json={"session_id": "s1", "format": "xlsx", "result_ref": "drop_tables;"},
    )
    assert res_bad_ref.status_code == 400

    # 404 на несуществующий дашборд
    res_dash_404 = client.post(
        "/api/artifacts/export",
        json={"session_id": "s1", "format": "xlsx", "dashboard_id": "nonexistent-id"},
    )
    assert res_dash_404.status_code == 404


def test_api_dashboard_export_get_endpoint(client: TestClient, test_db: DuckDBManager) -> None:
    store = DashboardStore()
    dash = Dashboard(
        session_id="sess-get",
        title="Тест GET экспорта",
        tiles=[
            DashboardTile(
                title="Клиенты",
                is_kpi=False,
                snapshot_data=[{"Клиент": "ООО Ромашка", "Сумма": 150000}],
            )
        ],
    )
    store.create(dash)

    from app.api.deps import get_db

    app.dependency_overrides[get_db] = lambda: test_db
    try:
        # Успешный GET экспорт
        res = client.get(f"/api/dashboards/{dash.id}/export?format=xlsx")
        assert res.status_code == 200
        assert (
            res.headers["content-type"]
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        # 404 на несуществующий дашборд
        res_404 = client.get("/api/dashboards/no-such-dash/export?format=xlsx")
        assert res_404.status_code == 404

        # Успешный экспорт в PDF
        res_pdf = client.get(f"/api/dashboards/{dash.id}/export?format=pdf")
        assert res_pdf.status_code == 200
        assert res_pdf.headers["content-type"] == "application/pdf"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_api_export_duckdb_error(client: TestClient, test_db: DuckDBManager) -> None:
    from app.core.memory.session_memory import SessionMemory

    memory = SessionMemory("s1", test_db)
    memory.set_table("raw")
    memory.start_turn("Вопрос")
    memory.finish_turn("ok", result_table="temp_result_v999")

    from app.api.deps import get_db

    app.dependency_overrides[get_db] = lambda: test_db
    try:
        # 404 при ошибке DuckDB (таблица не найдена)
        res = client.post(
            "/api/artifacts/export",
            json={"session_id": "s1", "format": "xlsx", "result_ref": "temp_result_v999"},
        )
        assert res.status_code == 404
        assert "не удалось прочитать" in res.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_extract_kpi_value_and_format_branches() -> None:
    from app.core.export.excel import _extract_kpi_value

    # Число < 10000 целое
    assert _format_kpi_number(500) == "500"

    # Словарь без ключа 'value'
    tile_dict_no_val = DashboardTile(
        title="Метрика",
        is_kpi=True,
        snapshot_data={"count": 42},
    )
    val, _lbl = _extract_kpi_value(tile_dict_no_val)
    assert val == "42"

    # Извлечение из spec.data.values
    tile_spec = DashboardTile(
        title="Спека",
        is_kpi=True,
        spec={"data": {"values": [{"res": 777}]}},
    )
    val2, _ = _extract_kpi_value(tile_spec)
    assert val2 == "777"

    # Пустая плитка
    tile_empty = DashboardTile(title="Пусто", is_kpi=True)
    val3, _ = _extract_kpi_value(tile_empty)
    assert val3 == "—"


def test_detect_column_roles_and_mark_types_extra() -> None:
    from app.core.export.excel import (
        _auto_adjust_column_widths,
        _detect_chart_type,
        _detect_column_roles,
    )

    # Колонка полностью None
    records = [{"a": None, "b": 10}, {"a": None, "b": 20}]
    _cat, nums = _detect_column_roles(records, ["a", "b"])
    assert nums == [2]

    # Марки с type в виде словаря
    assert _detect_chart_type({"mark": {"type": "line"}}, "x") == "line"
    assert _detect_chart_type({"mark": {"type": "area"}}, "x") == "area"

    # _auto_adjust_column_widths без max_col
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Тест"
    _auto_adjust_column_widths(ws)
    assert ws.column_dimensions["A"].width >= 11


def test_export_single_table_area_chart(tmp_path: Path, exporter: ExcelExporter) -> None:
    from openpyxl.chart import AreaChart

    out_file = str(tmp_path / "single_area.xlsx")
    records = [{"x": "1", "y": 10}, {"x": "2", "y": 20}]
    path = exporter.export(records, out_file, spec={"usermeta": {"chart_type": "area"}})
    wb = openpyxl.load_workbook(path)
    assert isinstance(wb.active._charts[0], AreaChart)


def test_export_dashboard_with_broken_db_and_types(
    tmp_path: Path, exporter: ExcelExporter, test_db: DuckDBManager
) -> None:
    # 1. Плитка со сломанной таблицей в DuckDB
    tile_broken = DashboardTile(
        title="Сломанная",
        is_kpi=False,
        result_table="nonexistent_duckdb_tbl",
        snapshot_data=None,
    )
    # 2. Плитка с float и date типами
    tile_types = DashboardTile(
        title="Типы данных",
        is_kpi=False,
        snapshot_data=[
            {
                "FloatVal": 12.34,
                "DateVal": date(2024, 5, 1),
                "DateTimeVal": datetime(2024, 5, 1, 10, 0),
            }
        ],
    )
    dash = Dashboard(
        id="dash-broken-test",
        session_id="s1",
        title="Тест типов и ошибок",
        tiles=[tile_broken, tile_types],
    )
    out_file = str(tmp_path / "dash_types.xlsx")
    path = exporter.export_dashboard(dash, out_file, db=test_db)
    wb = openpyxl.load_workbook(path)
    assert "Типы данных" in wb.sheetnames


def test_branch_coverage_cases(tmp_path: Path, exporter: ExcelExporter) -> None:
    from app.core.export.excel import (
        _auto_adjust_column_widths,
        _detect_chart_type,
        _detect_column_roles,
        _extract_kpi_value,
    )

    # 1. KPI row is empty dict
    val, _ = _extract_kpi_value(DashboardTile(title="EmptyRow", is_kpi=True, snapshot_data=[{}]))
    assert val == "—"

    # 2. non_id is empty when all numeric columns are IDs
    _, num_cols = _detect_column_roles([{"name": "A", "id": 1, "код": 2}], ["name", "id", "код"])
    assert num_cols == [2, 3]

    # multiline cell and None cell to cover loop continuation and None branch
    wb_test = openpyxl.Workbook()
    ws_test = wb_test.active
    ws_test.cell(row=1, column=1, value="Строка 1\nСтрока 2")
    ws_test.cell(row=2, column=1, value=None)
    _auto_adjust_column_widths(ws_test)
    assert ws_test.column_dimensions["A"].width >= 11

    # 3. Chart type unknown explicit and unknown mark
    assert _detect_chart_type({"usermeta": {"chart_type": "custom"}}, "x") == "bar"
    assert _detect_chart_type({"mark": 123}, "x") == "bar"
    assert _detect_chart_type({"mark": "unknown_mark"}, "x") == "bar"

    # 4. export with question only (headline=None, details=None)
    p_q = exporter.export([{"a": 1}], str(tmp_path / "q_only.xlsx"), question="Только вопрос?")
    assert Path(p_q).exists()

    # 5. export with details only (headline=None, question=None)
    p_d = exporter.export([{"a": 1}], str(tmp_path / "d_only.xlsx"), details=["Только деталь"])
    assert Path(p_d).exists()


def test_api_export_xlsx_with_spec_data_values_and_branches(
    client: TestClient, test_db: DuckDBManager, tmp_path: Path, exporter: ExcelExporter
) -> None:
    """Проверка экспорта в Excel с данными из spec (без result_ref) и всех веток извлечения."""
    import io

    from app.core.memory.session_memory import SessionMemory

    memory = SessionMemory("s_spec_test", test_db)
    memory.set_table("raw")

    # 1. Успешный экспорт без result_ref, со spec и headline
    res = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "s_spec_test",
            "format": "xlsx",
            "headline": "Структура таблицы",
            "details": ["10 строк", "3 колонки"],
            "spec": {
                "mark": "bar",
                "data": {
                    "values": [
                        {"Колонка": "id", "Тип": "INTEGER", "Пропуски": 0},
                        {"Колонка": "name", "Тип": "VARCHAR", "Пропуски": 5},
                    ]
                },
            },
        },
    )
    assert res.status_code == 200
    assert (
        res.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    wb = openpyxl.load_workbook(io.BytesIO(res.content))
    ws = wb.active
    # Проверяем наличие заголовка инсайта и строк таблицы
    text_values = [ws.cell(row=r, column=1).value for r in range(1, 10)]
    assert any("Структура таблицы" in str(v) for v in text_values if v)
    assert any("id" in str(v) for v in text_values if v)

    # 2. spec без data
    res_no_data = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "s_spec_test",
            "format": "xlsx",
            "headline": "График без данных",
            "spec": {"mark": "bar"},
        },
    )
    assert res_no_data.status_code == 200

    # 3. spec с data не dict или values не list
    res_bad_data = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "s_spec_test",
            "format": "xlsx",
            "headline": "Внешний URL",
            "spec": {"data": {"url": "foo.csv"}},
        },
    )
    assert res_bad_data.status_code == 200

    # 3b. spec.data как список словарей напрямую
    res_data_list = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "s_spec_test",
            "format": "xlsx",
            "headline": "Спека со списком",
            "spec": {"data": [{"x": 10}]},
        },
    )
    assert res_data_list.status_code == 200

    # 3c. spec.values как список словарей
    res_values_list = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "s_spec_test",
            "format": "xlsx",
            "headline": "Спека с values",
            "spec": {"values": [{"y": 20}]},
        },
    )
    assert res_values_list.status_code == 200

    # 4. export_dashboard с плиткой, данные которой находятся в spec.data.values
    dash = Dashboard(
        session_id="s_dash_spec",
        title="Дашборд со спекой",
        tiles=[
            DashboardTile(
                title="Плитка со спекой",
                is_kpi=False,
                spec={
                    "mark": "bar",
                    "data": {"values": [{"Метрика": "A", "Значение": 100}]},
                },
            )
        ],
    )
    out_path = str(tmp_path / "dash_spec.xlsx")
    exporter.export_dashboard(dash, out_path, db=test_db)
    wb_dash = openpyxl.load_workbook(out_path)
    assert "Плитка со спекой" in wb_dash.sheetnames


def test_detect_column_roles_vega_lite_encoding() -> None:
    # 1. Круговая диаграмма (theta & color)
    pie_spec = {
        "encoding": {
            "theta": {"field": "revenue", "type": "quantitative"},
            "color": {"field": "category", "type": "nominal"},
        }
    }
    cols = ["category", "revenue", "id"]
    cat_idx, num_idxs = _detect_column_roles([], cols, spec=pie_spec)
    assert cat_idx == 1  # category
    assert num_idxs == [2]  # revenue

    # 2. Вертикальный бар (x nominal, y quantitative)
    vbar_spec = {
        "encoding": {
            "x": {"field": "category", "type": "nominal"},
            "y": {"field": "revenue", "type": "quantitative"},
        }
    }
    cat_idx, num_idxs = _detect_column_roles([], cols, spec=vbar_spec)
    assert cat_idx == 1  # category
    assert num_idxs == [2]  # revenue

    # 3. Горизонтальный бар (x quantitative, y nominal)
    hbar_spec = {
        "encoding": {
            "x": {"field": "revenue", "type": "quantitative"},
            "y": {"field": "category", "type": "nominal"},
        }
    }
    cat_idx, num_idxs = _detect_column_roles([], cols, spec=hbar_spec)
    assert cat_idx == 1  # category (y)
    assert num_idxs == [2]  # revenue (x)

    # 4. Пустая выборка или колонки без числовых/текстовых данных
    cat_empty, num_empty = _detect_column_roles([], [])
    assert cat_empty == 1
    assert num_empty == []

    cat_none, num_none = _detect_column_roles([{"a": None}], ["a"])
    assert cat_none == 1
    assert num_none == []


def test_detect_chart_type_scatter_and_points() -> None:
    # explicit_type scatter и point -> line
    assert _detect_chart_type({"usermeta": {"chart_type": "scatter"}}, "x") == "line"
    assert _detect_chart_type({"usermeta": {"chart_type": "point"}}, "x") == "line"

    # mark point, circle, square, tick -> line
    assert _detect_chart_type({"mark": "point"}, "x") == "line"
    assert _detect_chart_type({"mark": {"type": "circle"}}, "x") == "line"
    assert _detect_chart_type({"mark": "square"}, "x") == "line"
    assert _detect_chart_type({"mark": "tick"}, "x") == "line"


def test_build_chart_failure_handled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    from app.core.export.excel import _build_chart

    wb = openpyxl.Workbook()
    ws = wb.active

    # 1. _build_chart исключение -> возвращает None без падения
    monkeypatch.setattr(BarChart, "__init__", MagicMock(side_effect=RuntimeError("Chart err")))
    res = _build_chart(ws, cat_col=1, num_cols=[2], header_row=1, last_row=10, chart_type="bar")
    assert res is None

    # 2. ws.add_chart исключение при экспорте
    exporter = ExcelExporter()
    monkeypatch.undo()

    def broken_add_chart(self, *args, **kwargs):
        raise RuntimeError("Add chart error")

    monkeypatch.setattr(ws.__class__, "add_chart", broken_add_chart)

    # Экспорт одиночного отчета с падающим add_chart
    out_p = str(tmp_path / "chart_err.xlsx")
    exporter.export(
        [{"cat": "A", "val": 10}, {"cat": "B", "val": 20}],
        out_p,
        title="Тест сбоя диаграммы",
    )
    assert Path(out_p).exists()

    # Экспорт дашборда с падающим add_chart
    dash = Dashboard(
        session_id="s1",
        title="Тест сбоя диаграммы дашборда",
        tiles=[
            DashboardTile(
                title="Плитка",
                is_kpi=False,
                snapshot_data=[{"cat": "A", "val": 10}, {"cat": "B", "val": 20}],
            )
        ],
    )
    out_dash = str(tmp_path / "dash_chart_err.xlsx")
    exporter.export_dashboard(dash, out_dash)
    assert Path(out_dash).exists()


def test_long_text_wrapping_and_row_limit_truncation(
    tmp_path: Path, exporter: ExcelExporter
) -> None:
    # Генерация выборки > 5000 строк
    large_records = [{"name": f"Item {i}", "desc": "A" * 50, "val": i} for i in range(5005)]

    # 1. Одиночный экспорт
    p_large = str(tmp_path / "large.xlsx")
    exporter.export(
        large_records,
        p_large,
        headline="+100% Результаты за год",
        question="=SUM(A1:B1)",
    )
    wb = openpyxl.load_workbook(p_large)
    ws = wb.active

    # Защита формул в headline и question
    assert ws.cell(row=1, column=1).value == "'+100% Результаты за год"
    assert ws.cell(row=2, column=1).value == "Вопрос: =SUM(A1:B1)"

    # Проверка строки метаданных с лимитом выборки
    meta_val = str(ws.cell(row=3, column=1).value)
    assert "Показано строк: 5,000 из 5,005 (лимит выборки)" in meta_val

    # Проверка wrap_text для длинного текста (> 40 символов)
    # Таблица начинается с row=4 (шапка) и row=5 (первая запись)
    c_desc = ws.cell(row=5, column=2)
    assert c_desc.alignment.wrap_text is True

    # 2. Экспорт дашборда с усечением
    dash = Dashboard(
        session_id="s_large",
        title="Дашборд с большой таблицей",
        tiles=[
            DashboardTile(
                title="Большая плитка",
                is_kpi=False,
                snapshot_data=large_records,
                insight="Инсайт по большой таблице",
            )
        ],
    )
    p_dash_large = str(tmp_path / "dash_large.xlsx")
    exporter.export_dashboard(dash, p_dash_large)
    wb_dash = openpyxl.load_workbook(p_dash_large)
    tile_ws = wb_dash["Большая плитка"]
    tile_texts = [str(tile_ws.cell(row=r, column=1).value) for r in range(1, 10)]
    assert any("Показано первых 5,000 строк из 5,005" in t for t in tile_texts)


def test_export_dashboard_profile_memory_source_label(
    tmp_path: Path, exporter: ExcelExporter, test_db: DuckDBManager
) -> None:
    test_db.execute("CREATE TABLE products_real (id INT, price INT)")
    test_db.execute("INSERT INTO products_real VALUES (1, 100), (2, 200)")
    test_db.execute("CREATE SCHEMA IF NOT EXISTS _meta")
    test_db.execute(
        """CREATE TABLE IF NOT EXISTS _meta.table_passport (
            table_name VARCHAR PRIMARY KEY,
            row_count BIGINT,
            column_count INT,
            profiled_at TIMESTAMPTZ,
            display_name VARCHAR,
            name_source VARCHAR
        )"""
    )
    test_db.execute(
        "INSERT INTO _meta.table_passport (table_name, row_count, column_count, profiled_at, display_name) "
        "VALUES ('products_real', 2, 2, now(), 'Каталог товаров')"
    )

    dash = Dashboard(
        session_id="s_prof",
        title="Дашборд с источником из ProfileMemory",
        tiles=[
            DashboardTile(
                title="Товары",
                is_kpi=False,
                result_table="products_real",
            )
        ],
    )
    out_p = str(tmp_path / "dash_prof.xlsx")
    exporter.export_dashboard(dash, out_p, db=test_db)
    wb = openpyxl.load_workbook(out_p)
    ws_summary = wb["Сводка"]
    meta_text = str(ws_summary.cell(row=2, column=1).value)
    assert "База данных: Каталог товаров" in meta_text
    assert "Всего плиток: 1" in meta_text

    # Также проверяем передачу direct database_name
    out_direct = str(tmp_path / "dash_direct.xlsx")
    exporter.export_dashboard(dash, out_direct, db=test_db, database_name="Прямое имя БД")
    wb_dir = openpyxl.load_workbook(out_direct)
    meta_dir = str(wb_dir["Сводка"].cell(row=2, column=1).value)
    assert "База данных: Прямое имя БД" in meta_dir

    # Проверка сбоя при чтении ProfileMemory
    from unittest.mock import MagicMock

    broken_db = MagicMock()
    broken_db.execute.side_effect = RuntimeError("Broken DB profile")
    out_broken = str(tmp_path / "dash_broken.xlsx")
    exporter.export_dashboard(dash, out_broken, db=broken_db)
    assert Path(out_broken).exists()


def test_api_export_error_handling_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, test_db: DuckDBManager
) -> None:
    # 1. Невалидный result_ref -> 400
    res_bad_ref = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "s1",
            "format": "xlsx",
            "result_ref": "invalid_ref_format",
        },
    )
    assert res_bad_ref.status_code == 400
    assert "result_ref должен быть ссылкой на temp_result_vN" in res_bad_ref.json()["detail"]

    # 2. Исключение в ExcelExporter.export -> 500
    from app.core.memory.session_memory import SessionMemory

    memory = SessionMemory("s_err", test_db)
    memory.set_table("t1")
    test_db.execute("CREATE TABLE temp_result_v1 AS SELECT 1 as a")
    memory.start_turn("тестовый вопрос")
    memory.finish_turn("ok", result_table="temp_result_v1")

    def broken_export(*args, **kwargs):
        raise RuntimeError("Ошибка при записи файла Excel")

    monkeypatch.setattr(ExcelExporter, "export", broken_export)
    res_exp_err = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "s_err",
            "format": "xlsx",
            "result_ref": "temp_result_v1",
        },
    )
    assert res_exp_err.status_code == 500
    assert "Не удалось сгенерировать Excel" in res_exp_err.json()["detail"]

    # 3. Исключение в ExcelExporter.export_dashboard в /api/artifacts/export -> 500
    dash_id = DashboardStore().create(Dashboard(session_id="s_err", title="Сбойный дашборд"))

    def broken_dash_export(*args, **kwargs):
        raise RuntimeError("Ошибка генерации дашборда")

    monkeypatch.setattr(ExcelExporter, "export_dashboard", broken_dash_export)
    res_dash_err = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "s_err",
            "dashboard_id": dash_id,
            "format": "xlsx",
        },
    )
    assert res_dash_err.status_code == 500
    assert "Не удалось сгенерировать Excel" in res_dash_err.json()["detail"]

    # 4. Исключение в GET /api/dashboards/{id}/export -> 500
    res_get_err = client.get(f"/api/dashboards/{dash_id}/export?format=xlsx")
    assert res_get_err.status_code == 500
    assert "Не удалось сгенерировать Excel" in res_get_err.json()["detail"]


def test_timezone_aware_and_table_spec_suppression(tmp_path: Path, exporter: ExcelExporter) -> None:
    from datetime import UTC

    # 1. Timezone aware datetime и формулы в данных
    aware_dt = datetime(2026, 10, 30, 15, 30, tzinfo=UTC)
    records = [
        {"дата": aware_dt, "формула": "=2+2", "плюс": "+100%", "значение": 42, "активен": True},
        {
            "дата": aware_dt,
            "формула": "-отрицательное",
            "плюс": "обычно",
            "значение": 84,
            "активен": False,
        },
    ]

    p_out = str(tmp_path / "aware_dt.xlsx")
    exporter.export(
        records,
        p_out,
        title="Тест часовых поясов",
        spec={"usermeta": {"chart_type": "table"}},  # Спецификация таблицы -> график подавлен!
    )
    wb = openpyxl.load_workbook(p_out)
    ws = wb.active
    # График подавлен для table
    assert len(ws._charts) == 0

    # Проверка нейтрализации формул
    assert ws.cell(row=2, column=2).value == "'=2+2"
    assert ws.cell(row=2, column=3).value == "'+100%"
    assert ws.cell(row=3, column=2).value == "'-отрицательное"


def test_schema_drift_records_excel(tmp_path: Path, exporter: ExcelExporter) -> None:
    # Записи с разным набором ключей (schema drift)
    records = [
        {"id": 1, "name": "Первый"},
        {"id": 2, "name": "Второй", "extra_metric": 999},
        {"id": 3, "third_field": "Значение"},
    ]
    p_out = str(tmp_path / "schema_drift.xlsx")
    exporter.export(records, p_out, title="Смена схемы")
    wb = openpyxl.load_workbook(p_out)
    ws = wb.active

    # Заголовки должны содержать объединение всех колонок: id, name, extra_metric, third_field
    cols = [ws.cell(row=1, column=c).value for c in range(1, 5)]
    assert cols == ["id", "name", "extra_metric", "third_field"]
