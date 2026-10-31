"""Память уровня 1: краткосрочная память сессии.

Сессия — список ходов: на каждое сообщение пользователя ``start_turn``
заводит ход (вопрос, таблицы), шаги плана пишутся в него, ``finish_turn``
закрывает его ответом и статусом (ok / failed / clarification). Ответ
на уточнение продолжает тот же ход (``resume_turn``).

Ход с шагами пишет чекпоинт (``save_checkpoint``) после каждого шага или
плитки дашборда: план, выполненные шаги и их temp_result_vN. Ход,
остановленный пользователем или упавший, по чекпоинту продолжается с места
остановки (``resume_checkpoint``), а не считается заново. Успешный ход
чекпоинт удаляет.

В контекст планировщика идут последние ``settings.context_turns`` ходов
любого статуса: вопрос и ответ, у успешных — ещё шаги с аргументами и
колонки результата. Шаги упавших и прерванных уточнением ходов в контекст
не попадают (#107). Ходы старше окна планировщик получает по запросу:
тул ``read_history`` ищет их через ``find_turns``.

Воспоминания — короткие записи сессии: что пользователь попросил
запомнить. Последние ``settings.context_memories`` включённых идут в
контекст планировщика и инсайта каждого хода, без отбора по вопросу (#197):
их мало. Они лежат в своей таблице ``session_memories``, по строке на
запись, а не в состоянии сессии: ход держит состояние до ``finish_turn`` и
пишет его целиком, запись через API во время хода иначе затиралась бы.

Ход-ответ на прошлое сообщение (``reply_to``) вместо прошлых ходов получает
цепочку ответов: выбранный ход и его предков по ``reply_to``. План выбранного
хода планировщик правит как черновик, смена вида берёт его график.

``datasource`` — таблица по умолчанию (``$source``), пока агент не выбирает
таблицу сам (#165); её смена ходы не сбрасывает. Метаданные сессии
(``title``, ``datasource_id``, ``created_at``) лежат в том же состоянии,
поэтому сессия переживает перезапуск приложения. Состояние до списка ходов
(``tables``, ``steps``, ``columns``) открывается, но не читается.

Сессия из бандла другого пользователя (#249) помечена ``imported``, её ходы —
тоже: в промпте они идут как данные, а не указания. Воспоминания такой сессии
приходят выключенными.

Состояние сессии хранится в SQLite (таблица ``session_state`` по адресу
``settings.sqlite_path``) вместо отдельных JSON-файлов.
"""

import json
import re
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.profile_memory import ProfileMemory

# Сколько ходов отдаёт поиск по истории (read_history).
HISTORY_HITS = 3
# Первые буквы слова при поиске по истории: «выручка» и «выручку» совпадают.
STEM_LETTERS = 5


def _connect(path: Path) -> sqlite3.Connection:
    """Открыть SQLite сессий, создав таблицы при первом обращении."""
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
    # text_key — текст без регистра и лишних пробелов: тот же текст не дублируется.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_memories (
            memory_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            text TEXT NOT NULL,
            text_key TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            turn_id TEXT,
            UNIQUE (session_id, text_key)
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
        # Текущий ход ещё не закрыт finish_turn: в прошлые ходы он не входит.
        self._open = False

    @staticmethod
    def list_sessions(limit: int, offset: int = 0) -> list[dict]:
        """Метаданные сохранённых сессий одним запросом, недавно изменённые первыми.

        Только сессии хотя бы с одним ходом: пустая сессия (открыли чат и не
        спросили) в сайдбар не попадает. Состояние целиком (с историей
        сообщений) не читается.
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
                       json_extract(state, '$.created_at') AS created_at,
                       COALESCE(json_extract(state, '$.imported'), 0) AS imported
                FROM session_state
                WHERE json_array_length(state, '$.turns') > 0
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
    def find_by_datasource(datasource_id: str) -> list[str]:
        """Список session_id сессий, привязанных к источнику данных."""
        conn = _connect(Path(settings.sqlite_path))
        try:
            rows = conn.execute(
                """
                SELECT session_id
                FROM session_state
                WHERE json_extract(state, '$.datasource_id') = ?
                """,
                (datasource_id,),
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()

    @staticmethod
    def delete(session_id: str) -> bool:
        """Удалить сессию из SQLite и каскадно удалить её дашборды."""
        conn = _connect(Path(settings.sqlite_path))
        try:
            cursor = conn.execute(
                "DELETE FROM session_state WHERE session_id = ?",
                (session_id,),
            )
            conn.execute("DELETE FROM session_memories WHERE session_id = ?", (session_id,))
            conn.commit()
            deleted = cursor.rowcount > 0
        finally:
            conn.close()
        if deleted:
            from app.core.dashboards.store import DashboardStore

            DashboardStore(Path(settings.sqlite_path)).delete_by_session(session_id)
        return deleted

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

    @property
    def imported(self) -> bool:
        """Сессия создана импортом бандла другого пользователя (#249)."""
        return bool(self._state.get("imported"))

    @property
    def source(self) -> dict | None:
        """Источник автора импортированной сессии: kind, маскированный DSN, таблицы."""
        return self._state.get("source")

    @property
    def turns(self) -> list[dict]:
        """Ходы сессии, старые первыми."""
        return self._state["turns"]

    @property
    def replay_tiles(self) -> dict[str, dict]:
        """Плитка импортированного дашборда -> шаг хода, по которому она пересчитывается (#250)."""
        return self._state.get("replay_tiles") or {}

    def import_session(
        self,
        title: str,
        turns: list[dict],
        source: dict | None,
        replay_tiles: dict[str, dict] | None = None,
    ) -> None:
        """Заполнить новую сессию ходами из бандла; ничего из них не выполняется.

        Таблицы у сессии нет: на первый вопрос получателя агент сам выберет
        таблицу из его данных.
        """
        self._state.update(
            title=title,
            datasource_id=None,
            datasource=None,
            pending_recovery=None,
            imported=True,
            source=source,
            turns=turns,
            replay_tiles=replay_tiles or {},
            created_at=datetime.now(UTC).isoformat(),
        )
        self._persist()

    def record_replay(self, results: dict[str, list[str]], charts: dict[str, str]) -> None:
        """Запомнить таблицы, пересчитанные у получателя (#250).

        ``results`` — таблицы шагов по ходам, ``charts`` — таблица графика
        хода: по ней работают смена вида и выгрузка в Excel.
        """
        for turn in self._state["turns"]:
            if turn["turn_id"] in results:
                turn["results"] = results[turn["turn_id"]]
            table = charts.get(turn["turn_id"])
            if table is None:
                continue
            turn["result_table"] = table
            if turn.get("chart") is not None:
                turn["chart"]["result_table"] = table
        self._persist()

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
        self._open = True
        self._persist()
        return self._turn["turn_id"]

    def use_table(self, table: str) -> None:
        """Агент сам выбрал таблицу под вопрос: она становится таблицей сессии и хода.

        Вызывается в начатом ходе. Таблица, выбранная раньше в этом же ходе,
        из таблиц хода убирается.
        """
        previous = self.table
        self.set_table(table)
        others = [name for name in self._turn["tables"] if name not in (table, previous)]
        self._turn["tables"] = [table, *others]
        self._persist()

    def resume_turn(self, turn_id: str | None) -> bool:
        """Продолжить ход после уточнения; False — такого хода нет."""
        turn = self._find(turn_id)
        if turn is None:
            return False
        self._turn = turn
        self._open = True
        return True

    @property
    def question(self) -> str | None:
        """Вопрос текущего хода (None — ход не начат)."""
        return self._turn["question"] if self._turn is not None else None

    def save_checkpoint(self, checkpoint: dict) -> None:
        """Сохранить, докуда дошёл текущий ход: остановленный ход продолжится отсюда.

        Пишется сразу вместе с шагами хода: остановка или падение процесса
        до finish_turn их не теряет.
        """
        self._turn["checkpoint"] = checkpoint
        self._persist()

    def resume_checkpoint(self, turn_id: str) -> dict | None:
        """Продолжить незавершённый ход с его чекпоинта; None — продолжать нечего.

        Продолжается только ход, не закончившийся ответом (статус failed):
        остановленный пользователем, оборванный или упавший.
        """
        turn = self._find(turn_id)
        if turn is None or turn["status"] != "failed" or not turn.get("checkpoint"):
            return None
        self._turn = turn
        self._open = True
        return turn["checkpoint"]

    def answered_turn(self, turn_id: str) -> dict | None:
        """Ход, уже закончившийся ответом; None — такого нет или он не ответил.

        Его ответ мог не дойти до фронта (страница перезагрузилась посреди
        хода): «Продолжить» тогда отдаёт сохранённый ответ.
        """
        turn = self._find(turn_id)
        return turn if turn is not None and turn["status"] == "ok" else None

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
        while len(chain) < settings.context_turns:
            parent = self._find(chain[-1].get("reply_to"))
            if parent is None or parent["status"] != "ok":
                break
            chain.append(parent)
        return chain[::-1]

    def record_step(self, step, result_table: str | None = None) -> None:
        """Записать выполненный шаг в текущий ход (сохраняется в finish_turn).

        Таблицы, присоединённые шагом (right_table, tables у join_related),
        добавляются к таблицам хода.
        """
        if result_table:
            self._turn.setdefault("results", []).append(result_table)
        arguments = step.arguments or {}
        joined = arguments.get("tables")
        joined = [*(joined if isinstance(joined, list) else []), arguments.get("right_table")]
        for table in joined:
            if (
                isinstance(table, str)
                and not table.startswith("$")
                and table not in self._turn["tables"]
            ):
                self._turn["tables"].append(table)
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

    def record_review(
        self, review: dict, draft: dict | None = None, first: dict | None = None
    ) -> None:
        """Итог проверки хода (A-29): последний вердикт, черновой ответ и первый вердикт.

        draft и first есть, только если ответ исправлялся.
        """
        self._turn["review"] = review
        if draft is not None:
            self._turn["draft"] = draft
        if first is not None:
            self._turn["first_review"] = first

    def record_chart(self, chart_type: str, result_table: str, title: str = "") -> None:
        """Запомнить график текущего хода (для смены вида без LLM)."""
        self._turn["chart"] = {"type": chart_type, "title": title, "result_table": result_table}

    def owns_result(self, result_table: str) -> bool:
        for turn in self._state["turns"]:
            chart = turn.get("chart") or {}
            if result_table in (turn.get("result_table"), chart.get("result_table")):
                return True
            if result_table in turn.get("results", []):
                return True
        pending = self._state.get("pending_recovery") or {}
        tables = pending.get("step_tables") or {}
        return result_table in tables.values() or result_table == pending.get("input_table")

    def finish_turn(
        self,
        status: str,
        headline: str | None = None,
        result_table: str | None = None,
        result_columns: list[str] | None = None,
        details: list[str] | None = None,
        reason: str | None = None,
    ) -> None:
        """Закрыть текущий ход ответом и статусом: ok, failed или clarification.

        details — пункты ответа под headline, reason — почему ход не дал ответа:
        по ним агент потом объясняет вывод или неудачу. Ход с ответом продолжать
        больше не нужно: его чекпоинт удаляется.
        """
        if status == "ok":
            self._turn.pop("checkpoint", None)
        self._turn.update(
            status=status,
            headline=headline,
            details=list(details or []),
            reason=reason,
            result_table=result_table,
            result_columns=list(result_columns or []),
        )
        self._open = False
        self._persist()

    def last_chart(self) -> dict | None:
        """График хода, на который отвечает текущий, иначе последнего успешного с графиком.

        У графика хода, импортированного по ссылке, нет таблицы результата (#249):
        его не перестроить, и более старый график вместо него не берётся. Из
        файла ``.session`` ход приходит с таблицей (#251).
        """
        target = self._find(self._turn.get("reply_to")) if self._turn is not None else None
        if target is not None and target["status"] == "ok" and target["chart"] is not None:
            chart = target["chart"]
        else:
            chart = next(
                (
                    turn["chart"]
                    for turn in reversed(self._state["turns"])
                    if turn["status"] == "ok" and turn["chart"] is not None
                ),
                None,
            )
        return chart if chart is not None and chart.get("result_table") else None

    def _past_turns(self) -> list[dict]:
        """Ходы, кроме идущего, с номером в сессии (с 1); шаги — только у успешных."""
        return [
            {**turn, "number": number, "steps": turn["steps"] if turn["status"] == "ok" else []}
            for number, turn in enumerate(self._state["turns"], 1)
            if not (self._open and turn is self._turn)
        ]

    def recent_turns(self) -> list[dict]:
        """Последние ``settings.context_turns`` ходов любого статуса, старые первыми."""
        return self._past_turns()[-settings.context_turns :] if settings.context_turns else []

    def explained_turns(self) -> list[dict]:
        """Ходы для пояснения прошлых ответов: последние ходы и ход, на который отвечает текущий.

        Ход из reply_to старше окна последних ходов добавляется в начало;
        у него и только у него ``focus`` — True.
        """
        reply_to = self._turn.get("reply_to") if self._turn is not None else None
        turns = self.recent_turns()
        if reply_to is not None and all(turn["turn_id"] != reply_to for turn in turns):
            target = [turn for turn in self._past_turns() if turn["turn_id"] == reply_to]
            turns = target + turns
        return [{**turn, "focus": turn["turn_id"] == reply_to} for turn in turns]

    def find_turns(self, query: str | None = None, number: int | None = None) -> list[dict]:
        """Прошлые ходы для read_history: ход по номеру или поиск по вопросам и ответам.

        Поиск — по словам запроса в вопросе и ответе хода (совпадение первых
        ``STEM_LETTERS`` букв), лучшие ``HISTORY_HITS`` ходов, старые первыми.
        """
        past = self._past_turns()
        if number is not None:
            return [turn for turn in past if turn["number"] == number]
        wanted = _stems(query or "")
        scored = [
            (len(wanted & _stems(f"{turn['question']} {turn.get('headline') or ''}")), turn)
            for turn in past
        ]
        hits = sorted((pair for pair in scored if pair[0]), key=lambda pair: -pair[0])
        return sorted((turn for _, turn in hits[:HISTORY_HITS]), key=lambda turn: turn["number"])

    # --- Воспоминания ---

    def memories(self, include_disabled: bool = False) -> list[dict]:
        """Воспоминания сессии, старые первыми: включённые или все.

        Читаются из SQLite на каждый вызов: запись, добавленная через API
        во время хода, видна сразу.
        """
        query = (
            "SELECT memory_id, text, source, created_at, enabled, turn_id"
            " FROM session_memories WHERE session_id = ?"
        )
        if not include_disabled:
            query += " AND enabled"
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(query + " ORDER BY rowid", (self.session_id,)).fetchall()
        finally:
            conn.close()
        return [{**row, "enabled": bool(row["enabled"])} for row in map(dict, rows)]

    def context_memories(self) -> list[str]:
        """Тексты последних ``settings.context_memories`` включённых воспоминаний."""
        limit = settings.context_memories
        return [item["text"] for item in self.memories()[-limit:]] if limit else []

    def remember(self, text: str, source: str = "user") -> dict | None:
        """Добавить воспоминание и вернуть его запись; пустой текст — None.

        Уже записанный текст (без учёта регистра и пробелов) не дублируется:
        возвращается прежняя запись, выключенная — снова включается. ``source`` —
        откуда запись: ``user`` (просьба в чате или кнопка), позже — результаты
        глубокого исследования.
        """
        text = " ".join(text.split())
        if not text:
            return None
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO session_memories
                    (memory_id, session_id, text, text_key, source, created_at, enabled, turn_id)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT (session_id, text_key) DO UPDATE SET enabled = 1
                """,
                (
                    uuid.uuid4().hex,
                    self.session_id,
                    text,
                    text.casefold(),
                    source,
                    datetime.now(UTC).isoformat(),
                    self.turn_id,
                ),
            )
            conn.commit()
            (memory_id,) = conn.execute(
                "SELECT memory_id FROM session_memories WHERE session_id = ? AND text_key = ?",
                (self.session_id, text.casefold()),
            ).fetchone()
        finally:
            conn.close()
        return self._memory(memory_id)

    def import_memories(self, items: list[dict]) -> None:
        """Записать воспоминания из бандла выключенными: включает их пользователь.

        Записи без хода этой сессии получают ``turn_id`` None; повтор текста
        пропускается.
        """
        known = {turn["turn_id"] for turn in self._state["turns"]}
        conn = self._connect()
        try:
            for item in items:
                text = " ".join(item["text"].split())
                if not text:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO session_memories
                        (memory_id, session_id, text, text_key, source, created_at, enabled, turn_id)
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        self.session_id,
                        text,
                        text.casefold(),
                        item["source"],
                        item["created_at"],
                        item["turn_id"] if item.get("turn_id") in known else None,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def set_memory_enabled(self, memory_id: str, enabled: bool) -> dict | None:
        """Включить или выключить воспоминание; None — такой записи нет."""
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE session_memories SET enabled = ? WHERE session_id = ? AND memory_id = ?",
                (int(enabled), self.session_id, memory_id),
            )
            conn.commit()
        finally:
            conn.close()
        return self._memory(memory_id)

    def _memory(self, memory_id: str) -> dict | None:
        return next(
            (
                item
                for item in self.memories(include_disabled=True)
                if item["memory_id"] == memory_id
            ),
            None,
        )

    def build_context(self, question: str) -> dict:
        """Собрать контекст: прошлые ходы или цепочка ответа, воспоминания, колонки, паспорт."""
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
            "memories": self.context_memories(),
            "question": question,
            "columns": columns,
            "datasource": datasource,
            "profile": profile,
            # Описание таблицы из паспорта: названия для отбора колонок, описания для промпта.
            "titles": {row.column: row.title for row in enrichment if row.title},
            "descriptions": {row.column: row.description for row in enrichment if row.description},
        }

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


def _stems(text: str) -> set[str]:
    """Основы слов для поиска по истории: слова от 3 букв, первые STEM_LETTERS букв."""
    return {word[:STEM_LETTERS] for word in re.findall(r"\w{3,}", text.casefold())}
