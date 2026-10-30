"""Очистка устаревших temp_result_vN.

Результаты шагов живут в DuckDB постоянными таблицами; без очистки файл
DuckDB растёт с каждым вопросом. Сохраняются таблицы плиток дашбордов,
последних графиков и чекпоинтов остановленных ходов сессий, активных за
сутки: такой ход можно продолжить. Остальные удаляются, когда старше
``settings.result_ttl_s``.
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
    """Таблицы, которые нельзя удалять: плитки, графики и чекпоинты ходов свежих сессий."""
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
    """Таблицы графиков и чекпоинтов ходов сессии (и last_chart состояния до списка ходов)."""
    try:
        state = json.loads(raw_state)
    except (TypeError, ValueError):
        return set()
    if not isinstance(state, dict):
        return set()
    histories = list((state.get("tables") or {}).values())
    histories.append(state)
    charts = [h.get("last_chart") for h in histories if isinstance(h, dict)]
    turns = [turn for turn in state.get("turns") or [] if isinstance(turn, dict)]
    charts += [turn.get("chart") for turn in turns]
    kept = {c["result_table"] for c in charts if isinstance(c, dict) and c.get("result_table")}
    for turn in turns:
        kept.update(_checkpoint_tables(turn.get("checkpoint")))
    return kept


def _checkpoint_tables(checkpoint: object) -> set[str]:
    """Таблицы шагов и готовых плиток из чекпоинта остановленного хода."""
    if not isinstance(checkpoint, dict):
        return set()
    tables = set((checkpoint.get("step_tables") or {}).values())
    tables.update(
        tile.get("result_table")
        for tile in (checkpoint.get("built") or {}).values()
        if isinstance(tile, dict)
    )
    return {table for table in tables if isinstance(table, str)}


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
