"""Память уровня 1: краткосрочная память сессии.

Хранит историю текстового диалога и историю ResearchStep, привязанную
к session_id. Чтобы не взрывать контекст, в промпт идёт скользящее
окно последних 3-5 сообщений + сжатая выжимка предыдущих шагов.
Сессия привязана к таблице DuckDB (``set_table``); сообщения и последний
график хранятся отдельно для каждой таблицы.

Состояние сессии хранится в SQLite (таблица ``session_state`` по адресу
``settings.sqlite_path``) вместо отдельных JSON-файлов.
"""

import json
import sqlite3
import time
from pathlib import Path

from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.profile_memory import ProfileMemory


class SessionMemory:
    """Память одной сессии диалога (SQLite на диск)."""

    def __init__(self, session_id: str, db: DuckDBManager | None = None) -> None:
        self.session_id = session_id
        self._db_path = Path(settings.sqlite_path)
        self.db = db
        self._state = self._load()

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
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

    def set_table(self, table: str, columns: list[str]) -> None:
        """Привязать сессию к таблице и её колонкам.

        Сообщения и последний график хранятся отдельно для каждой таблицы
        и при возврате к ней снова доступны. Шаги и незавершённое уточнение
        относятся к плану по прошлой таблице и сбрасываются.
        """
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
            # Обогащение паспорта: алиасы для отбора колонок, описания для промпта.
            "aliases": {row.column: row.aliases for row in enrichment if row.aliases},
            "descriptions": {row.column: row.description for row in enrichment if row.description},
        }

    def record_step(self, step, result_table: str) -> None:
        """Запомнить выполненный шаг и его результат."""
        self._state["steps"].append(
            {"step_id": step.step_id, "purpose": step.purpose, "result": result_table}
        )

    def record_chart(self, chart_type: str, result_table: str, caption: str = "") -> None:
        """Запомнить последний показанный график текущей таблицы (для смены вида без LLM)."""
        self._history()["last_chart"] = {
            "chart_type": chart_type,
            "result_table": result_table,
            "caption": caption,
        }

    def last_chart(self) -> dict | None:
        """Последний показанный график текущей таблицы или None."""
        return self._history()["last_chart"]

    def record_answer(self, answer: str) -> None:
        """Запомнить ответ агента в истории текущей таблицы."""
        self._history()["messages"].append({"role": "agent", "content": answer})
        self._persist()

    # --- pending_recovery (восстановление после ошибки шага) ---

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
