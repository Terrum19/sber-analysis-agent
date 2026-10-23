"""Память уровня 1: краткосрочная память сессии.

Хранит историю текстового диалога и историю ResearchStep, привязанную
к session_id. Чтобы не взрывать контекст, в промпт идёт скользящее
окно последних 3-5 сообщений + сжатая выжимка предыдущих шагов.
Сессия привязана к таблице DuckDB (``set_table``); сообщения и последний
график хранятся отдельно для каждой таблицы. Метаданные сессии
(``title``, ``datasource_id``, ``created_at``) лежат в том же состоянии,
поэтому сессия переживает перезапуск приложения.

Состояние сессии хранится в SQLite (таблица ``session_state`` по адресу
``settings.sqlite_path``) вместо отдельных JSON-файлов.
"""

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.profile_memory import ProfileMemory


def _connect(path: Path) -> sqlite3.Connection:
    """Открыть SQLite сессий, создав таблицу ``session_state`` при первом обращении."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_state (
            session_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            updated_at REAL
        )
        """
    )
    conn.commit()
    return conn


class SessionMemory:
    """Память одной сессии диалога (SQLite на диск)."""

    def __init__(self, session_id: str, db: DuckDBManager | None = None) -> None:
        self.session_id = session_id
        self._db_path = Path(settings.sqlite_path)
        self.db = db
        self._state = self._load()

    @staticmethod
    def list_sessions(limit: int, offset: int = 0) -> list[dict]:
        """Метаданные сохранённых сессий одним запросом, недавно изменённые первыми.

        Состояние целиком (с историей сообщений) не читается.
        """
        conn = _connect(Path(settings.sqlite_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT session_id,
                       json_extract(state, '$.datasource') AS "table",
                       json_extract(state, '$.datasource_id') AS datasource_id,
                       json_extract(state, '$.title') AS title,
                       json_extract(state, '$.created_at') AS created_at
                FROM session_state
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        return _connect(self._db_path)

    def _load(self) -> dict:
        """Загрузить состояние сессии из SQLite."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT state FROM session_state WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is not None:
            return json.loads(row[0])
        return {
            "steps": [],
            "datasource": None,
            "columns": [],
            "tables": {},
            "pending_recovery": None,
        }

    def exists(self) -> bool:
        """Сохранена ли сессия в SQLite."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM session_state WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()
        finally:
            conn.close()
        return row is not None

    def _persist(self) -> None:
        """Сохранить состояние сессии в SQLite."""
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO session_state (session_id, state, updated_at)
                VALUES (?, ?, ?)
                """,
                (self.session_id, json.dumps(self._state, ensure_ascii=False), time.time()),
            )
            conn.commit()
        finally:
            conn.close()

    @property
    def table(self) -> str | None:
        """Таблица, к которой привязана сессия (None — данные не подключены)."""
        return self._state.get("datasource")

    @property
    def title(self) -> str | None:
        """Название сессии для сайдбара."""
        return self._state.get("title")

    @property
    def datasource_id(self) -> str | None:
        """id подключения из DatasourceOut, как его передал клиент."""
        return self._state.get("datasource_id")

    @property
    def created_at(self) -> str | None:
        """Время создания сессии (ISO, UTC); None у сессий, созданных до его записи."""
        return self._state.get("created_at")

    def set_meta(self, title: str | None, datasource_id: str | None) -> None:
        """Запомнить название и источник сессии; время создания ставится один раз."""
        self._state["title"] = title
        self._state["datasource_id"] = datasource_id
        self._state.setdefault("created_at", datetime.now(UTC).isoformat())
        self._persist()

    def set_table(self, table: str, columns: list[str]) -> None:
        """Привязать сессию к таблице и её колонкам.

        Сообщения и последний график хранятся отдельно для каждой таблицы
        и при возврате к ней снова доступны. Шаги и незавершённое уточнение
        относятся к плану по прошлой таблице и сбрасываются. При смене
        таблицы сбрасывается и ``datasource_id``: новая таблица может быть
        из другого источника. Та же таблица обновляет только колонки: шаги
        и незавершённое уточнение остаются.
        """
        if table == self.table:
            self._state["columns"] = list(columns)
            self._persist()
            return
        if self.table is not None:
            self._state["datasource_id"] = None
        self._state["datasource"] = table
        self._state["columns"] = list(columns)
        self._state["steps"] = []
        self._state["pending_recovery"] = None
        self._history()
        self._persist()

    def _history(self) -> dict:
        """Сообщения и последний график текущей таблицы.

        Без таблицы история не ведётся: возвращается пустая запись,
        которая не сохраняется.
        """
        empty = {"messages": [], "last_chart": None}
        if self.table is None:
            return empty
        return self._state.setdefault("tables", {}).setdefault(self.table, empty)

    def build_context(self, question: str) -> dict:
        """Собрать контекст: скользящее окно сообщений + выжимка шагов + колонки + словарь."""
        window = self._history()["messages"][-5:]
        summary = "; ".join(f"шаг {s['step_id']}: {s['purpose']}" for s in self._state["steps"])
        columns = self._state.get("columns", [])
        datasource = self.table

        # Профиль данных (паспорт таблицы) для долгосрочного контекста;
        # без DuckDB (память открыта только ради истории) профиля нет.
        profile = None
        enrichment = []
        if self.db is not None and datasource is not None:
            passport = ProfileMemory(self.db)
            profile = passport.load_profile(datasource)
            enrichment = passport.load_enrichment(datasource)
        # Колонки не задавались явно — берём их из паспорта таблицы.
        if not columns and profile is not None:
            columns = [column.name for column in profile.columns]

        # Формируем краткое описание колонок для LLM
        columns_summary = ", ".join(columns) if columns else "неизвестно"

        return {
            "messages": window,
            "summary": summary,
            "question": question,
            "columns": columns,
            "columns_summary": columns_summary,
            "datasource": datasource,
            "profile": profile,
            # Описание таблицы из паспорта: названия для отбора колонок, описания для промпта.
            "titles": {row.column: row.title for row in enrichment if row.title},
            "descriptions": {row.column: row.description for row in enrichment if row.description},
        }

    def record_step(self, step, result_table: str) -> None:
        """Запомнить выполненный шаг и его результат."""
        step_id = (
            getattr(step, "step_id", None)
            if hasattr(step, "step_id")
            else (step.get("step_id") if isinstance(step, dict) else len(self._state["steps"]) + 1)
        )
        purpose = (
            getattr(step, "purpose", "")
            if hasattr(step, "purpose")
            else (step.get("purpose", "") if isinstance(step, dict) else "")
        )
        self._state["steps"].append(
            {"step_id": step_id, "purpose": purpose, "result": result_table}
        )
        self._persist()

    def record_chart(self, chart_type: str, result_table: str, caption: str = "") -> None:
        """Запомнить последний показанный график текущей таблицы (для смены вида без LLM)."""
        self._history()["last_chart"] = {
            "chart_type": chart_type,
            "result_table": result_table,
            "caption": caption,
        }
        self._persist()

    def owns_result(self, result_table: str) -> bool:
        """Проверить, принадлежит ли промежуточная таблица данной сессии."""
        if not result_table:
            return False

        for step in self._state.get("steps", []):
            if step.get("result") == result_table:
                return True

        for tbl_data in self._state.get("tables", {}).values():
            chart = tbl_data.get("last_chart")
            if chart and chart.get("result_table") == result_table:
                return True

        pending = self._state.get("pending_recovery")
        if pending and isinstance(pending, dict):
            step_tables = pending.get("step_tables", {})
            if isinstance(step_tables, dict) and result_table in step_tables.values():
                return True
            if pending.get("input_table") == result_table:
                return True
        return False

    def last_chart(self) -> dict | None:
        """Последний показанный график текущей таблицы или None."""
        return self._history()["last_chart"]

    def record_answer(self, answer: str) -> None:
        """Запомнить ответ агента в истории текущей таблицы."""
        self._history()["messages"].append({"role": "agent", "content": answer})
        self._persist()

    def set_pending_recovery(self, data: dict) -> None:
        """Сохранить контекст неудачи шага для последующего уточнения."""
        self._state["pending_recovery"] = data
        self._persist()

    def get_pending_recovery(self) -> dict | None:
        """Вернуть сохранённое состояние ожидания уточнения или None."""
        return self._state.get("pending_recovery")

    def clear_pending_recovery(self) -> None:
        """Очистить состояние ожидания уточнения после успешного восстановления."""
        self._state["pending_recovery"] = None
        self._persist()
