import io
from pathlib import Path

import pypdf
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.data.duckdb_manager import DuckDBManager
from app.core.export.charts import normalize_spec, render_chart_png
from app.core.export.pdf import PdfExporter, sanitize_text, truncate_text
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


def test_5_api_export_pdf_endpoint(client: TestClient, tmp_path: Path) -> None:
    """Тест 5: API-тест эндпоинта POST /api/artifacts/export с format='pdf'."""
    # Подготавливаем таблицу в DuckDB
    db: DuckDBManager = client.app.dependency_overrides[get_db]()
    db.execute(
        "CREATE TABLE temp_result_v1 AS SELECT 'Электроника' AS category, 42500000 AS revenue "
        "UNION ALL SELECT 'Одежда', 21000000"
    )

    # Привязываем сессию и промежуточный результат к памяти
    memory = SessionMemory("test_session_123", db)
    memory.set_table("raw")
    memory.start_turn("Вопрос")
    memory.finish_turn("ok", result_table="temp_result_v1")

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
    assert "attachment;" in response.headers["content-disposition"]
    assert ".pdf" in response.headers["content-disposition"]
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

    # Экспорт в xlsx без result_ref
    resp = client.post(
        "/api/artifacts/export",
        json={"session_id": "s1", "format": "xlsx"},
    )
    assert resp.status_code == 400
    assert "result_ref" in resp.json()["detail"]


def test_api_export_xlsx_endpoint(client: TestClient, tmp_path: Path) -> None:
    """Тест экспорта в Excel через эндпоинт /api/artifacts/export."""
    db: DuckDBManager = client.app.dependency_overrides[get_db]()
    db.execute("CREATE TABLE temp_result_v2 AS SELECT 'Товар' AS item, 100 AS price")

    memory = SessionMemory("s1", db)
    memory.set_table("raw")
    memory.start_turn("Вопрос")
    memory.finish_turn("ok", result_table="temp_result_v2")

    resp = client.post(
        "/api/artifacts/export",
        json={"session_id": "s1", "format": "xlsx", "result_ref": "temp_result_v2"},
    )
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers["content-type"]
    assert "attachment;" in resp.headers["content-disposition"]
    assert ".xlsx" in resp.headers["content-disposition"]


def test_xml_escaping_in_pdf_report(tmp_path: Path) -> None:
    """Тест экранирования XML-символов в тексте пользователя и таблицах."""
    pdf_path = str(tmp_path / "xml_escape_test.pdf")

    # Текст с символами, опасными для парсера ReportLab (<, >, &, теги)
    question = "Выручка < 100 млн & > 50 млн? <b>fake</b>"
    headline = "Анализ: маржа & рентабельность <тег & незакрытый>"
    details = [
        "Компании: AT&T, P&G, Johnson & Johnson",
        "Сравнение: X < Y и A > B",
        "Символ рубля: 1 500 ₽ и тег <i>курсив</i>",
    ]
    records = [
        {
            "№": 1,
            "<Категория & ID>": "<b>XSS-like value</b> & <unclosed>",
            "Значение": "x < 50",
            "Выручка, ₽": 2500000,
        }
    ]

    exporter = PdfExporter()
    out = exporter.export_answer(
        path=pdf_path,
        records=records,
        question=question,
        headline=headline,
        details=details,
        title="Тест & Безопасность <v1>",
    )
    assert out == pdf_path
    assert Path(pdf_path).exists()

    reader = pypdf.PdfReader(pdf_path)
    all_text = "".join(page.extract_text() for page in reader.pages)

    # Проверяем, что теги не были съедены парсером, а отображаются как буквальный текст
    assert "<b>fake</b>" in all_text
    assert "AT&T" in all_text
    assert "руб." in all_text
    assert "<b>XSS-like value</b>" in all_text
    assert "<unclosed>" in all_text or "unclosed" in all_text


def test_api_export_suppressed_error_fixed_raises_404(client: TestClient) -> None:
    """Проверка исправления подавления ошибок: если result_ref не удаётся прочитать, возвращается 404, а не пустой отчёт."""
    db: DuckDBManager = client.app.dependency_overrides[get_db]()
    memory = SessionMemory("session_err", db)
    memory.set_table("raw")
    # Записываем шаг, но таблицы в DuckDB нет
    memory.start_turn("Вопрос")
    memory.finish_turn("ok", result_table="temp_result_v99")

    payload = {
        "session_id": "session_err",
        "format": "pdf",
        "result_ref": "temp_result_v99",
        "headline": "Присутствует инсайт",
        "question": "Присутствует вопрос",
    }
    resp = client.post("/api/artifacts/export", json=payload)
    # Ранее возвращался 200 с неполным отчётом без таблицы, теперь должен возвращаться 404
    assert resp.status_code == 404
    assert "temp_result_v99" in resp.json()["detail"]


def test_api_export_session_isolation(client: TestClient) -> None:
    """Проверка изоляции сессий: нельзя получить результат другой сессии."""
    db: DuckDBManager = client.app.dependency_overrides[get_db]()
    db.execute("CREATE TABLE temp_result_v10 AS SELECT 1 AS id, 'secret' AS val")

    # Сессия А владеет temp_result_v10
    mem_a = SessionMemory("session_alice", db)
    mem_a.set_table("raw")
    mem_a.start_turn("Вопрос")
    mem_a.finish_turn("ok", result_table="temp_result_v10")

    # Сессия Б существует, но не владеет temp_result_v10
    mem_b = SessionMemory("session_bob", db)
    mem_b.set_table("raw")

    # Сессия Б пытается запросить результат Сессии А
    resp_b = client.post(
        "/api/artifacts/export",
        json={"session_id": "session_bob", "format": "xlsx", "result_ref": "temp_result_v10"},
    )
    assert resp_b.status_code == 404
    assert "не найден в сессии" in resp_b.json()["detail"]

    # Несуществующая сессия
    resp_c = client.post(
        "/api/artifacts/export",
        json={"session_id": "session_charlie", "format": "xlsx", "result_ref": "temp_result_v10"},
    )
    assert resp_c.status_code == 404
    assert "не найдена" in resp_c.json()["detail"]

    # Пустой session_id
    resp_empty = client.post(
        "/api/artifacts/export",
        json={"session_id": "  ", "format": "xlsx", "result_ref": "temp_result_v10"},
    )
    assert resp_empty.status_code == 400

    # Сессия А успешно экспортирует свой результат
    resp_a = client.post(
        "/api/artifacts/export",
        json={"session_id": "session_alice", "format": "xlsx", "result_ref": "temp_result_v10"},
    )
    assert resp_a.status_code == 200


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

    assert fake_import("math") is not None
    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert render_chart_png(SAMPLE_SPEC) is None


def test_pdf_exporter_edge_cases(tmp_path: Path) -> None:
    """Проверка краевых случаев в PdfExporter."""
    exporter = PdfExporter()

    # Проверка функции sanitize_text
    assert sanitize_text("<b>тест & пример</b>") == "&lt;b&gt;тест &amp; пример&lt;/b&gt;"
    assert sanitize_text("100 ₽") == "100 руб."
    assert sanitize_text(None) == ""

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

    # Отчёт только с headline (без details)
    h_only_pdf = str(tmp_path / "h_only.pdf")
    exporter.export_answer(h_only_pdf, headline="Только заголовок")
    assert Path(h_only_pdf).exists()

    # Отчёт только с details (без headline) и пробельным вопросом
    d_only_pdf = str(tmp_path / "d_only.pdf")
    exporter.export_answer(
        d_only_pdf, question="   \n   ", details=["Только деталь 1", "Только деталь 2"]
    )
    assert Path(d_only_pdf).exists()

    # Полностью пустой отчёт
    empty_pdf = str(tmp_path / "empty.pdf")
    exporter.export_answer(empty_pdf)
    assert Path(empty_pdf).exists()


def test_api_export_empty_result_ref_is_404(client: TestClient) -> None:
    """Проверка ошибки 404, если результат DuckDB пуст (0 записей)."""
    db: DuckDBManager = client.app.dependency_overrides[get_db]()
    db.execute("CREATE TABLE temp_result_v77 AS SELECT 1 AS id WHERE 1=0")
    memory = SessionMemory("s_empty_test", db)
    memory.set_table("raw")
    memory.start_turn("Вопрос")
    memory.finish_turn("ok", result_table="temp_result_v77")

    resp = client.post(
        "/api/artifacts/export",
        json={"session_id": "s_empty_test", "format": "pdf", "result_ref": "temp_result_v77"},
    )
    assert resp.status_code == 404
    assert "пуст" in resp.json()["detail"]


def test_api_export_pdf_insight_only(client: TestClient) -> None:
    """Проверка успешного экспорта отчёта только с инсайтом (без result_ref)."""
    db: DuckDBManager = client.app.dependency_overrides[get_db]()
    memory = SessionMemory("s_insight_only", db)
    memory.set_table("raw")

    resp = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "s_insight_only",
            "format": "pdf",
            "headline": "Ключевой инсайт",
            "details": ["Пункт 1"],
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"


def test_truncate_text_helper() -> None:
    """Проверка функции безопасного усечения текста truncate_text."""
    assert truncate_text("короткий", 10) == "короткий"
    assert truncate_text("очень длинный текст", 10) == "очень д..."
    assert truncate_text(None, 10) == ""
    assert truncate_text("  пробелы  ", 20) == "пробелы"


def test_long_user_text_does_not_raise_layout_error(tmp_path: Path) -> None:
    """Тест предотвращения LayoutError при передаче сверхдлинного пользовательского текста."""
    pdf_path = str(tmp_path / "overflow_test.pdf")

    # Сверхдлинный вопрос, вывод и детали
    huge_question = "Вопрос аналитика с подробным описанием контекста: " * 50  # ~2500 символов
    huge_headline = "Главный аналитический вывод с множеством факторов: " * 40  # ~2000 символов
    huge_details = [
        f"Детальный пункт {i}: " + ("пояснение к фактору " * 30)  # ~600 символов
        for i in range(15)
    ]
    # Таблица с ячейками, содержащими длинные строки
    records = [
        {
            "Колонка 1": f"Значение строки {r}: " + ("очень длинное текстовое описание " * 20),
            "Колонка 2": 1000000 + r * 500,
            "Колонка 3": "Нормальный текст",
        }
        for r in range(25)
    ]

    exporter = PdfExporter()
    out = exporter.export_answer(
        path=pdf_path,
        records=records,
        question=huge_question,
        headline=huge_headline,
        details=huge_details,
        title="Сверхдлинный отчёт " * 10,
    )
    assert out == pdf_path
    assert Path(pdf_path).exists()

    # Проверяем, что документ успешно собрался и разбился на страницы
    reader = pypdf.PdfReader(pdf_path)
    assert len(reader.pages) >= 3, "Многостраничный контент должен распределиться между страницами"
    for page in reader.pages:
        assert len(page.extract_text()) > 0, "Каждая страница должна содержать валидный текст"


def test_api_export_with_turn_lifecycle(client: TestClient) -> None:
    """Тест экспорта по сессии с современной походовой архитектурой (start_turn -> finish_turn)."""
    db: DuckDBManager = client.app.dependency_overrides[get_db]()
    db.execute("CREATE TABLE temp_result_v50 AS SELECT 'Ноутбуки' AS product, 500000 AS revenue")

    memory = SessionMemory("turn_session_50", db)
    memory.set_table("raw")
    memory.start_turn("Какая выручка по ноутбукам?")
    memory.record_chart("bar", "temp_result_v50", "Выручка по ноутбукам")
    memory.finish_turn(
        "ok",
        headline="Выручка составила 500 тыс. руб.",
        result_table="temp_result_v50",
        result_columns=["product", "revenue"],
    )

    assert memory.owns_result("temp_result_v50") is True

    payload = {
        "session_id": "turn_session_50",
        "format": "pdf",
        "result_ref": "temp_result_v50",
        "question": "Какая выручка по ноутбукам?",
        "headline": "Выручка составила 500 тыс. руб.",
        "details": ["План выполнен на 100%"],
        "spec": SAMPLE_SPEC,
    }
    resp = client.post("/api/artifacts/export", json=payload)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    reader = pypdf.PdfReader(io.BytesIO(resp.content))
    assert len(reader.pages) >= 2
    full_text = "".join(p.extract_text() for p in reader.pages)
    assert "Выручка составила 500 тыс. руб." in full_text
    assert "Ноутбуки" in full_text


def test_validate_chart_spec_security_and_dos_protection() -> None:
    """Тест валидации спецификации графика против DoS, OOM и SSRF."""
    from app.core.export.charts import validate_chart_spec

    # 1. Запрет data.sequence (генератор огромных массивов, ведущий к fatal OOM в V8)
    seq_spec = {
        "data": {"sequence": {"start": 0, "stop": 3000000, "as": "x"}},
        "mark": "point",
        "encoding": {"x": {"field": "x"}},
    }
    with pytest.raises(ValueError, match="data.sequence запрещён"):
        validate_chart_spec(seq_spec)

    # 2. Запрет data.url (SSRF)
    url_spec = {
        "data": {"url": "http://169.254.169.254/latest/meta-data"},
        "mark": "bar",
    }
    with pytest.raises(ValueError, match="data.url запрещены"):
        validate_chart_spec(url_spec)

    # 3. Запрет генераторов в слоях layer
    layer_spec = {
        "layer": [{"data": {"sequence": {"start": 0, "stop": 100, "as": "x"}}, "mark": "line"}]
    }
    with pytest.raises(ValueError, match="data.sequence запрещён"):
        validate_chart_spec(layer_spec)

    # 4. Ограничение количества строк в data.values
    huge_values = [{"x": i, "y": i * 2} for i in range(1500)]
    valid_spec = {"data": {"values": huge_values}, "mark": "bar"}
    cleaned = validate_chart_spec(valid_spec)
    assert len(cleaned["data"]["values"]) == 1000


def test_render_chart_png_dos_crash_safety() -> None:
    """Тест изоляции рендеринга: опасный spec не роняет процесс сервера."""
    from app.core.export.charts import render_chart_png

    crash_spec = {
        "data": {"sequence": {"start": 0, "stop": 3000000, "as": "x"}},
        "mark": "point",
        "encoding": {"x": {"field": "x", "type": "quantitative"}},
    }
    # Функция должна вернуть None и не завершить процесс с кодом 133
    result = render_chart_png(crash_spec)
    assert result is None


def test_missing_dejavu_font_raises_runtime_error(monkeypatch) -> None:
    """Проверка явной ошибки RuntimeError, если системный шрифт DejaVu Sans не найден."""
    monkeypatch.setattr("app.core.export.pdf.FONT_SEARCH_PATHS", [])
    monkeypatch.setattr("app.core.export.pdf._FONTS_REGISTERED", False)
    with pytest.raises(RuntimeError, match="DejaVu"):
        from app.core.export.pdf import ensure_fonts_registered

        ensure_fonts_registered()


def test_api_export_pdf_runtime_error_returns_500(client: TestClient, monkeypatch) -> None:
    """Эндпоинт экспорта возвращает 500 при RuntimeError (например, сбое шрифтов)."""
    sid = "session_runtime_err"
    memory = SessionMemory(sid)
    memory.set_table("test_table")
    memory.start_turn("Вопрос")
    memory.finish_turn(status="ok")

    def _broken_export(*args, **kwargs):
        raise RuntimeError("Тестовый сбой шрифтов DejaVu")

    monkeypatch.setattr("app.core.export.pdf.PdfExporter.export_answer", _broken_export)

    resp = client.post(
        "/api/artifacts/export",
        json={
            "session_id": sid,
            "format": "pdf",
            "question": "Тест",
        },
    )
    assert resp.status_code == 500
    assert "Тестовый сбой шрифтов DejaVu" in resp.json()["detail"]


def test_validate_chart_spec_all_guardrails() -> None:
    """Дополнительные проверки валидации spec: типы, лимит размера, graticule, sphere, вложенные spec."""
    from app.core.export.charts import validate_chart_spec

    with pytest.raises(TypeError, match="dict"):
        validate_chart_spec("invalid_type")  # type: ignore

    with pytest.raises(ValueError, match="превышает лимит"):
        validate_chart_spec({"data": {"values": [{"k": "v" * 1000} for _ in range(300)]}})

    with pytest.raises(ValueError, match="Генераторы данных запрещены"):
        validate_chart_spec({"data": {"graticule": True}})

    with pytest.raises(ValueError, match="Генераторы данных запрещены"):
        validate_chart_spec({"data": {"sphere": True}})

    with pytest.raises(ValueError, match="data.sequence запрещён"):
        validate_chart_spec({"spec": {"data": {"sequence": {"start": 0, "stop": 5}}}})


def test_render_chart_png_timeout_and_oserror(monkeypatch) -> None:
    """Обработка таймаута и системных ошибок запуска подпроцесса рендера."""
    import subprocess

    from app.core.export.charts import render_chart_png

    def _timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="python", timeout=1.0)

    monkeypatch.setattr(subprocess, "run", _timeout_run)
    assert render_chart_png({"mark": "bar"}) is None

    def _oserror_run(*args, **kwargs):
        raise OSError("Subprocess execution failed")

    monkeypatch.setattr(subprocess, "run", _oserror_run)
    assert render_chart_png({"mark": "bar"}) is None


def test_validate_chart_spec_non_serializable_and_non_dict_children() -> None:
    """Проверка несериализуемого JSON и не-словарей в layer."""
    from app.core.export.charts import validate_chart_spec

    with pytest.raises(ValueError, match="не сериализуется в JSON"):
        validate_chart_spec({"invalid": object()})

    # Узел layer со строковым элементом вместо словаря
    spec = {"layer": ["not-a-dict-item"]}
    cleaned = validate_chart_spec(spec)
    assert cleaned["layer"] == ["not-a-dict-item"]

    # Проверка обнаружения url и sequence на любой глубине (facet, списки, трансформации)
    with pytest.raises(ValueError, match="data.url запрещены"):
        validate_chart_spec(
            {"facet": {"row": {"data": {"url": "http://127.0.0.1:8767/internal.csv"}}}}
        )

    with pytest.raises(ValueError, match="data.sequence запрещён"):
        validate_chart_spec(
            {"transform": [{"lookup": "k", "from": {"data": {"sequence": {"stop": 100}}}}]}
        )

    rows = {"data": {"values": [{"url": "a.ru", "sequence": 1}]}, "mark": "bar"}
    assert validate_chart_spec(rows)["data"]["values"] == [{"url": "a.ru", "sequence": 1}]

    datasets = {"datasets": {"big": [{"x": index} for index in range(1500)], "name": "x"}}
    cleaned = validate_chart_spec(datasets)
    assert len(cleaned["datasets"]["big"]) == 1000
    assert cleaned["datasets"]["name"] == "x"


def test_render_chart_png_empty_stdout(monkeypatch) -> None:
    """Подпроцесс завершился с кодом 0, но с пустым stdout."""
    import subprocess

    from app.core.export.charts import render_chart_png

    class DummyProc:
        returncode = 0
        stdout = b""
        stderr = b""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: DummyProc())
    assert render_chart_png({"mark": "bar"}) is None


def test_ensure_fonts_registration_corrupted_font(monkeypatch, tmp_path: Path) -> None:
    """Сбой регистрации повреждённого шрифта вызывает RuntimeError."""
    fake_font = tmp_path / "CorruptFont.ttf"
    fake_font.write_text("corrupted font content")

    monkeypatch.setattr("app.core.export.pdf.FONT_SEARCH_PATHS", [(str(fake_font), str(fake_font))])
    monkeypatch.setattr("app.core.export.pdf._FONTS_REGISTERED", False)

    from app.core.export.pdf import ensure_fonts_registered

    with pytest.raises(RuntimeError, match="Ошибка регистрации шрифта DejaVu"):
        ensure_fonts_registered()


def test_pdf_exporter_whitespace_question_fallback(tmp_path: Path) -> None:
    """Вопрос, состоящий только из пробелов и переводов строк."""
    pdf_path = tmp_path / "whitespace_q.pdf"
    PdfExporter().export_answer(
        path=str(pdf_path),
        question="   \n   \n   ",
    )
    assert pdf_path.is_file()
    assert pdf_path.stat().st_size > 0


def test_pdf_exporter_zero_dimension_image(monkeypatch) -> None:
    """Обработка изображения с нулевыми размерами (orig_w <= 0 или orig_h <= 0)."""

    class DummyImg:
        size = (0, 100)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("PIL.Image.open", lambda *args, **kwargs: DummyImg())
    img = PdfExporter()._build_image(b"fake_bytes", 500, 300)
    assert img is None


def test_branch_coverage_remaining_branches(tmp_path: Path) -> None:
    """100% ветвевое покрытие для charts.py, excel.py и pdf.py."""
    from app.core.export.charts import normalize_spec
    from app.core.export.excel import ExcelExporter

    # 1. charts.py: normalize_spec с фиксированной шириной (ветка 98->101)
    norm = normalize_spec({"width": 500, "height": 300})
    assert norm["width"] == 500

    # 2. excel.py: экспорт с пустым списком records (ветка 19->29)
    xlsx_path = tmp_path / "empty.xlsx"
    ExcelExporter().export([], str(xlsx_path))
    assert xlsx_path.is_file()

    # 3. pdf.py: только вопрос без инсайта (ветка 327->355)
    p1 = tmp_path / "q_only.pdf"
    PdfExporter().export_answer(str(p1), question="Только вопрос")
    assert p1.is_file()

    # 4. pdf.py: битое изображение графика (ветка 361->365)
    p2 = tmp_path / "broken_img.pdf"
    PdfExporter().export_answer(str(p2), question="Вопрос", chart_png=b"not_an_image")
    assert p2.is_file()

    # 5. pdf.py: список пустых записей без колонок (ветка 395->398)
    p3 = tmp_path / "empty_records.pdf"
    PdfExporter().export_answer(str(p3), records=[{}, {}])
    assert p3.is_file()


def test_render_chart_png_skips_when_all_render_slots_are_busy(monkeypatch) -> None:
    from app.core.export import charts

    class BusySlots:
        def acquire(self, timeout: float) -> bool:
            return False

    monkeypatch.setattr(charts, "_render_slots", BusySlots())
    assert charts.render_chart_png({"mark": "bar"}) is None


def test_api_export_xlsx_of_a_dashboard_tile(client: TestClient) -> None:
    from app.core.dashboards.models import Dashboard, DashboardTile
    from app.core.dashboards.store import DashboardStore

    db: DuckDBManager = client.app.dependency_overrides[get_db]()
    db.execute("CREATE OR REPLACE TABLE temp_result_v61 AS SELECT 1 AS id, 'a' AS name")
    dashboard_id = DashboardStore().create(
        Dashboard(
            session_id="dashboard_session_gone",
            title="Дашборд",
            tiles=[DashboardTile(title="Плитка", result_table="temp_result_v61")],
        )
    )

    owned = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "dashboard_session_gone",
            "format": "xlsx",
            "result_ref": "temp_result_v61",
            "dashboard_id": dashboard_id,
        },
    )
    assert owned.status_code == 200

    foreign = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "dashboard_session_gone",
            "format": "xlsx",
            "result_ref": "temp_result_v62",
            "dashboard_id": dashboard_id,
        },
    )
    assert foreign.status_code == 404

    missing = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "dashboard_session_gone",
            "format": "xlsx",
            "result_ref": "temp_result_v61",
            "dashboard_id": "no_such_dashboard",
        },
    )
    assert missing.status_code == 404


def test_export_answer_as_table_single_page(tmp_path: Path) -> None:
    """Табличный ответ должен рендериться на 1-й странице и не занимать 2-ю страницу."""
    pdf_path = str(tmp_path / "table_answer.pdf")
    records = [{"ID": i, "Название": f"Позиция {i}", "Сумма": i * 100} for i in range(15)]
    exporter = PdfExporter()
    out = exporter.export_answer(
        path=pdf_path,
        records=records,
        question="Список товаров",
        headline="Топ позиций",
        details=["Всего 15 строк"],
        title="Таблица позиций",
        is_table=True,
    )
    assert out == pdf_path
    assert Path(pdf_path).exists()

    reader = pypdf.PdfReader(pdf_path)
    assert len(reader.pages) == 1, "Табличный ответ должен строго помещаться на одной странице"
    text = reader.pages[0].extract_text()
    assert "Таблица позиций" in text
    assert "Данные таблицы" in text
    assert "Визуализация данных" not in text
    assert "Таблица с детальными показателями приведена в приложении ниже" not in text
    assert "Позиция 0" in text
    assert "Позиция 14" in text


def test_export_answer_as_table_large_table_does_not_spill_to_page_2(tmp_path: Path) -> None:
    """Большая таблица с длинными текстами и подробным инсайтом динамически сжимается и не вылезает на 2 страницу."""
    pdf_path = str(tmp_path / "large_table_answer.pdf")
    records = [
        {
            "Код": f"SKU-{i:04d}",
            "Описание": f"Длинное подробное описание товара категории {i} со множеством дополнительных характеристик для проверки высоты строки",
            "Выручка, ₽": 100000.0 + i * 500,
            "Статус": "В наличии на складе в Москве",
        }
        for i in range(50)
    ]
    exporter = PdfExporter()
    out = exporter.export_answer(
        path=pdf_path,
        records=records,
        question="Длинный вопрос исследования:\nКаковы основные показатели по всем товарным позициям на текущую дату?",
        headline="Ключевой аналитический вывод по товарным остаткам",
        details=[
            "Первая деталь: выручка стабильно растёт во всех филиалах",
            "Вторая деталь: наличие запасов на складах превышает норматив",
            "Третья деталь: динамика заказов положительная",
        ],
        title="Отчёт по товарным остаткам",
        is_table=True,
    )
    assert out == pdf_path
    reader = pypdf.PdfReader(pdf_path)
    assert len(reader.pages) == 1, (
        "Даже большая таблица с длинным текстом не должна вылезать на страницу 2"
    )


def test_export_answer_as_table_empty_row_dict(tmp_path: Path) -> None:
    """Таблица без колонок обрабатывается корректно."""
    pdf_path = str(tmp_path / "empty_cols.pdf")
    exporter = PdfExporter()
    out = exporter.export_answer(
        path=pdf_path,
        records=[{}],
        is_table=True,
    )
    assert out == pdf_path
    assert Path(pdf_path).exists()


def test_is_table_spec_text_mark_and_transforms() -> None:
    """Проверка определения табличных спецификаций с mark: text и transform."""
    from app.core.export.charts import is_table_spec

    assert is_table_spec({"mark": "text", "transform": [{"fold": ["a", "b"]}]}) is True
    assert (
        is_table_spec({"mark": {"type": "text"}, "transform": [{"window": [{"op": "row_number"}]}]})
        is True
    )
    assert is_table_spec({"mark": "text", "transform": [{"filter": "datum.x > 0"}]}) is False
    assert is_table_spec({"mark": "text"}) is False
    assert is_table_spec({"mark": {"type": "bar"}}) is False
    assert is_table_spec({"usermeta": {"chart_type": "grid"}}) is True
    assert is_table_spec({"usermeta": {"requested_type": "dataframe"}}) is True


def test_api_export_pdf_of_table_spec_single_page(client: TestClient) -> None:
    """Экспорт плитки-таблицы через API возвращает PDF с 1 страницей без картинки графика."""
    db: DuckDBManager = client.app.dependency_overrides[get_db]()
    memory = SessionMemory("test_table_export_session", db)
    memory.set_table("raw")

    spec = {
        "usermeta": {"chart_type": "table"},
        "data": {"values": [{"Категория": f"Кат {i}", "Значение": i * 100} for i in range(10)]},
    }
    resp = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "test_table_export_session",
            "format": "pdf",
            "title": "Таблица исследования",
            "headline": "Ключевые показатели",
            "spec": spec,
        },
    )
    assert resp.status_code == 200
    reader = pypdf.PdfReader(io.BytesIO(resp.content))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert "Таблица исследования" in text
    assert "Данные таблицы" in text
    assert "Визуализация данных" not in text


def test_export_answer_chart_render_failed_notice(tmp_path: Path) -> None:
    """Проверка вывода предупреждения, когда chart_render_failed=True."""
    pdf_path = str(tmp_path / "chart_failed.pdf")
    exporter = PdfExporter()
    out = exporter.export_answer(
        path=pdf_path,
        records=[{"x": 1, "y": 2}],
        chart_render_failed=True,
    )
    assert out == pdf_path
    reader = pypdf.PdfReader(pdf_path)
    text = "".join(p.extract_text() or "" for p in reader.pages)
    assert "График не удалось отобразить в PDF (ошибка рендеринга визуализации)" in text


def test_api_export_overwrites_stale_spec_values(client: TestClient, monkeypatch) -> None:
    """Проверка безусловной синхронизации данных: свежие records из result_ref перезаписывают stale spec.data.values."""
    db: DuckDBManager = client.app.dependency_overrides[get_db]()
    db.execute("CREATE TABLE temp_result_v99 AS SELECT 'Свежие_данные' AS title, 999 AS val")
    memory = SessionMemory("sync_spec_session", db)
    memory.set_table("raw")
    memory.start_turn("Какая выручка?")
    memory.finish_turn(
        "ok",
        result_table="temp_result_v99",
        result_columns=["title", "val"],
    )

    rendered_specs: list[dict] = []

    def mock_render(spec, **kwargs):
        rendered_specs.append(spec)
        return b"fake_png"

    monkeypatch.setattr("app.api.routes.artifacts.render_chart_png", mock_render)

    stale_spec = {
        "mark": "bar",
        "data": {"values": [{"title": "Устаревшие_данные", "val": 1}]},
    }

    resp = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "sync_spec_session",
            "format": "pdf",
            "result_ref": "temp_result_v99",
            "spec": stale_spec,
        },
    )
    assert resp.status_code == 200
    assert len(rendered_specs) == 1
    # Проверяем, что в рендеринг ушли свежие данные, а не старые
    assert rendered_specs[0]["data"]["values"] == [{"title": "Свежие_данные", "val": 999}]


def test_api_export_chart_render_failure_adds_warning_in_pdf(
    client: TestClient, monkeypatch
) -> None:
    """Если render_chart_png возвращает None, экспорт в PDF включает уведомление о сбое графика."""
    db: DuckDBManager = client.app.dependency_overrides[get_db]()
    db.execute("CREATE TABLE temp_result_v88 AS SELECT 'Данные' AS col, 123 AS num")
    memory = SessionMemory("render_fail_session", db)
    memory.set_table("raw")
    memory.start_turn("Покажи данные")
    memory.finish_turn(
        "ok",
        result_table="temp_result_v88",
        result_columns=["col", "num"],
    )

    monkeypatch.setattr("app.api.routes.artifacts.render_chart_png", lambda *args, **kwargs: None)

    resp = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "render_fail_session",
            "format": "pdf",
            "result_ref": "temp_result_v88",
            "spec": {"mark": "line"},
        },
    )
    assert resp.status_code == 200
    reader = pypdf.PdfReader(io.BytesIO(resp.content))
    text = "".join(p.extract_text() or "" for p in reader.pages)
    assert "График не удалось отобразить в PDF (ошибка рендеринга визуализации)" in text


def test_api_export_heatmap_unpivots_wide_matrix(client: TestClient, monkeypatch) -> None:
    """Проверка того, что экспорт артефакта heatmap с широкой матрицей пересобирает спеку в unpivoted ячейки."""
    db: DuckDBManager = client.app.dependency_overrides[get_db]()
    db.execute(
        "CREATE TABLE temp_result_v77 AS SELECT 1.0 AS a, -0.2 AS b UNION ALL SELECT -0.2 AS a, 1.0 AS b"
    )
    memory = SessionMemory("heatmap_export_session", db)
    memory.set_table("raw")
    memory.start_turn("Построй корреляции")
    memory.finish_turn(
        "ok",
        result_table="temp_result_v77",
        result_columns=["a", "b"],
    )

    rendered_specs: list[dict] = []

    def mock_render(spec, **kwargs):
        rendered_specs.append(spec)
        return b"fake_png"

    monkeypatch.setattr("app.api.routes.artifacts.render_chart_png", mock_render)

    resp = client.post(
        "/api/artifacts/export",
        json={
            "session_id": "heatmap_export_session",
            "format": "pdf",
            "result_ref": "temp_result_v77",
            "spec": {"usermeta": {"chart_type": "heatmap"}},
        },
    )
    assert resp.status_code == 200
    assert len(rendered_specs) == 1
    # Спека должна содержать 4 unpivoted ячейки x, y, value
    assert len(rendered_specs[0]["data"]["values"]) == 4
    assert rendered_specs[0]["encoding"]["x"]["field"] == "x"
    assert rendered_specs[0]["encoding"]["y"]["field"] == "y"


def test_is_table_spec_extended_detection() -> None:
    """Проверка расширенного определения табличных спецификаций."""
    from app.core.export.charts import is_table_spec, render_chart_png

    assert is_table_spec({"chart_type": "table"}) is True
    assert is_table_spec({"type": "table"}) is True
    assert is_table_spec({"kind": "dataframe"}) is True
    assert is_table_spec({"meta": {"chart_type": "grid"}}) is True
    assert is_table_spec({"encoding": {"x": {"field": "__col"}, "y": {"field": "__row"}}}) is True
    assert is_table_spec({"encoding": {"text": {"field": "__val"}}}) is True
    assert (
        is_table_spec(
            {
                "mark": "text",
                "encoding": {
                    "x": {"field": "col"},
                    "y": {"field": "row"},
                    "text": {"field": "val"},
                },
            }
        )
        is True
    )
    # Обычный mark: text без кодирования не считается таблицей (KPI или одиночный текст)
    assert is_table_spec({"mark": "text"}) is False
    assert is_table_spec({"mark": "bar"}) is False
    assert is_table_spec(None) is False
    assert is_table_spec({}) is False

    # render_chart_png не должен рендерить табличные спеки в растр
    assert render_chart_png({"chart_type": "table"}) is None
    assert render_chart_png({"encoding": {"x": {"field": "__col"}}}) is None


def test_normalize_spec_x_axis_auto_rotation() -> None:
    """Если подписи по оси X накладываются, они должны быть повернуты под -45 градусов."""
    from app.core.export.charts import normalize_spec

    # 1. Длинные перекрывающиеся категории -> labelAngle: -45
    spec_dense = {
        "mark": "bar",
        "data": {
            "values": [
                {"month": "Январь 2024", "v": 10},
                {"month": "Февраль 2024", "v": 20},
                {"month": "Март 2024", "v": 30},
                {"month": "Апрель 2024", "v": 40},
                {"month": "Май 2024", "v": 50},
                {"month": "Июнь 2024", "v": 60},
                {"month": "Июль 2024", "v": 70},
                {"month": "Август 2024", "v": 80},
                {"month": "Сентябрь 2024", "v": 90},
                {"month": "Октябрь 2024", "v": 100},
                {"month": "Ноябрь 2024", "v": 110},
                {"month": "Декабрь 2024", "v": 120},
            ]
        },
        "encoding": {
            "x": {"field": "month", "type": "nominal"},
            "y": {"field": "v", "type": "quantitative"},
        },
    }
    norm_dense = normalize_spec(spec_dense, width=640)
    assert norm_dense["encoding"]["x"]["axis"]["labelAngle"] == -45

    # 2. Небольшое количество коротких категорий -> labelAngle не выставляется
    spec_sparse = {
        "mark": "bar",
        "data": {
            "values": [
                {"cat": "A", "v": 10},
                {"cat": "B", "v": 20},
                {"cat": "C", "v": 30},
            ]
        },
        "encoding": {
            "x": {"field": "cat", "type": "nominal"},
            "y": {"field": "v", "type": "quantitative"},
        },
    }
    norm_sparse = normalize_spec(spec_sparse, width=640)
    axis = norm_sparse["encoding"]["x"].get("axis", {})
    assert axis.get("labelAngle") in (None, 0)

    # 3. Уже заданный ненулевой угол не перезаписывается
    spec_custom = {
        "mark": "bar",
        "data": spec_dense["data"],
        "encoding": {
            "x": {"field": "month", "type": "nominal", "axis": {"labelAngle": 90}},
            "y": {"field": "v", "type": "quantitative"},
        },
    }
    norm_custom = normalize_spec(spec_custom, width=640)
    assert norm_custom["encoding"]["x"]["axis"]["labelAngle"] == 90

    # 4. Числовая ось без явных меток не считается перекрывающейся
    spec_quant = {
        "mark": "point",
        "data": {"values": [{"x": float(i), "y": float(i * 2)} for i in range(50)]},
        "encoding": {
            "x": {"field": "x", "type": "quantitative"},
            "y": {"field": "y", "type": "quantitative"},
        },
    }
    norm_quant = normalize_spec(spec_quant, width=640)
    assert norm_quant["encoding"]["x"].get("axis", {}).get("labelAngle") in (None, 0)

    # 5. Метки из scale.domain
    spec_domain = {
        "mark": "bar",
        "encoding": {
            "x": {
                "field": "city",
                "type": "nominal",
                "scale": {
                    "domain": [
                        "Санкт-Петербург",
                        "Нижний Новгород",
                        "Екатеринбург",
                        "Новосибирск",
                        "Владивосток",
                        "Ростов-на-Дону",
                        "Красноярск",
                        "Калининград",
                    ]
                },
            },
            "y": {"field": "val", "type": "quantitative"},
        },
    }
    norm_domain = normalize_spec(spec_domain, width=640)
    assert norm_domain["encoding"]["x"]["axis"]["labelAngle"] == -45
