import io
from pathlib import Path

import pypdf
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.data.duckdb_manager import DuckDBManager
from app.core.export.charts import normalize_spec, render_chart_png
from app.core.export.pdf import PdfExporter
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
def client(tmp_path: Path):
    db = DuckDBManager(":memory:")
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()
    db.close()


def test_1_render_chart_png_converts_container_width() -> None:
    """Тест 1: Рендеринг графика render_chart_png — замена width: container, получение валидного PNG-заголовка."""
    normalized = normalize_spec(SAMPLE_SPEC, width=640)
    assert normalized["width"] == 640
    assert normalized["height"] == 360

    png = render_chart_png(SAMPLE_SPEC, width=640, scale=2.0)
    assert png is not None
    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000

    # Проверка отказоустойчивости при битых/пустых данных
    assert render_chart_png(None) is None
    assert render_chart_png({}) is None
    assert render_chart_png({"invalid": "spec"}) is None


def test_2_and_3_export_full_report_generates_valid_pdf_with_cyrillic_and_image(
    tmp_path: Path,
) -> None:
    """Тест 2 и 3: Генерация PDF полного отчёта и валидация через pypdf (кириллица, картинка)."""
    pdf_path = str(tmp_path / "full_report.pdf")
    chart_png = render_chart_png(SAMPLE_SPEC)
    assert chart_png is not None

    question = "Как изменилась выручка по категориям в 2026 году?"
    headline = "Выручка выросла на 14.2%, ключевой драйвер — Электроника (+28%)"
    details = [
        "Категория «Электроника» принесла 42.5 млн ₽ (прирост +28.0% к предыдущему кварталу).",
        "Категория «Одежда» показала незначительный спад на 1.3%.",
        "Средний чек по всем направлениям вырос с 3 200 до 3 650 ₽.",
    ]
    # Создаём больше 50 строк для проверки ограничения топ-50
    records = [
        {
            "№": i + 1,
            "Категория": f"Товарная группа {i + 1}",
            "Выручка, ₽": 1000000.50 + i * 5000,
            "Доля": f"{50 - i * 0.5:.1f}%",
        }
        for i in range(60)
    ]

    exporter = PdfExporter()
    out = exporter.export_answer(
        path=pdf_path,
        records=records,
        question=question,
        headline=headline,
        details=details,
        chart_png=chart_png,
        title="Тестовый отчёт Q3",
    )
    assert out == pdf_path
    assert Path(pdf_path).exists()
    assert Path(pdf_path).stat().st_size > 5000

    # Проверка структуры через pypdf
    reader = pypdf.PdfReader(pdf_path)
    assert len(reader.pages) >= 2, "Двухстраничный мемо должен иметь минимум 2 страницы"

    # Проверка первой страницы: кириллический текст и картинка
    page1_text = reader.pages[0].extract_text()
    assert "ВОПРОС ИССЛЕДОВАНИЯ" in page1_text
    assert "Выручка выросла" in page1_text
    assert "Электроника" in page1_text
    assert len(reader.pages[0].images) >= 1, "На странице 1 должна быть встроенная картинка графика"

    # Проверка второй страницы (и последующих): табличные данные
    all_pages_text = "".join(page.extract_text() for page in reader.pages)
    assert "Таблица результатов" in all_pages_text
    assert "Товарная группа 1" in all_pages_text
    assert "Товарная группа 50" in all_pages_text
    # 51-й строки быть не должно в таблице (ограничение топ-50)
    assert "Товарная группа 55" not in all_pages_text


def test_4_export_minimal_table_only_report(tmp_path: Path) -> None:
    """Тест 4: Генерация PDF в минимальном режиме (только таблица без вопроса и графика)."""
    pdf_path = str(tmp_path / "table_only.pdf")
    records = [{"ID": i, "Название": f"Позиция {i}", "Сумма": i * 100} for i in range(10)]

    exporter = PdfExporter()
    out = exporter.export_answer(
        path=pdf_path,
        records=records,
    )
    assert out == pdf_path
    assert Path(pdf_path).exists()

    reader = pypdf.PdfReader(pdf_path)
    text = "".join(page.extract_text() for page in reader.pages)
    assert "Таблица результатов" in text
    assert "Позиция 0" in text
    assert "Позиция 9" in text

    # Проверка совместимого метода .export()
    compat_path = str(tmp_path / "compat.pdf")
    exporter.export(headline="Заголовок", details=["Деталь"], records=records, path=compat_path)
    assert Path(compat_path).exists()


def test_5_api_export_pdf_endpoint(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    """Тест 5: API-тест эндпоинта POST /api/artifacts/export с format='pdf'."""
    monkeypatch.setattr("app.config.settings.storage_dir", str(tmp_path))

    # Подготавливаем таблицу в DuckDB
    db: DuckDBManager = client.app.dependency_overrides[get_db]()
    db.execute(
        "CREATE TABLE temp_result_v1 AS SELECT 'Электроника' AS category, 42500000 AS revenue "
        "UNION ALL SELECT 'Одежда', 21000000"
    )

    payload = {
        "session_id": "test_session_123",
        "format": "pdf",
        "result_ref": "temp_result_v1",
        "question": "Какова динамика по категориям?",
        "headline": "Лидер продаж — Электроника",
        "details": ["Электроника показала рост", "Одежда на прежнем уровне"],
        "spec": SAMPLE_SPEC,
    }

    response = client.post("/api/artifacts/export", json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith('attachment; filename="agent_export_')
    assert response.content.startswith(b"%PDF")

    # Проверяем, что полученный PDF валиден
    reader = pypdf.PdfReader(io.BytesIO(response.content))
    assert len(reader.pages) >= 1
    page_text = reader.pages[0].extract_text()
    assert "Лидер продаж" in page_text or "ВОПРОС ИССЛЕДОВАНИЯ" in page_text


def test_api_export_pdf_validation_errors(client: TestClient) -> None:
    """Проверка ошибок валидации эндпоинта /api/artifacts/export."""
    # Неверный формат result_ref
    resp = client.post(
        "/api/artifacts/export",
        json={"session_id": "s1", "format": "pdf", "result_ref": "malicious_table"},
    )
    assert resp.status_code == 400
    assert "temp_result_vN" in resp.json()["detail"]

    # Пустой запрос без result_ref и без инсайта
    resp = client.post(
        "/api/artifacts/export",
        json={"session_id": "s1", "format": "pdf"},
    )
    assert resp.status_code == 400

    # result_ref не найден и нет инсайта
    resp = client.post(
        "/api/artifacts/export",
        json={"session_id": "s1", "format": "pdf", "result_ref": "temp_result_v999"},
    )
    assert resp.status_code == 404


def test_api_export_xlsx_endpoint(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    """Тест экспорта в Excel через эндпоинт /api/artifacts/export."""
    monkeypatch.setattr("app.config.settings.storage_dir", str(tmp_path))
    db: DuckDBManager = client.app.dependency_overrides[get_db]()
    db.execute("CREATE TABLE temp_result_v2 AS SELECT 'Товар' AS item, 100 AS price")
    resp = client.post(
        "/api/artifacts/export",
        json={"session_id": "s1", "format": "xlsx", "result_ref": "temp_result_v2"},
    )
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers["content-type"]
    assert resp.headers["content-disposition"].startswith('attachment; filename="agent_export_')


def test_charts_edge_cases(monkeypatch) -> None:
    """Проверка краевых случаев в модуле charts."""
    # autosize string и dict
    spec_fit_str = {**SAMPLE_SPEC, "autosize": "fit"}
    norm_str = normalize_spec(spec_fit_str)
    assert norm_str["autosize"] == "pad"

    spec_fit_dict = {**SAMPLE_SPEC, "autosize": {"type": "fit-x"}}
    norm_dict = normalize_spec(spec_fit_dict)
    assert norm_dict["autosize"]["type"] == "pad"

    # Имитация отсутствия пакета vl_convert
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "vl_convert":
            raise ImportError("no vl_convert")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert render_chart_png(SAMPLE_SPEC) is None


def test_pdf_exporter_edge_cases(tmp_path: Path) -> None:
    """Проверка краевых случаев в PdfExporter."""
    exporter = PdfExporter()

    # Форматирование значений ячеек
    assert exporter._format_cell_value(None) == "—"
    assert exporter._format_cell_value(1000) == "1 000"
    assert exporter._format_cell_value(1234.567) == "1 234.57"
    assert exporter._format_cell_value("обычный текст") == "обычный текст"

    # Пустая таблица
    assert exporter._build_data_table([], 500, exporter._build_styles()) is None
    assert exporter._build_data_table([{}], 500, exporter._build_styles()) is None

    # Битое изображение
    assert exporter._build_image(b"not an image", 500, 200) is None

    # Полностью пустой отчёт
    empty_pdf = str(tmp_path / "empty.pdf")
    exporter.export_answer(empty_pdf)
    assert Path(empty_pdf).exists()
