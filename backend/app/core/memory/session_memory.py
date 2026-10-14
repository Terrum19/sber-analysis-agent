"""Память уровня 1: краткосрочная память сессии.

Хранит историю текстового диалога и историю ResearchStep, привязанную
к session_id. Чтобы не взрывать контекст, в промпт идёт скользящее
окно последних 3-5 сообщений + сжатая выжимка предыдущих шагов.
"""
import json
from pathlib import Path

from app.config import settings


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
        return json.loads(file.read_text()) if file.exists() else {"messages": [], "steps": []}

    def build_context(self, question: str) -> dict:
        """Собрать контекст: скользящее окно сообщений + выжимка шагов."""
        window = self._state["messages"][-5:]
        summary = "; ".join(f"шаг {s['step_id']}: {s['purpose']}" for s in self._state["steps"])
        return {"messages": window, "summary": summary, "question": question}

    def record_step(self, step, result_table: str) -> None:
        """Запомнить выполненный шаг и его результат."""
        self._state["steps"].append({"step_id": step.step_id, "purpose": step.purpose, "result": result_table})

    def record_answer(self, answer: str) -> None:
        """Запомнить ответ агента."""
        self._state["messages"].append({"role": "agent", "content": answer})
        self._persist()

    def _persist(self) -> None:
        """Сохранить состояние сессии на диск."""
        file = self._path.with_suffix(f".{self.session_id}.json")
        file.write_text(json.dumps(self._state, ensure_ascii=False))
