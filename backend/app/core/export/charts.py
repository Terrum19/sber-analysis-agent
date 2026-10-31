"""Рендеринг графиков Vega-Lite в растровый формат PNG через vl-convert-python.

Работает локально в замкнутом контуре (без Node.js, Chromium и сети).
"""

from __future__ import annotations

import copy
import json
import logging
import subprocess
import sys
import threading
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_WIDTH = 640
DEFAULT_HEIGHT = 360
DEFAULT_SCALE = 2.0
MAX_SPEC_JSON_BYTES = 256 * 1024  # 256 KB
MAX_DATA_ROWS = 1000
RENDER_TIMEOUT_S = 7.0
MAX_PARALLEL_RENDERS = 2

_render_slots = threading.BoundedSemaphore(MAX_PARALLEL_RENDERS)

_RENDER_SUBPROCESS_SCRIPT = """
import sys, json
try:
    import vl_convert as vlc
except ImportError:
    sys.exit(2)

try:
    payload = json.loads(sys.stdin.read())
    spec = payload["spec"]
    scale = payload.get("scale", 2.0)
    png_bytes = vlc.vegalite_to_png(vl_spec=spec, scale=scale, allowed_base_urls=[])
    sys.stdout.buffer.write(png_bytes)
except Exception as exc:
    sys.stderr.write(str(exc))
    sys.exit(1)
"""


def is_table_spec(spec: dict[str, Any] | None) -> bool:
    """Определить, представляет ли спецификация табличный вид вместо графической визуализации."""
    if not spec or not isinstance(spec, dict):
        return False

    table_type_names = {"table", "grid", "dataframe", "data_table", "tabular", "raw_table"}

    # 1. Проверка явного типа графика в корне спеки или в секциях метаданных
    candidate_types: list[Any] = [
        spec.get("chart_type"),
        spec.get("requested_type"),
        spec.get("type"),
        spec.get("kind"),
    ]
    for meta_key in ("usermeta", "meta", "params"):
        meta_val = spec.get(meta_key)
        if isinstance(meta_val, dict):
            candidate_types.extend(
                [
                    meta_val.get("chart_type"),
                    meta_val.get("requested_type"),
                    meta_val.get("type"),
                    meta_val.get("kind"),
                ]
            )

    for c_type in candidate_types:
        if c_type and str(c_type).strip().lower() in table_type_names:
            return True

    # 2. Проверка служебных полей табличного кодирования vega_spec_builder (__col, __row, __val)
    encoding = spec.get("encoding")
    if isinstance(encoding, dict):
        x_field = (
            encoding.get("x", {}).get("field") if isinstance(encoding.get("x"), dict) else None
        )
        y_field = (
            encoding.get("y", {}).get("field") if isinstance(encoding.get("y"), dict) else None
        )
        text_field = (
            encoding.get("text", {}).get("field")
            if isinstance(encoding.get("text"), dict)
            else None
        )
        if x_field == "__col" or y_field == "__row" or text_field == "__val":
            return True

    # 3. Проверка mark: text
    mark = spec.get("mark")
    mark_type = mark.get("type") if isinstance(mark, dict) else mark
    if mark_type == "text":
        transforms = spec.get("transform", [])
        if isinstance(transforms, list) and any(
            isinstance(t, dict) and ("fold" in t or "window" in t) for t in transforms
        ):
            return True
        if (
            isinstance(encoding, dict)
            and "text" in encoding
            and ("x" in encoding and "y" in encoding)
        ):
            return True

    return False


def validate_chart_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Проверить спецификацию графика на безопасность и ограничения ресурсов.

    Защищает сервер от OOM и DoS-атак:
    - Запрещены генераторы данных (data.sequence, data.graticule, data.sphere),
      способные сгенерировать миллионы точек и уронить V8 по OOM.
    - Запрещены внешние URL (data.url) для предотвращения SSRF.
    - Ограничен суммарный размер JSON (до 256 КБ).
    - Ограничено количество инлайн-строк в data.values (до 1000).
    """
    if not isinstance(spec, dict):
        raise TypeError("Спецификация графика должна быть объектом dict")

    try:
        raw_json = json.dumps(spec)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Спецификация графика не сериализуется в JSON: {exc}") from exc

    if len(raw_json.encode("utf-8")) > MAX_SPEC_JSON_BYTES:
        raise ValueError(f"Размер спецификации графика превышает лимит {MAX_SPEC_JSON_BYTES} байт")

    clean_spec = copy.deepcopy(spec)

    def _check_data(data: dict[str, Any]) -> None:
        if "sequence" in data:
            raise ValueError("Генератор данных data.sequence запрещён в целях безопасности")
        if "url" in data:
            raise ValueError("Внешние источники данных data.url запрещены в целях безопасности")
        if "graticule" in data or "sphere" in data:
            raise ValueError("Генераторы данных запрещены в целях безопасности")

    def _scan_node(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in list(node.items()):
                if key == "data" and isinstance(value, dict):
                    _check_data(value)
                if key == "values" and isinstance(value, list):
                    node["values"] = value[:MAX_DATA_ROWS]
                elif key == "datasets" and isinstance(value, dict):
                    node["datasets"] = {
                        name: rows[:MAX_DATA_ROWS] if isinstance(rows, list) else rows
                        for name, rows in value.items()
                    }
                else:
                    _scan_node(value)
        elif isinstance(node, list):
            for item in node:
                _scan_node(item)

    _scan_node(clean_spec)
    return clean_spec


def _detect_x_axis_overlap(
    x_encoding: dict[str, Any],
    data_values: list[dict[str, Any]] | None,
    plot_width: float,
) -> bool:
    """Определить, накладываются ли подписи на оси X при горизонтальном размещении."""
    if not isinstance(x_encoding, dict):
        return False

    axis = x_encoding.get("axis")
    if isinstance(axis, dict):
        current_angle = axis.get("labelAngle")
        if current_angle not in (None, 0):
            return False

    # Для непрерывных числовых шкал без явного списка значений Vega-Lite сама генерирует деления
    x_type = str(x_encoding.get("type") or "").lower()
    has_explicit_axis_values = isinstance(axis, dict) and isinstance(axis.get("values"), list)
    if x_type == "quantitative" and not has_explicit_axis_values:
        return False

    distinct_labels: list[str] = []
    seen: set[str] = set()

    # 1. Попытка извлечь метки из scale.domain
    scale = x_encoding.get("scale")
    if isinstance(scale, dict) and isinstance(scale.get("domain"), list):
        for item in scale["domain"]:
            if item is not None:
                s = str(item)
                if s not in seen:
                    seen.add(s)
                    distinct_labels.append(s)

    # 2. Попытка извлечь метки из axis.values
    if not distinct_labels and isinstance(axis, dict) and isinstance(axis.get("values"), list):
        for item in axis["values"]:
            if item is not None:
                s = str(item)
                if s not in seen:
                    seen.add(s)
                    distinct_labels.append(s)

    # 3. Извлечь уникальные значения из data_values по полю field
    field_name = x_encoding.get("field")
    if not distinct_labels and field_name and isinstance(data_values, list):
        for row in data_values:
            if isinstance(row, dict) and field_name in row:
                val = row[field_name]
                if val is not None:
                    s = str(val)
                    if s not in seen:
                        seen.add(s)
                        distinct_labels.append(s)

    n = len(distinct_labels)
    if n <= 1:
        return False

    slot_width = plot_width / n
    max_len = max(len(s) for s in distinct_labels)
    avg_len = sum(len(s) for s in distinct_labels) / n

    needed_max = max_len * 6.5 + 8.0
    needed_avg = avg_len * 6.0 + 6.0

    return bool(needed_max > slot_width or needed_avg > slot_width)


def _auto_rotate_x_axis(spec: dict[str, Any], default_width: int = DEFAULT_WIDTH) -> None:
    """Проверить наложение подписей на оси X и повернуть их под -45 градусов при необходимости."""
    root_data = spec.get("data")
    root_values = root_data.get("values") if isinstance(root_data, dict) else None

    def _process_view(view: dict[str, Any], parent_values: list[dict[str, Any]] | None) -> None:
        if not isinstance(view, dict):
            return

        view_data = view.get("data")
        view_values = view_data.get("values") if isinstance(view_data, dict) else parent_values

        encoding = view.get("encoding")
        if isinstance(encoding, dict) and "x" in encoding and isinstance(encoding["x"], dict):
            x_enc = encoding["x"]
            view_width = view.get("width")
            w = (
                float(view_width)
                if isinstance(view_width, (int, float)) and view_width > 0
                else float(default_width)
            )
            plot_width = max(100.0, w - 90.0)
            if _detect_x_axis_overlap(
                x_enc, view_values if isinstance(view_values, list) else None, plot_width
            ):
                axis = x_enc.setdefault("axis", {})
                if isinstance(axis, dict):
                    axis["labelAngle"] = -45

        for sub_key in ("layer", "concat", "vconcat", "hconcat"):
            sub_views = view.get(sub_key)
            if isinstance(sub_views, list):
                for sub in sub_views:
                    if isinstance(sub, dict):
                        _process_view(sub, view_values)
        if "spec" in view and isinstance(view["spec"], dict):
            _process_view(view["spec"], view_values)

    _process_view(spec, root_values if isinstance(root_values, list) else None)


def normalize_spec(spec: dict[str, Any], width: int = DEFAULT_WIDTH) -> dict[str, Any]:
    """Адаптировать спецификацию Vega-Lite для автономного рендеринга.

    Убирает динамические размеры браузерного контейнера ("container"),
    так как vl-convert работает вне DOM-окружения.
    Также удаляет встроенный заголовок графика (spec["title"]),
    поскольку заголовок графика/плитки выводится средствами документа (PDF).
    """
    normalized = copy.deepcopy(spec)
    normalized.pop("title", None)
    for concat_key in ("concat", "vconcat", "hconcat", "layer"):
        if concat_key in normalized and isinstance(normalized[concat_key], list):
            for subview in normalized[concat_key]:
                if isinstance(subview, dict):
                    subview.pop("title", None)
    if "spec" in normalized and isinstance(normalized["spec"], dict):
        normalized["spec"].pop("title", None)

    is_concat = any(k in normalized for k in ("concat", "vconcat", "hconcat"))
    if normalized.get("width") == "container" or ("width" not in normalized and not is_concat):
        normalized["width"] = width

    if normalized.get("height") == "container":
        normalized["height"] = DEFAULT_HEIGHT

    autosize = normalized.get("autosize")
    if isinstance(autosize, dict) and autosize.get("type") in ("fit", "fit-x", "fit-y"):
        normalized["autosize"] = {"type": "pad", "contains": "padding"}
    elif autosize in ("fit", "fit-x", "fit-y"):
        normalized["autosize"] = "pad"

    _auto_rotate_x_axis(normalized, default_width=width)

    return normalized


def render_chart_png(
    spec: dict[str, Any] | None,
    width: int = DEFAULT_WIDTH,
    scale: float = DEFAULT_SCALE,
    timeout_s: float = RENDER_TIMEOUT_S,
) -> bytes | None:
    """Сконвертировать Vega-Lite спецификацию в изображение PNG.

    Выполняет рендеринг в изолированном подпроцессе с лимитом времени и
    передачей allowed_base_urls=[], гарантируя, что фатальный OOM в V8
    или сетевые запросы не уронят основной процесс бэкенда.

    Args:
        spec: Vega-Lite словарь спецификации.
        width: Фиксированная ширина графика (по умолчанию 640).
        scale: Масштаб растеризации для высокого разрешения (Retina/HiDPI, по умолчанию 2.0).
        timeout_s: Таймаут рендеринга в секундах (по умолчанию 7.0).

    Returns:
        Байты PNG изображения или None в случае ошибки/отсутствия спецификации.
    """
    if not spec or not isinstance(spec, dict) or is_table_spec(spec):
        return None

    try:
        validated = validate_chart_spec(spec)
        normalized = normalize_spec(validated, width=width)
    except (ValueError, TypeError) as exc:
        logger.warning("Спецификация графика отклонена валидатором: %s", exc)
        return None

    try:
        import vl_convert  # noqa: F401
    except ImportError:
        logger.warning("Пакет vl-convert-python не установлен, рендеринг графика пропущен.")
        return None

    input_payload = json.dumps({"spec": normalized, "scale": scale}).encode("utf-8")

    if not _render_slots.acquire(timeout=timeout_s):
        logger.warning("Рендеринг графика пропущен: заняты все %d слота", MAX_PARALLEL_RENDERS)
        return None
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _RENDER_SUBPROCESS_SCRIPT],
            input=input_payload,
            capture_output=True,
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Рендеринг графика прерван по таймауту (%s с)", timeout_s)
        return None
    except OSError as exc:
        logger.warning("Ошибка запуска подпроцесса рендеринга графика: %s", exc)
        return None
    finally:
        _render_slots.release()

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        logger.warning(
            "Подпроцесс рендеринга графика завершился с кодом %d: %s", proc.returncode, err
        )
        return None

    if proc.stdout:
        return bytes(proc.stdout)
    return None
