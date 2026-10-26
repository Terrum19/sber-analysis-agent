"""Очистка устаревших temp_result_vN.

Результаты шагов живут в DuckDB постоянными таблицами; без очистки файл
DuckDB растёт с каждым вопросом. Сохраняются таблицы плиток дашбордов и
последних графиков сессий, активных за сутки; остальные удаляются,
когда старше ``settings.result_ttl_s``.
"""

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path

from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager

logger = logging.getLogger(__name__)

SESSION_KEEP_S = 24 * 3600


def collect_keep(sqlite_path: str | None = None, session_keep_s: int = SESSION_KEEP_S) -> set[str]:
    """Таблицы, которые нельзя удалять: result_table плиток и last_chart свежих сессий."""
    path = Path(sqlite_path or settings.sqlite_path)
    if not path.exists():
        return set()
    keep: set[str] = set()
    connection = sqlite3.connect(str(path))
    try:
        existing = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "dashboard_tiles" in existing:
            keep.update(
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT result_table FROM dashboard_tiles"
                    " WHERE result_table IS NOT NULL"
                )
            )
        if "session_state" in existing:
            since = time.time() - session_keep_s
            for (raw_state,) in connection.execute(
                "SELECT state FROM session_state WHERE updated_at >= ?", (since,)
            ):
                keep.update(_session_charts(raw_state))
    finally:
        connection.close()
    return keep


def _session_charts(raw_state: str) -> set[str]:
    """result_table графиков ходов сессии (и last_chart состояния до списка ходов)."""
    try:
        state = json.loads(raw_state)
    except (TypeError, ValueError):
        return set()
    if not isinstance(state, dict):
        return set()
    histories = list((state.get("tables") or {}).values())
    histories.append(state)
    charts = [h.get("last_chart") for h in histories if isinstance(h, dict)]
    charts += [t.get("chart") for t in state.get("turns") or [] if isinstance(t, dict)]
    return {c["result_table"] for c in charts if isinstance(c, dict) and c.get("result_table")}


def cleanup(db: DuckDBManager) -> list[str]:
    """Один проход очистки; ошибки пишутся в лог и не пробрасываются."""
    try:
        dropped = db.cleanup_results(collect_keep(), settings.result_ttl_s)
    except Exception:
        logger.exception("Очистка temp_result_vN не удалась")
        return []
    if dropped:
        logger.info("Удалено устаревших результатов: %d", len(dropped))
    return dropped


async def cleanup_forever(db: DuckDBManager) -> None:
    """Фоновая очистка раз в result_ttl_s / 4 секунд."""
    while True:
        await asyncio.sleep(max(settings.result_ttl_s / 4, 1))
        await asyncio.to_thread(cleanup, db)
