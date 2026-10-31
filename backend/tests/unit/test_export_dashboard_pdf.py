import io
from pathlib import Path

import pypdf
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.dashboards.models import Dashboard, DashboardTile
from app.core.dashboards.store import DashboardStore
from app.core.data.duckdb_manager import DuckDBManager
from app.core.export.pdf import PdfExporter
from app.core.memory.session_memory import SessionMemory
from app.main import app

SAMPLE_SPEC = {
    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
    "description": "Тестовый график",
    "data": {
        "values": [
            {"category": "Электроника", "amount": 42500000},
            {"category": "Одежда", "amount": 21000000},
            {"category": "Продукты", "amount": 15000000},
        ]
    },
    "mark": "bar",
    "encoding": {
        "x": {"field": "category", "type": "nominal"},
        "y": {"field": "amount", "type": "quantitative"},
    },
    "width": "container",
    "height": "container",
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.config.settings.storage_dir", str(tmp_path))
    monkeypatch.setattr("app.config.settings.sqlite_path", str(tmp_path / "sessions.sqlite"))
    db = DuckDBManager(":memory:")
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()
    db.close()


def test_export_dashboard_pdf_full_structure(tmp_path: Path) -> None:
    """Проверка генерации PDF дашборда: KPI, графики, инсайты, таблицы."""
    exporter = PdfExporter()
    out_file = str(tmp_path / "dashboard.pdf")

    kpi_tile = DashboardTile(
        title="Общая выручка",
        is_kpi=True,
        snapshot_data=[{"total": 12500000}],
    )
    chart_tile = DashboardTile(
        title="Продажи по категориям",
        is_kpi=False,
        spec=SAMPLE_SPEC,
        insight="Электроника лидирует по объёмам выручки",
    )
    table_tile = DashboardTile(
        title="Таблица заказов",
        is_kpi=False,
        spec={"usermeta": {"chart_type": "table"}},
        snapshot_data=[
            {"ID": 101, "Клиент": "ООО Ромашка", "Сумма": 150000},
            {"ID": 102, "Клиент": "ИП Иванов", "Сумма": 85000},
        ],
    )

    dashboard = Dashboard(
        id="dash-123",
        session_id="sess-abc",
        title="Квартальный дашборд продаж",
        tiles=[kpi_tile, chart_tile, table_tile],
    )

    path = exporter.export_dashboard(
        dashboard,
        out_file,
        session_title="Анализ продаж за квартал",
        source_name="orders.csv",
    )
    assert Path(path).exists()
    assert Path(path).stat().st_size > 0

    reader = pypdf.PdfReader(path)
    assert len(reader.pages) >= 1
    full_text = "".join([page.extract_text() or "" for page in reader.pages])

    assert "Квартальный дашборд продаж" in full_text
    assert "Анализ продаж за квартал" in full_text
    assert "orders.csv" in full_text
    assert "Всего плиток: 3" in full_text
    assert "sess-abc" not in full_text
    assert "12.5 млн" in full_text
    assert "Продажи по категориям" in full_text
    assert "Электроника лидирует" in full_text
    assert "ООО Ромашка" in full_text


def test_export_dashboard_pdf_empty(tmp_path: Path) -> None:
    """Граничный случай: пустой дашборд без плиток."""
    exporter = PdfExporter()
    out_file = str(tmp_path / "empty_dash.pdf")

    dashboard = Dashboard(
        id="dash-empty",
        session_id="sess-empty",
        title="Пустой дашборд",
        tiles=[],
    )

    path = exporter.export_dashboard(dashboard, out_file)
    assert Path(path).exists()
    reader = pypdf.PdfReader(path)
    assert len(reader.pages) >= 1
    text = reader.pages[0].extract_text() or ""
    assert "Пустой дашборд" in text


def test_export_dashboard_pdf_kpi_only(tmp_path: Path) -> None:
    """Дашборд только с KPI плитками."""
    exporter = PdfExporter()
    out_file = str(tmp_path / "kpi_only.pdf")

    dashboard = Dashboard(
        id="dash-kpi",
        session_id="sess-kpi",
        title="KPI Дашборд",
        tiles=[
            DashboardTile(title="Выручка", is_kpi=True, snapshot_data={"value": 45000000}),
            DashboardTile(title="Прирост", is_kpi=True, snapshot_data={"value": "+14%"}),
        ],
    )

    path = exporter.export_dashboard(dashboard, out_file)
    assert Path(path).exists()
    reader = pypdf.PdfReader(path)
    assert len(reader.pages) >= 1
    text = reader.pages[0].extract_text() or ""
    assert "KPI Дашборд" in text
    assert "45.0 млн" in text or "45" in text


def test_export_dashboard_pdf_with_duckdb(tmp_path: Path) -> None:
    """Дашборд с вычиткой данных из DuckDB для плиток без snapshot_data."""
    exporter = PdfExporter()
    out_file = str(tmp_path / "duckdb_dash.pdf")

    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE temp_result_v55 AS SELECT 'Москва' AS city, 99000 AS val")

    dashboard = Dashboard(
        id="dash-duck",
        session_id="sess-duck",
        title="Дашборд с DuckDB",
        tiles=[
            DashboardTile(
                title="Города",
                is_kpi=False,
                result_table="temp_result_v55",
                spec={"mark": "bar", "encoding": {"x": {"field": "city"}, "y": {"field": "val"}}},
            )
        ],
    )

    path = exporter.export_dashboard(dashboard, out_file, db=db)
    assert Path(path).exists()
    reader = pypdf.PdfReader(path)
    assert len(reader.pages) >= 1
    text = reader.pages[0].extract_text() or ""
    assert "Дашборд с DuckDB" in text
    db.close()


def test_api_export_dashboard_get_pdf(client: TestClient, tmp_path: Path) -> None:
    """API-тест: GET /api/dashboards/{id}/export?format=pdf."""
    store = DashboardStore(str(tmp_path / "sessions.sqlite"))
    dash = Dashboard(
        id="d-test-api",
        session_id="s-test-api",
        title="API Тестовый Дашборд",
        tiles=[
            DashboardTile(
                title="Метрика",
                is_kpi=True,
                snapshot_data={"value": 1000},
            )
        ],
    )
    store.create(dash)

    resp = client.get("/api/dashboards/d-test-api/export?format=pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF")

    reader = pypdf.PdfReader(io.BytesIO(resp.content))
    assert len(reader.pages) >= 1
    assert "API Тестовый Дашборд" in (reader.pages[0].extract_text() or "")


def test_api_export_dashboard_post_artifacts_pdf(client: TestClient, tmp_path: Path) -> None:
    """API-тест: POST /api/artifacts/export с dashboard_id и format=pdf."""
    store = DashboardStore(str(tmp_path / "sessions.sqlite"))
    dash = Dashboard(
        id="d-post-api",
        session_id="s-post-api",
        title="Дашборд через Artifacts API",
        tiles=[
            DashboardTile(
                title="Клиенты",
                is_kpi=True,
                snapshot_data={"value": 500},
            )
        ],
    )
    store.create(dash)

    resp = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "s-post-api",
            "dashboard_id": "d-post-api",
            "format": "pdf",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")

    reader = pypdf.PdfReader(io.BytesIO(resp.content))
    assert len(reader.pages) >= 1
    assert "Дашборд через Artifacts API" in (reader.pages[0].extract_text() or "")


def test_export_dashboard_custom_title_overrides_stored_title(
    client: TestClient, tmp_path: Path
) -> None:
    """Проверка, что переданный заголовок (title) обновляет название дашборда и имя файла."""
    store = DashboardStore(str(tmp_path / "sessions.sqlite"))
    dash = Dashboard(
        id="d-custom-title",
        session_id="s-custom-title",
        title="Старое название",
        tiles=[DashboardTile(title="Метрика", is_kpi=True, snapshot_data={"value": 100})],
    )
    store.create(dash)

    resp = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "s-custom-title",
            "dashboard_id": "d-custom-title",
            "format": "pdf",
            "title": "Дашборд исследования (отредактированный)",
        },
    )
    assert resp.status_code == 200
    assert "attachment;" in resp.headers["content-disposition"]
    # Проверяем, что в Content-Disposition закодирован отредактированный заголовок
    assert (
        "%D0%94%D0%B0%D1%88%D0%B1%D0%BE%D1%80%D0%B4%20%D0%B8%D1%81%D1%81%D0%BB%D0%B5%D0%B4%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D1%8F"
        in resp.headers["content-disposition"]
    )
    reader = pypdf.PdfReader(io.BytesIO(resp.content))
    page_text = reader.pages[0].extract_text() or ""
    assert "Дашборд исследования (отредактированный)" in page_text


def test_export_single_chart_custom_title(client: TestClient, tmp_path: Path) -> None:
    """Проверка экспорта отдельного графика с кастомным названием из интерфейса."""
    db: DuckDBManager = client.app.dependency_overrides[get_db]()
    db.execute("CREATE TABLE temp_result_v99 AS SELECT 'Группа А' as grp, 42 as cnt")
    memory = SessionMemory("s-chart-title", db)
    memory.set_table("raw")
    memory.start_turn("Вопрос")
    memory.finish_turn("ok", result_table="temp_result_v99")

    resp = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "s-chart-title",
            "result_ref": "temp_result_v99",
            "format": "pdf",
            "title": "График распределения по возрасту",
            "spec": SAMPLE_SPEC,
        },
    )
    assert resp.status_code == 200
    assert "attachment;" in resp.headers["content-disposition"]
    assert (
        "%D0%93%D1%80%D0%B0%D1%84%D0%B8%D0%BA%20%D1%80%D0%B0%D1%81%D0%BF%D1%80%D0%B5%D0%B4%D0%B5%D0%BB%D0%B5%D0%BD%D0%B8%D1%8F"
        in resp.headers["content-disposition"]
    )
    reader = pypdf.PdfReader(io.BytesIO(resp.content))
    text = "".join(p.extract_text() or "" for p in reader.pages)
    assert "График распределения по возрасту" in text


def test_export_large_table_never_spills_to_page_3(tmp_path: Path) -> None:
    """Гарантия строгого соблюдения двухстраничного формата даже при 50 строках данных."""
    exporter = PdfExporter()
    pdf_path = str(tmp_path / "two_page_guarantee.pdf")
    records = [
        {
            "№": i + 1,
            "Категория товаров": f"Категория с достаточно длинным названием {i + 1}",
            "Выручка за период, ₽": 500000.75 + i * 1234.56,
            "Маржинальность": f"{15.5 + (i % 10) * 0.8:.2f}%",
        }
        for i in range(50)
    ]

    out = exporter.export_answer(
        path=pdf_path,
        records=records,
        question="Детальный анализ финансовых показателей за 2026 год по всем направлениям?",
        headline="Выручка продемонстрировала уверенный рост по ключевым товарным группам",
        details=[
            "Основной прирост обеспечен флагманскими категориями продуктов.",
            "Операционные расходы остались в пределах целевых нормативов.",
        ],
        title="Финансовый отчёт 2026",
    )
    reader = pypdf.PdfReader(out)
    assert len(reader.pages) == 2, (
        f"Отчёт должен быть строго двухстраничным, получено страниц: {len(reader.pages)}"
    )


def test_export_substantive_insight_when_title_duplicated(tmp_path: Path) -> None:
    """Проверка формирования содержательного инсайта без дублирования названия и удаление фразы про Excel."""
    exporter = PdfExporter()
    pdf_path = str(tmp_path / "substantive_insight.pdf")
    records = [
        {"age_bucket": "18-24", "n": 150},
        {"age_bucket": "25-34", "n": 480},
        {"age_bucket": "35-44", "n": 320},
    ]

    out = exporter.export_answer(
        path=pdf_path,
        records=records,
        headline="распр возраста",  # Совпадает с заголовком
        title="распр возраста",
    )
    reader = pypdf.PdfReader(out)
    text = "".join(p.extract_text() or "" for p in reader.pages)
    assert "Выборка содержит 3 записей" in text
    assert "Показатели в выборке: age_bucket, n" in text
    assert "Для выгрузки полного массива данных используйте экспорт в Excel" not in text
    assert "Таблица с детальными показателями приведена в приложении ниже" in text


def test_kpi_formatting_and_extraction_edge_cases(tmp_path: Path) -> None:
    """Тестирование краевых случаев форматирования и извлечения KPI (миллиарды, тысячи, float, вложенные значения)."""
    from app.core.export.pdf import _extract_kpi_value, _format_kpi_number

    # Проверка диапазонов чисел в _format_kpi_number
    assert "2.5 млрд" in _format_kpi_number(2_500_000_000)
    assert "25 000" in _format_kpi_number(25_000)
    assert "12.34" in _format_kpi_number(12.34)
    assert _format_kpi_number(None) == "—"
    assert _format_kpi_number("произвольный текст") == "произвольный текст"

    # 1. Извлечение из словаря без ключа 'value'
    tile_dict = DashboardTile(title="Метрика", is_kpi=True, snapshot_data={"custom_key": 999})
    val, _lbl = _extract_kpi_value(tile_dict)
    assert val == "999"

    # 2. Извлечение из spec.data.values при отсутствии snapshot_data
    tile_spec = DashboardTile(
        title="Метрика из спеки",
        is_kpi=True,
        snapshot_data=None,
        spec={"data": {"values": [{"amount": 777}]}},
    )
    val2, _lbl2 = _extract_kpi_value(tile_spec)
    assert val2 == "777"

    # 3. Пустая спека и snapshot_data=None
    tile_empty = DashboardTile(title="Пустая", is_kpi=True, snapshot_data=None, spec={})
    val3, _lbl3 = _extract_kpi_value(tile_empty)
    assert val3 == "—"


def test_dashboard_tile_duckdb_read_error_fallback(tmp_path: Path) -> None:
    """Если таблица DuckDB не существует, экспорт дашборда не падает с ошибкой, а корректно деградирует."""
    exporter = PdfExporter()
    pdf_path = str(tmp_path / "dash_missing_table.pdf")
    db = DuckDBManager(":memory:")

    dash = Dashboard(
        id="dash-err",
        session_id="sess-err",
        title="Дашборд с отсутствующей таблицей",
        tiles=[
            DashboardTile(
                title="Несуществующая таблица",
                result_table="table_that_does_not_exist",
                spec={"mark": "bar"},
            )
        ],
    )
    out = exporter.export_dashboard(dash, pdf_path, db=db)
    assert Path(out).exists()
    reader = pypdf.PdfReader(out)
    assert len(reader.pages) >= 1
    db.close()


def test_artifacts_export_dashboard_500_error_handling(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Проверка перехвата исключения при генерации PDF дашборда и возврата 500 ошибки."""
    store = DashboardStore(str(tmp_path / "sessions.sqlite"))
    dash = Dashboard(
        id="d-error",
        session_id="s-error",
        title="Дашборд с ошибкой",
        tiles=[],
    )
    store.create(dash)

    def mock_export_dashboard(*args, **kwargs):
        raise RuntimeError("Искусственная ошибка рендеринга")

    monkeypatch.setattr(PdfExporter, "export_dashboard", mock_export_dashboard)

    resp = client.post(
        "/api/artifacts/export",
        json={"session_id": "s-error", "dashboard_id": "d-error", "format": "pdf"},
    )
    assert resp.status_code == 500
    assert "Не удалось сгенерировать PDF" in resp.json()["detail"]


def test_artifacts_export_single_chart_injects_spec_values(client: TestClient) -> None:
    """Проверка инъекции records в spec['data']['values'], когда спека передана без данных."""
    db: DuckDBManager = client.app.dependency_overrides[get_db]()
    db.execute("CREATE TABLE temp_result_v77 AS SELECT 'Группа 1' as g, 10 as v")
    memory = SessionMemory("s-inject", db)
    memory.set_table("raw")
    memory.start_turn("Вопрос")
    memory.finish_turn("ok", result_table="temp_result_v77")

    resp = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "s-inject",
            "result_ref": "temp_result_v77",
            "format": "pdf",
            "spec": {"mark": "bar"},  # Без поля data
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


def test_normalize_spec_strips_chart_titles() -> None:
    """Проверка удаления встроенных заголовков Vega-Lite из спецификации (включая layer/concat/spec)."""
    from app.core.export.charts import normalize_spec

    # 1. Верхнеуровневый заголовок из промпта LLM
    spec_with_title = {
        "title": "оставить только цену для построения гистограммы распределения",
        "mark": "bar",
        "encoding": {"x": {"field": "Price", "title": "Цена"}},
    }
    norm1 = normalize_spec(spec_with_title)
    assert "title" not in norm1
    assert norm1["encoding"]["x"]["title"] == "Цена"  # Подпись оси сохранена

    # 2. Подграфики в layer / concat
    spec_layer = {
        "title": "Общий заголовок",
        "layer": [
            {"title": "Слой 1", "mark": "bar"},
            {"title": "Слой 2", "mark": "line"},
            "non-dict-subview",  # Краевой случай не-dict
        ],
    }
    norm2 = normalize_spec(spec_layer)
    assert "title" not in norm2
    assert "title" not in norm2["layer"][0]
    assert "title" not in norm2["layer"][1]

    # 3. Вложенная спецификация spec
    spec_nested = {
        "title": "Фасетный график",
        "spec": {"title": "Вложенный заголовок", "mark": "point"},
    }
    norm3 = normalize_spec(spec_nested)
    assert "title" not in norm3
    assert "title" not in norm3["spec"]


def test_resolve_dashboard_meta_csv_datasource(tmp_path: Path, monkeypatch) -> None:
    """Разрешение метаданных: имя CSV-файла из папки загрузок uploads/csv_..."""
    monkeypatch.setattr("app.config.settings.storage_dir", str(tmp_path))
    monkeypatch.setattr("app.config.settings.sqlite_path", str(tmp_path / "sessions.sqlite"))

    ds_folder = tmp_path / "uploads" / "csv_test123"
    ds_folder.mkdir(parents=True, exist_ok=True)
    (ds_folder / "customers.csv").write_text("id,name\n1,alice\n")

    db = DuckDBManager(":memory:")
    memory = SessionMemory("sess-csv", db)
    memory.set_meta(title="Сессия анализа клиентов", datasource_id="csv_test123")

    exporter = PdfExporter()
    dash = Dashboard(
        id="d-csv",
        session_id="sess-csv",
        title="Дашборд клиентов",
        tiles=[],
    )

    stitle, source = exporter._resolve_dashboard_meta(dash, db=db)
    assert stitle == "Сессия анализа клиентов"
    assert source == "customers.csv"
    db.close()


def test_resolve_dashboard_meta_db_datasource(tmp_path: Path, monkeypatch) -> None:
    """Разрешение метаданных: имя БД из DatasourceStore."""
    monkeypatch.setattr("app.config.settings.storage_dir", str(tmp_path))
    monkeypatch.setattr("app.config.settings.sqlite_path", str(tmp_path / "sessions.sqlite"))

    from app.core.memory.datasource_store import DatasourceRecord, DatasourceStore

    ds_store = DatasourceStore(str(tmp_path / "sessions.sqlite"))
    ds_store.save(
        DatasourceRecord(
            identity="pg_prod_1",
            kind="postgres",
            dsn="postgresql://user@localhost:5432/analytics",
            description="Корпоративная БД Аналитики",
        )
    )

    db = DuckDBManager(":memory:")
    memory = SessionMemory("sess-db", db)
    memory.set_meta(title="Финансовый аудит", datasource_id="pg_prod_1")

    exporter = PdfExporter()
    dash = Dashboard(
        id="d-db",
        session_id="sess-db",
        title="Финансовый отчет",
        tiles=[],
    )

    stitle, source = exporter._resolve_dashboard_meta(dash, db=db)
    assert stitle == "Финансовый аудит"
    assert source == "Корпоративная БД Аналитики"
    db.close()


def test_resolve_dashboard_meta_table_fallback(tmp_path: Path, monkeypatch) -> None:
    """Разрешение метаданных: откат на имя таблицы memory.table и плитки result_table."""
    monkeypatch.setattr("app.config.settings.storage_dir", str(tmp_path))
    monkeypatch.setattr("app.config.settings.sqlite_path", str(tmp_path / "sessions.sqlite"))

    db = DuckDBManager(":memory:")
    memory = SessionMemory("sess-tbl", db)
    memory.set_meta(title="Сессия без источника", datasource_id=None)
    memory.set_table("sales_summary")

    exporter = PdfExporter()
    dash1 = Dashboard(
        id="d-tbl",
        session_id="sess-tbl",
        title="Отчет по продажам",
        tiles=[],
    )
    _stitle1, source1 = exporter._resolve_dashboard_meta(dash1, db=db)
    assert source1 == "sales_summary"

    # Откат на плитку дашборда при отсутствии сессии
    dash2 = Dashboard(
        id="d-tile-tbl",
        session_id="",
        title="Дашборд без сессии",
        tiles=[DashboardTile(title="Плитка", result_table="public_contracts")],
    )
    _stitle2, source2 = exporter._resolve_dashboard_meta(dash2, db=db)
    assert source2 == "public_contracts"
    db.close()


def test_resolve_dashboard_meta_ignores_raw_uuid_title(tmp_path: Path, monkeypatch) -> None:
    """Сырой UUID сессии никогда не выводится в качестве названия."""
    monkeypatch.setattr("app.config.settings.storage_dir", str(tmp_path))
    monkeypatch.setattr("app.config.settings.sqlite_path", str(tmp_path / "sessions.sqlite"))

    exporter = PdfExporter()
    raw_uuid = "77aa0f77a54c491cb174f6e90af9b28c"
    dash = Dashboard(id="d-uuid", session_id="", title="Дашборд", tiles=[])

    stitle, _ = exporter._resolve_dashboard_meta(dash, session_title=raw_uuid)
    assert stitle is None


def test_resolve_dashboard_meta_exception_handling(monkeypatch) -> None:
    """Перехват исключения при ошибке загрузки SessionMemory."""
    exporter = PdfExporter()
    dash = Dashboard(id="d-err", session_id="err-session", title="Дашборд", tiles=[])

    from app.core.memory import session_memory

    def raise_err(*args, **kwargs):
        raise RuntimeError("DB failure")

    monkeypatch.setattr(session_memory, "SessionMemory", raise_err)
    stitle, source = exporter._resolve_dashboard_meta(dash)
    assert stitle is None
    assert source is None


def test_build_dashboard_header_variations(tmp_path: Path) -> None:
    """Тестирование вариантов шапки дашборда (дублирующееся название, пустые стили, форматирование)."""
    exporter = PdfExporter()

    # styles=None
    elems1 = exporter._build_dashboard_header(
        title="Дашборд",
        created_at=None,
        tile_count=5,
        printable_width=500,
        styles=None,
        session_title="Дашборд",  # Совпадает с title -> Сессия не дублируется
        source_name="data.csv",
    )
    assert len(elems1) == 3

    # Передача session_title и source_name через GET /api/dashboards/{id}/export
    # и POST /api/artifacts/export
    out_pdf = str(tmp_path / "header_test.pdf")
    dash = Dashboard(
        id="d-h",
        session_id="s-h",
        title="Сводный дашборд",
        tiles=[DashboardTile(title="KPI", is_kpi=True, snapshot_data={"value": 10})],
    )
    exporter.export_dashboard(
        dash,
        out_pdf,
        database_name="Недвижимость Бангалора",
        source_name="baza.csv",
    )
    reader = pypdf.PdfReader(out_pdf)
    text = reader.pages[0].extract_text() or ""
    assert "База данных: Недвижимость Бангалора" in text
    assert "Сессия:" not in text
    assert "Источник: baza.csv" in text
    assert "Всего плиток: 1" in text


def test_export_dashboard_pdf_resolves_database_from_profile_memory(tmp_path: Path) -> None:
    """Тест автоматического разрешения человекочитаемого названия базы данных из ProfileMemory."""
    db = DuckDBManager()
    db.execute("CREATE OR REPLACE TABLE bangalore_test AS SELECT 1 AS id, 200 AS price")
    from app.core.memory.profile_memory import ProfileMemory

    ProfileMemory(db)
    db.execute(
        "INSERT OR REPLACE INTO _meta.table_passport "
        "(table_name, row_count, column_count, empty_rows, profiled_at, display_name) "
        "VALUES ('bangalore_test', 1, 2, 0, now(), 'Недвижимость Бангалора')"
    )

    from app.core.memory.session_memory import SessionMemory

    memory = SessionMemory("sess-auto", db)
    memory.set_table("bangalore_test")

    dash = Dashboard(
        id="d-auto-db",
        session_id="sess-auto",
        title="Дашборд дома Индии",
        tiles=[DashboardTile(title="Цены", result_table="bangalore_test")],
    )

    out_pdf = str(tmp_path / "auto_db_test.pdf")
    exporter = PdfExporter()
    exporter.export_dashboard(dash, out_pdf, db=db)

    reader = pypdf.PdfReader(out_pdf)
    text = reader.pages[0].extract_text() or ""
    assert "База данных: Недвижимость Бангалора" in text
    assert "Сессия:" not in text
    assert "Всего плиток: 1" in text


def test_export_dashboard_pdf_resolves_database_from_tile_profile_memory(tmp_path: Path) -> None:
    """Тест разрешения display_name базы данных из плиток, когда сессия не привязана к таблице."""
    db = DuckDBManager()
    db.execute("CREATE OR REPLACE TABLE houses AS SELECT 1 AS id, 100 AS price")
    from app.core.memory.profile_memory import ProfileMemory

    ProfileMemory(db)
    db.execute(
        "INSERT OR REPLACE INTO _meta.table_passport "
        "(table_name, row_count, column_count, empty_rows, profiled_at, display_name) "
        "VALUES ('houses', 1, 2, 0, now(), 'Жилая недвижимость')"
    )

    dash = Dashboard(
        id="d-tiles-only",
        session_id="no-sess",
        title="Сводный дашборд",
        tiles=[DashboardTile(title="Цены домов", result_table="houses")],
    )

    out_pdf = str(tmp_path / "tiles_only.pdf")
    exporter = PdfExporter()
    exporter.export_dashboard(dash, out_pdf, db=db)

    reader = pypdf.PdfReader(out_pdf)
    text = reader.pages[0].extract_text() or ""
    assert "База данных: Жилая недвижимость" in text
    assert "Всего плиток: 1" in text


def test_export_dashboard_pdf_resolves_database_handles_exception(tmp_path: Path) -> None:
    """Проверка устойчивости к ошибкам чтения display_name из плитки."""

    class BrokenDb:
        pass

    dash = Dashboard(
        id="d-broken-db",
        session_id="no-sess",
        title="Сводный дашборд",
        tiles=[DashboardTile(title="Цены", result_table="houses")],
    )
    out_pdf = str(tmp_path / "broken_db.pdf")
    exporter = PdfExporter()
    exporter.export_dashboard(dash, out_pdf, db=BrokenDb())  # type: ignore
    assert Path(out_pdf).exists()


def test_tile_card_chart_failure_and_table_truncation(tmp_path: Path, monkeypatch) -> None:
    """Проверка плитки при сбое рендеринга графика, усечении таблицы до 12 строк и пустой плитке."""
    monkeypatch.setattr("app.core.export.pdf.render_chart_png", lambda *args, **kwargs: None)

    fifteen_rows = [{"id": i, "val": i * 10} for i in range(1, 16)]
    table_tile = DashboardTile(
        title="Табличная плитка",
        spec={"usermeta": {"chart_type": "table"}},
        snapshot_data=fifteen_rows,
    )
    chart_tile = DashboardTile(
        title="Графическая плитка с ошибкой",
        spec={"mark": "bar"},
        snapshot_data=fifteen_rows,
    )
    empty_tile = DashboardTile(
        title="Пустая плитка",
        snapshot_data=None,
    )

    dash = Dashboard(
        id="d-trunc-test",
        session_id="sess-trunc",
        title="Дашборд с усечением",
        tiles=[table_tile, chart_tile, empty_tile],
    )

    out_pdf = str(tmp_path / "trunc_test.pdf")
    exporter = PdfExporter()
    exporter.export_dashboard(dash, out_pdf)

    reader = pypdf.PdfReader(out_pdf)
    text = "".join(p.extract_text() or "" for p in reader.pages)

    # 1. Проверяем уведомление о сбое графика
    assert "График не удалось отобразить в PDF; ниже представлены табличные данные" in text
    # 2. Проверяем сноску об ограничении строк таблицы до 12
    assert "Показаны первые 12 строк из 15" in text
    # 3. Проверяем уведомление об отсутствии данных
    assert "Данные для отображения плитки отсутствуют" in text


def test_export_dashboard_api_database_name_and_unique_filename(
    client: TestClient, monkeypatch
) -> None:
    """Проверка параметра database_name, custom title, обработки ошибок и генерации уникальных файлов на диске."""
    dash = Dashboard(
        id="d-unique-file-test",
        session_id="sess-file-test",
        title="Аналитический Срез",
        tiles=[
            DashboardTile(title="KPI Метрика", is_kpi=True, snapshot_data={"value": 100}),
        ],
    )
    store = DashboardStore()
    store.create(dash)

    # 1. Экспорт в PDF с database_name
    resp1 = client.get(
        "/api/dashboards/d-unique-file-test/export?format=pdf&database_name=Продажи2026"
    )
    assert resp1.status_code == 200
    assert resp1.headers["content-type"] == "application/pdf"
    # Клиент получает чистое имя файла
    assert "attachment" in resp1.headers["content-disposition"]
    assert ".pdf" in resp1.headers["content-disposition"]

    reader1 = pypdf.PdfReader(io.BytesIO(resp1.content))
    text1 = "".join(p.extract_text() or "" for p in reader1.pages)
    assert "База данных: Продажи2026" in text1

    # 2. Повторный экспорт того же дашборда с переопределением title
    resp2 = client.get("/api/dashboards/d-unique-file-test/export?format=pdf&title=НовыйЗаголовок")
    assert resp2.status_code == 200

    # 3. Экспорт несуществующего дашборда -> 404
    resp_404 = client.get("/api/dashboards/non-existent-dash/export")
    assert resp_404.status_code == 404

    # 4. Ошибка генерации PDF -> 500
    def broken_export(*args, **kwargs):
        raise RuntimeError("PDF generation failed")

    monkeypatch.setattr("app.api.routes.dashboards.PdfExporter.export_dashboard", broken_export)
    resp_500 = client.get("/api/dashboards/d-unique-file-test/export?format=pdf")
    assert resp_500.status_code == 500

    # 5. Экспорт в Excel
    resp_xlsx = client.get("/api/dashboards/d-unique-file-test/export?format=xlsx")
    assert resp_xlsx.status_code == 200
    assert (
        resp_xlsx.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_export_dashboard_pdf_heatmap_preserves_unpivoted_cells(tmp_path: Path) -> None:
    """Проверка того, что экспорт PDF не затирает unpivoted ячейки heatmap сырыми строками DuckDB."""
    from app.core.viz.vega_spec_builder import build_vega_spec

    raw_corr_records = [
        {"male": 1.0, "age": -0.14, "isAlive": -0.05},
        {"male": -0.14, "age": 1.0, "isAlive": 0.65},
        {"male": -0.05, "age": 0.65, "isAlive": 1.0},
    ]
    heatmap_spec = build_vega_spec("heatmap", data=raw_corr_records, title="Матрица корреляций")
    assert len(heatmap_spec["data"]["values"]) == 9

    # Плитка содержит спеку с 9 unpivoted значениями, а snapshot_data содержит 3 сырые строки DuckDB
    tile = DashboardTile(
        title="Матрица корреляций",
        spec=heatmap_spec,
        snapshot_data=raw_corr_records,
    )
    dash = Dashboard(
        id="d-heatmap-test",
        session_id="sess-heatmap",
        title="Дашборд с тепловой картой",
        tiles=[tile],
    )
    out_pdf = str(tmp_path / "heatmap_test.pdf")
    exporter = PdfExporter()
    exporter.export_dashboard(dash, out_pdf)

    reader = pypdf.PdfReader(out_pdf)
    assert len(reader.pages) >= 1
    images = [img for page in reader.pages for img in page.images]
    assert len(images) >= 1
    assert len(images[0].data) > 1000


def test_export_dashboard_pdf_heatmap_rebuilds_when_spec_values_missing(tmp_path: Path) -> None:
    """Если у плитки в spec.data.values нет готовых ячеек, экспорт собирает их из records."""
    raw_corr_records = [
        {"male": 1.0, "age": -0.14},
        {"male": -0.14, "age": 1.0},
    ]
    tile = DashboardTile(
        title="Матрица корреляций без values",
        spec={"usermeta": {"chart_type": "heatmap"}},
        snapshot_data=raw_corr_records,
    )
    dash = Dashboard(
        id="d-heatmap-rebuild",
        session_id="sess-heatmap",
        title="Дашборд",
        tiles=[tile],
    )
    out_pdf = str(tmp_path / "heatmap_rebuild.pdf")
    exporter = PdfExporter()
    exporter.export_dashboard(dash, out_pdf)
    reader = pypdf.PdfReader(out_pdf)
    assert len(reader.pages) >= 1


def test_build_tile_card_keeps_title_and_chart_together() -> None:
    """Заголовок плитки, инсайт и график должны объединяться в единый KeepTogether блок."""
    from reportlab.platypus import KeepTogether, Paragraph, Table

    from app.core.dashboards.models import DashboardTile
    from app.core.export.pdf import PdfExporter

    tile = DashboardTile(
        title="Динамика выручки по кварталам",
        insight="Рост выручки составил 15% по сравнению с прошлым годом",
        spec={
            "mark": "bar",
            "data": {"values": [{"q": "Q1", "v": 10}, {"q": "Q2", "v": 20}]},
            "encoding": {
                "x": {"field": "q", "type": "nominal"},
                "y": {"field": "v", "type": "quantitative"},
            },
        },
    )

    exporter = PdfExporter()
    styles = exporter._build_styles()
    card_elements = exporter._build_tile_card(tile, printable_width=523.27, styles=styles)

    assert len(card_elements) == 1
    keep_together = card_elements[0]
    assert isinstance(keep_together, KeepTogether)

    # Проверяем, что внутри KeepTogether находятся заголовок, блок инсайта и график
    inner = keep_together._content
    assert any(isinstance(f, Paragraph) and "Динамика выручки" in f.text for f in inner)
    assert any(isinstance(f, Table) for f in inner)  # блок инсайта
    # Проверяем наличие визуализации (картинка Image)
    from reportlab.platypus import Image as RLImage

    assert any(isinstance(f, RLImage) for f in inner)


def test_export_dashboard_pdf_table_spec_renders_as_table_not_chart(tmp_path: Path) -> None:
    """Плитка с табличной спекой рендерится как таблица данных, а не как график-картинка."""
    tile = DashboardTile(
        title="Таблица ключевых метрик",
        spec={
            "chart_type": "table",
            "data": {
                "values": [
                    {"Метрика": "CAC", "Значение": 1500},
                    {"Метрика": "LTV", "Значение": 8000},
                ]
            },
        },
    )
    dash = Dashboard(
        id="d-table-tile",
        session_id="sess-table",
        title="Дашборд с таблицей",
        tiles=[tile],
    )
    out_pdf = str(tmp_path / "table_tile_dash.pdf")
    exporter = PdfExporter()
    exporter.export_dashboard(dash, out_pdf)

    reader = pypdf.PdfReader(out_pdf)
    assert len(reader.pages) >= 1
    # Таблица не должна содержать сгенерированных картинок графиков
    images = [img for page in reader.pages for img in page.images]
    assert len(images) == 0

    text = "".join(p.extract_text() or "" for p in reader.pages)
    assert "Таблица ключевых метрик" in text
    assert "CAC" in text
    assert "LTV" in text
