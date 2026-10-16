"""Память уровня 1: краткосрочная память сессии.

Хранит историю текстового диалога и историю ResearchStep, привязанную
к session_id. Чтобы не взрывать контекст, в промпт идёт скользящее
окно последних 3-5 сообщений + сжатая выжимка предыдущих шагов.
"""

import json
from pathlib import Path

from app.config import settings
from app.core.memory.profile_memory import ProfileMemory


class SessionMemory:
    """Память одной сессии диалога (SQLite/файл на диск)."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._path = Path(settings.sqlite_path)
        self._state = self._load()

    def _load(self) -> dict:
        """Загрузить состояние сессии (TODO: SQLite вместо файла)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        file = self._path.with_suffix(f".{self.session_id}.json")
        return json.loads(file.read_text()) if file.exists() else {
            "messages": [],
            "steps": [],
            "datasource": None,
            "columns": [],
        }

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

    def _persist(self) -> None:
        """Сохранить состояние сессии на диск."""
        file = self._path.with_suffix(f".{self.session_id}.json")
        file.write_text(json.dumps(self._state, ensure_ascii=False))
