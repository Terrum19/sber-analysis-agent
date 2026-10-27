"""Память уровня 1: краткосрочная память сессии.

Сессия — список ходов: на каждое сообщение пользователя ``start_turn``
заводит ход (вопрос, таблицы), шаги плана пишутся в него, ``finish_turn``
закрывает его ответом и статусом (ok / failed / clarification). Ответ
на уточнение продолжает тот же ход (``resume_turn``).

В контекст планировщика идут последние ``CONTEXT_TURNS`` успешных ходов
с планом: вопрос, шаги с аргументами, итог и колонки результата. Размер
контекста не растёт с числом ходов, шаги упавших ходов в него не попадают.

Ход-ответ на прошлое сообщение (``reply_to``) вместо прошлых ходов получает
цепочку ответов: выбранный ход и его предков по ``reply_to``. План выбранного
хода планировщик правит как черновик, смена вида берёт его график.

``datasource`` — таблица по умолчанию (``$source``), пока агент не выбирает
таблицу сам (#165); её смена ходы не сбрасывает. Метаданные сессии
(``title``, ``datasource_id``, ``created_at``) лежат в том же состоянии,
поэтому сессия переживает перезапуск приложения. Состояние до списка ходов
(``tables``, ``steps``, ``columns``) открывается, но не читается.

Состояние сессии хранится в SQLite (таблица ``session_state`` по адресу
``settings.sqlite_path``) вместо отдельных JSON-файлов.
"""

import json
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.profile_memory import ProfileMemory

# Сколько прошлых успешных ходов видит планировщик.
CONTEXT_TURNS = 3


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
        self._state.setdefault("turns", [])
        self._turn: dict | None = None

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
        return {"datasource": None, "pending_recovery": None, "turns": []}

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

    @staticmethod
    def delete(session_id: str) -> bool:
        """Удалить сессию из SQLite."""
        conn = _connect(Path(settings.sqlite_path))
        try:
            cursor = conn.execute(
                "DELETE FROM session_state WHERE session_id = ?",
                (session_id,),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

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

    def set_title(self, title: str) -> None:
        """Переименовать сессию; остальные метаданные не меняются."""
        self._state["title"] = title
        self._persist()

    def set_table(self, table: str) -> None:
        """Сделать таблицу таблицей по умолчанию ($source) для следующих ходов.

        Ходы остаются: вопрос по новой таблице может продолжать прошлый.
        Незавершённое уточнение сбрасывается — его план по прошлой таблице.
        При смене таблицы сбрасывается и ``datasource_id``: новая таблица
        может быть из другого источника.
        """
        if table == self.table:
            return
        if self.table is not None:
            self._state["datasource_id"] = None
        self._state["datasource"] = table
        self._state["pending_recovery"] = None
        self._persist()

    @property
    def turn_id(self) -> str | None:
        """id текущего хода (None — ход не начат)."""
        return self._turn["turn_id"] if self._turn is not None else None

    def start_turn(self, question: str, reply_to: str | None = None) -> str:
        """Начать ход по вопросу пользователя и вернуть его turn_id.

        Ход сохраняется сразу со статусом failed: ход, не дошедший до
        finish_turn (сбой процесса, отключение клиента), таким и остаётся.
        """
        self._turn = {
            "turn_id": uuid.uuid4().hex,
            "question": question,
            "tables": [self.table] if self.table is not None else [],
            "steps": [],
            "result_table": None,
            "result_columns": [],
            "headline": None,
            "chart": None,
            "status": "failed",
            "reply_to": reply_to,
        }
        self._state["turns"].append(self._turn)
        self._persist()
        return self._turn["turn_id"]

    def resume_turn(self, turn_id: str | None) -> bool:
        """Продолжить ход после уточнения; False — такого хода нет."""
        turn = self._find(turn_id)
        if turn is None:
            return False
        self._turn = turn
        return True

    def _find(self, turn_id: str | None) -> dict | None:
        for turn in self._state["turns"]:
            if turn["turn_id"] == turn_id:
                return turn
        return None

    def reply_chain(self) -> list[dict]:
        """Ход, на который отвечает текущий, и его предки по reply_to, старые первыми.

        Пусто, если текущий ход не ответ или выбранный ход не успешный ход
        с планом: тогда планировщик получает обычные прошлые ходы.
        """
        target = self._find(self._turn.get("reply_to")) if self._turn is not None else None
        if target is None or target["status"] != "ok" or not target["steps"]:
            return []
        chain = [target]
        while len(chain) < CONTEXT_TURNS:
            parent = self._find(chain[-1].get("reply_to"))
            if parent is None or parent["status"] != "ok":
                break
            chain.append(parent)
        return chain[::-1]

    def record_step(self, step) -> None:
        """Записать выполненный шаг в текущий ход (сохраняется в finish_turn)."""
        self._turn["steps"].append(
            {
                "step_id": step.step_id,
                "tool": step.tool,
                "input": step.input,
                "arguments": step.arguments,
                "columns": step.columns,
                "purpose": step.purpose,
            }
        )

    def record_chart(self, chart_type: str, result_table: str, title: str = "") -> None:
        """Запомнить график текущего хода (для смены вида без LLM)."""
        self._turn["chart"] = {"type": chart_type, "title": title, "result_table": result_table}

    def finish_turn(
        self,
        status: str,
        headline: str | None = None,
        result_table: str | None = None,
        result_columns: list[str] | None = None,
    ) -> None:
        """Закрыть текущий ход ответом и статусом: ok, failed или clarification."""
        self._turn.update(
            status=status,
            headline=headline,
            result_table=result_table,
            result_columns=list(result_columns or []),
        )
        self._persist()

    def last_chart(self) -> dict | None:
        """График хода, на который отвечает текущий, иначе последнего успешного с графиком."""
        target = self._find(self._turn.get("reply_to")) if self._turn is not None else None
        if target is not None and target["status"] == "ok" and target["chart"] is not None:
            return target["chart"]
        for turn in reversed(self._state["turns"]):
            if turn["status"] == "ok" and turn["chart"] is not None:
                return turn["chart"]
        return None

    def recent_turns(self) -> list[dict]:
        """Последние CONTEXT_TURNS успешных ходов с планом, старые первыми."""
        done = [t for t in self._state["turns"] if t["status"] == "ok" and t["steps"]]
        return done[-CONTEXT_TURNS:]

    def build_context(self, question: str) -> dict:
        """Собрать контекст: прошлые ходы или цепочка ответа + колонки + паспорт таблицы."""
        datasource = self.table

        # Профиль данных (паспорт таблицы) для долгосрочного контекста;
        # без DuckDB (память открыта только ради истории) профиля нет.
        profile = None
        enrichment = []
        columns: list[str] = []
        if self.db is not None and datasource is not None:
            passport = ProfileMemory(self.db)
            profile = passport.load_profile(datasource)
            enrichment = passport.load_enrichment(datasource)
            if profile is not None:
                columns = [column.name for column in profile.columns]
            else:
                # Без паспорта колонки из каталога; каталог недоступен — план
                # строится без них, упавший шаг исправит планировщик.
                try:
                    columns = self.db.columns(datasource)
                except duckdb.Error:
                    columns = []

        reply = self.reply_chain()
        return {
            "turns": [] if reply else self.recent_turns(),
            "reply": reply,
            "question": question,
            "columns": columns,
            "datasource": datasource,
            "profile": profile,
            # Описание таблицы из паспорта: названия для отбора колонок, описания для промпта.
            "titles": {row.column: row.title for row in enrichment if row.title},
            "descriptions": {row.column: row.description for row in enrichment if row.description},
        }

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
