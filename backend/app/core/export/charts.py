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
    if not spec or not isinstance(spec, dict):
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
