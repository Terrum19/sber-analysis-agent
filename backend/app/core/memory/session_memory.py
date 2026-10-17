"""Память уровня 1: краткосрочная память сессии.

Хранит историю текстового диалога и историю ResearchStep, привязанную
к session_id. Чтобы не взрывать контекст, в промпт идёт скользящее
окно последних 3-5 сообщений + сжатая выжимка предыдущих шагов.

Состояние сессии хранится в SQLite (таблица ``session_state`` по адресу
``settings.sqlite_path``) вместо отдельных JSON-файлов.
"""

import json
import sqlite3
import time
from pathlib import Path

from app.config import settings
from app.core.memory.profile_memory import ProfileMemory

class SessionMemory:
    """Память одной сессии диалога (SQLite на диск)."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._db_path = Path(settings.sqlite_path)
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
            "messages": [],
            "steps": [],
            "datasource": None,
            "columns": [],
            "pending_recovery": None,
        }

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

    def set_datasource(self, datasource_name: str, columns: list[str]) -> None:
        """Установить привязанный datasource и его колонки для контекста."""
        self._state["datasource"] = datasource_name
        self._state["columns"] = list(columns)

    def build_context(self, question: str) -> dict:
        """Собрать контекст: скользящее окно сообщений + выжимка шагов + колонки + словарь."""
        window = self._state["messages"][-5:]
        summary = "; ".join(f"шаг {s['step_id']}: {s['purpose']}" for s in self._state["steps"])
        columns = self._state.get("columns", [])
        datasource = self._state.get("datasource")

        # Формируем краткое описание колонок для LLM
        columns_summary = ", ".join(columns) if columns else "неизвестно"

        # Профиль данных (паспорт таблицы) для долгосрочного контекста
        profile = ProfileMemory().load(datasource or "raw")

        return {
            "messages": window,
            "summary": summary,
            "question": question,
            "columns": columns,
            "columns_summary": columns_summary,
            "datasource": datasource or "raw",
            "profile": profile,
        }

    def record_step(self, step, result_table: str) -> None:
        """Запомнить выполненный шаг и его результат."""
        self._state["steps"].append(
            {"step_id": step.step_id, "purpose": step.purpose, "result": result_table}
        )

    def record_answer(self, answer: str) -> None:
        """Запомнить ответ агента."""
        self._state["messages"].append({"role": "agent", "content": answer})
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
