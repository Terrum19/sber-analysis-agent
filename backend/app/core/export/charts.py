"""Рендеринг графиков Vega-Lite в растровый формат PNG через vl-convert-python.

Работает локально в замкнутом контуре (без Node.js, Chromium и сети).
"""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_WIDTH = 640
DEFAULT_HEIGHT = 360
DEFAULT_SCALE = 2.0


def normalize_spec(spec: dict[str, Any], width: int = DEFAULT_WIDTH) -> dict[str, Any]:
    """Адаптировать спецификацию Vega-Lite для автономного рендеринга.

    Убирает динамические размеры браузерного контейнера ("container"),
    так как vl-convert работает вне DOM-окружения.
    """
    normalized = copy.deepcopy(spec)

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
) -> bytes | None:
    """Сконвертировать Vega-Lite спецификацию в изображение PNG.

    Args:
        spec: Vega-Lite словарь спецификации.
        width: Фиксированная ширина графика (по умолчанию 640).
        scale: Масштаб растеризации для высокого разрешения (Retina/HiDPI, по умолчанию 2.0).

    Returns:
        Байты PNG изображения или None в случае ошибки/отсутствия спецификации.
    """
    if not spec or not isinstance(spec, dict):
        return None

    try:
        import vl_convert as vlc
    except ImportError:
        logger.warning("Пакет vl-convert-python не установлен, рендеринг графика пропущен.")
        return None

    try:
        normalized = normalize_spec(spec, width=width)
        png_bytes = vlc.vegalite_to_png(vl_spec=normalized, scale=scale)
        if isinstance(png_bytes, (bytes, bytearray)):
            return bytes(png_bytes)
        return None
    except Exception as error:  # noqa: BLE001
        logger.warning("Не удалось отрендерить график Vega-Lite в PNG: %s", error)
        return None
