"""Связи между таблицами: внешние ключи, по которым агент соединяет таблицы (#165).

Источники связей, по убыванию доверия:
1. declared — ограничения FOREIGN KEY из схемы: PostgreSQL отдаёт их через
   коннектор (BaseConnector.foreign_keys), у таблиц самой DuckDB они лежат в
   duckdb_constraints().
2. inferred — у таблиц без объявленных ключей (CSV): пара колонок с одним
   именем или вида <таблица>_id -> <таблица>.id. Кандидат принимается, только
   если ключ справа уникален, а значения слева почти все находятся справа —
   это проверяет один SQL-запрос на пару, без LLM.

Связи ищутся при подключении (ingest.attach/restore) и лежат в
_meta.table_relation: планировщик получает их на каждый вопрос без запросов
к данным. Сторона «многие» — table/columns, сторона «один» — ref_table/ref_columns.
"""

import json
import logging
import re
from dataclasses import dataclass

import duckdb

from app.core.data.profiling import INTEGER_TYPES, TEXT_TYPES, TableProfile, quote_identifier
from app.core.data.semantic_select import matches, rank_columns, tokenize

logger = logging.getLogger(__name__)

# Доля различных значений колонки слева, которые должны найтись в ключе справа.
MIN_COVERAGE = 0.9
# Меньше различных значений — совпадение может быть случайным.
MIN_DISTINCT = 2
# Колонки связанной таблицы в промпте планировщика и число связанных таблиц.
PROMPT_COLUMNS = 12
PROMPT_TABLES = 3
# Колонка «id» есть почти в каждой таблице: id = id связью не считаем.
SURROGATE_KEY = "id"
KEY_KINDS = {"identifier", "text"}

SCHEMA = (
    "CREATE SCHEMA IF NOT EXISTS _meta",
    """CREATE TABLE IF NOT EXISTS _meta.table_relation (
        table_name   VARCHAR NOT NULL,
        columns      JSON NOT NULL,
        ref_table    VARCHAR NOT NULL,
        ref_columns  JSON NOT NULL,
        source       VARCHAR NOT NULL,
        coverage     DOUBLE,
        found_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
)


@dataclass(frozen=True)
class Relation:
    """Внешний ключ: table.columns ссылается на ref_table.ref_columns."""

    table: str
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]
    source: str = "declared"
    coverage: float | None = None

    def other(self, table: str) -> str:
        return self.ref_table if table == self.table else self.table

    def condition(self, table: str) -> str:
        """Условие для join_tables, когда table — текущая таблица (t)."""
        mine, theirs = (
            (self.columns, self.ref_columns)
            if table == self.table
            else (self.ref_columns, self.columns)
        )
        return " AND ".join(
            f"t.{quote_identifier(left)} = t_right.{quote_identifier(right)}"
            if not _plain(left) or not _plain(right)
            else f"t.{left} = t_right.{right}"
            for left, right in zip(mine, theirs, strict=True)
        )

    def describe(self) -> str:
        """«orders.user_id -> users.id (ключ из схемы)»."""
        origin = (
            "ключ из схемы источника"
            if self.source == "declared"
            else f"найдено по данным: {self.coverage:.0%} значений есть в {self.ref_table}"
        )
        left = ", ".join(self.columns)
        right = ", ".join(self.ref_columns)
        return f"{self.table}.{left} -> {self.ref_table}.{right} ({origin})"


def _plain(name: str) -> bool:
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is not None


def ensure_schema(db) -> None:
    for statement in SCHEMA:
        db.execute(statement)


def save(db, tables: list[str], relations: list[Relation]) -> None:
    """Заменить связи, в которых участвует хотя бы одна из tables."""
    ensure_schema(db)
    with db.transaction():
        db.execute(
            "DELETE FROM _meta.table_relation "
            "WHERE list_contains($tables, table_name) OR list_contains($tables, ref_table)",
            {"tables": tables},
        )
        for relation in relations:
            db.execute(
                "INSERT INTO _meta.table_relation "
                "(table_name, columns, ref_table, ref_columns, source, coverage) "
                "VALUES ($table, $columns, $ref_table, $ref_columns, $source, $coverage)",
                {
                    "table": relation.table,
                    "columns": json.dumps(list(relation.columns), ensure_ascii=False),
                    "ref_table": relation.ref_table,
                    "ref_columns": json.dumps(list(relation.ref_columns), ensure_ascii=False),
                    "source": relation.source,
                    "coverage": relation.coverage,
                },
            )


def load(db, table: str | None = None) -> list[Relation]:
    """Связи таблицы (с любой стороны) или все; только между существующими таблицами."""
    ensure_schema(db)
    rows = db.query(
        "SELECT table_name, columns, ref_table, ref_columns, source, coverage "
        "FROM _meta.table_relation "
        "WHERE $table IS NULL OR table_name = $table OR ref_table = $table "
        "ORDER BY source, table_name, ref_table",
        {"table": table},
    )
    existing = set(db.user_tables())
    relations = []
    for row in rows:
        if row["table_name"] not in existing or row["ref_table"] not in existing:
            continue
        relations.append(
            Relation(
                table=row["table_name"],
                columns=tuple(json.loads(row["columns"])),
                ref_table=row["ref_table"],
                ref_columns=tuple(json.loads(row["ref_columns"])),
                source=row["source"],
                coverage=row["coverage"],
            )
        )
    return relations


def delete(db, table: str) -> None:
    """Удалить связи таблицы (таблицу отключили)."""
    save(db, [table], [])


def declared_in_duckdb(db, tables: set[str]) -> list[Relation]:
    """FOREIGN KEY таблиц самой DuckDB (CREATE TABLE ... REFERENCES)."""
    rows = db.query(
        "SELECT table_name, constraint_column_names AS columns, referenced_table, "
        "referenced_column_names AS ref_columns FROM duckdb_constraints() "
        "WHERE constraint_type = 'FOREIGN KEY' AND schema_name = 'main' "
        "AND database_name IN ('temp', current_database())"
    )
    return [
        Relation(
            table=row["table_name"],
            columns=tuple(row["columns"]),
            ref_table=row["referenced_table"],
            ref_columns=tuple(row["ref_columns"]),
        )
        for row in rows
        if row["table_name"] in tables and row["referenced_table"] in tables
    ]


def _name_stems(table: str) -> set[str]:
    """Как таблицу называют в имени ключа: demo_users -> demo_users, users, user, demo_user."""
    lowered = table.lower()
    names = {lowered, lowered.rsplit("_", 1)[-1]}
    for name in names.copy():
        if name.endswith("ies"):
            names.add(name[:-3] + "y")
        elif name.endswith("es"):
            names.add(name[:-2])
        if name.endswith("s"):
            names.add(name[:-1])
    return names


def _key_class(column) -> str | None:
    """Класс типа ключа: сравнивать можно только колонки одного класса."""
    dtype = column.dtype.upper()
    if column.kind not in KEY_KINDS and not (
        column.kind == "numeric" and column.name.lower().endswith("id")
    ):
        return None
    if dtype in INTEGER_TYPES:
        return "integer"
    if dtype in TEXT_TYPES:
        return "text"
    return None


def candidates(left: TableProfile, right: TableProfile) -> list[tuple[str, str]]:
    """Пары (колонка left, колонка right), где left может ссылаться на ключ right."""
    stems = _name_stems(right.table)
    pairs = []
    for a in left.columns:
        kind = _key_class(a)
        if kind is None:
            continue
        name = a.name.lower()
        for b in right.columns:
            if _key_class(b) != kind:
                continue
            target = b.name.lower()
            same = name == target and name != SURROGATE_KEY
            named = target == SURROGATE_KEY and any(
                name in (f"{stem}_id", f"{stem}id") for stem in stems
            )
            if same or named:
                pairs.append((a.name, b.name))
    return pairs


def verify(db, left: str, column: str, right: str, key: str) -> float | None:
    """Доля различных значений left.column, найденных в right.key; None — не ключ.

    Ключ справа должен быть уникальным и без пропусков: иначе соединение
    размножит строки слева, и это не внешний ключ.
    """
    a, b = quote_identifier(column), quote_identifier(key)
    row = db.query(
        f"""WITH l AS (SELECT DISTINCT {a} AS k FROM {quote_identifier(left)} WHERE {a} IS NOT NULL),
        r AS (SELECT {b} AS k FROM {quote_identifier(right)}),
        stats AS (SELECT count(*) AS total, count(DISTINCT k) AS distinct_keys,
                         count(k) AS present FROM r)
        SELECT (SELECT count(*) FROM l) AS values,
               (SELECT count(*) FROM l SEMI JOIN r USING (k)) AS found,
               stats.total = stats.distinct_keys AND stats.total = stats.present AS unique_key
        FROM stats"""
    )[0]
    if not row["unique_key"] or row["values"] < MIN_DISTINCT:
        return None
    return row["found"] / row["values"]


def infer(
    db, profiles: dict[str, TableProfile], new: set[str], known: set[tuple]
) -> list[Relation]:
    """Связи по данным между new и всеми таблицами profiles (в обе стороны).

    known — пары (table, ref_table) с объявленными ключами: их не угадываем.
    """
    found = []
    for left, left_profile in profiles.items():
        for right, right_profile in profiles.items():
            if left == right or (left not in new and right not in new):
                continue
            if (left, right) in known:
                continue
            for column, key in candidates(left_profile, right_profile):
                try:
                    coverage = verify(db, left, column, right, key)
                except duckdb.Error as error:
                    logger.warning(
                        "связь %s.%s -> %s.%s не проверена: %s", left, column, right, key, error
                    )
                    continue
                if coverage is not None and coverage >= MIN_COVERAGE:
                    found.append(
                        Relation(left, (column,), right, (key,), "inferred", round(coverage, 4))
                    )
    return _drop_mirrors(found)


def _drop_mirrors(relations: list[Relation]) -> list[Relation]:
    """a.k -> b.k и b.k -> a.k (оба ключа уникальны, 1:1) — оставить одну связь."""
    kept: list[Relation] = []
    seen = set()
    for relation in sorted(relations, key=lambda r: -(r.coverage or 0)):
        pair = frozenset(
            [(relation.table, relation.columns), (relation.ref_table, relation.ref_columns)]
        )
        if pair in seen:
            continue
        seen.add(pair)
        kept.append(relation)
    return kept


def discover(db, connector, tables: list[str]) -> list[Relation]:
    """Найти и сохранить связи подключённых tables со всеми таблицами пользователя.

    Вызывать под db.lock после сохранения профилей. Сбой поиска связей не
    отменяет подключение: таблицы просто останутся без связей.
    """
    from app.core.memory.profile_memory import ProfileMemory

    try:
        existing = set(db.user_tables())
        declared = [
            relation
            for relation in [*connector.foreign_keys(db), *declared_in_duckdb(db, existing)]
            if relation.table in existing and relation.ref_table in existing
        ]
        known = {(r.table, r.ref_table) for r in declared}
        known |= {(r.ref_table, r.table) for r in declared}
        memory = ProfileMemory(db)
        profiles = {
            table: profile
            for table in sorted(existing)
            if (profile := memory.load_profile(table)) is not None
        }
        relations = list(dict.fromkeys(declared))
        relations += infer(db, profiles, set(tables), known)
        save(db, tables, relations)
    except duckdb.Error:
        logger.exception("связи таблиц %s не найдены", tables)
        return []
    for relation in relations:
        logger.info("связь: %s", relation.describe())
    return relations


@dataclass
class Related:
    """Таблица, связанная с текущей, для промпта планировщика."""

    table: str
    relation: Relation
    columns: list[str]
    score: int


def related_tables(db, table: str, question: str, limit: int = PROMPT_TABLES) -> list[Related]:
    """Таблицы, связанные с table внешним ключом, по совпадению со словами вопроса.

    Без вызова LLM: связей у таблицы обычно немного, поэтому планировщик
    видит их все (до limit), а вопрос только упорядочивает таблицы и колонки.
    """
    from app.core.memory.profile_memory import ProfileMemory

    memory = ProfileMemory(db)
    words = tokenize(question)
    related: dict[str, Related] = {}
    for relation in load(db, table):
        other = relation.other(table)
        if other == table or other in related:
            continue
        profile = memory.load_profile(other)
        names = (
            [column.name for column in profile.columns]
            if profile is not None
            else db.columns(other)
        )
        titles = {row.column: row.title for row in memory.load_enrichment(other) if row.title}
        ranked = rank_columns(question, names, profile, titles)
        text = " ".join([other, *names, *titles.values()]).replace("_", " ")
        score = matches(words, tokenize(text))
        related[other] = Related(other, relation, ranked, score)
    return sorted(related.values(), key=lambda item: (-item.score, item.table))[:limit]


def describe_related(table: str, related: list[Related]) -> str:
    """Блок промпта: связанные таблицы, их колонки и готовое условие join_tables."""
    if not related:
        return ""
    lines = [
        (
            f"Связанные таблицы (текущая таблица $source — {table}). Если для ответа нужна "
            "колонка из связанной таблицы, сначала соедини таблицы шагом join_tables "
            "(или left_join_tables, чтобы не терять строки без пары) с условием ниже — "
            "после соединения колонки обеих таблиц доступны по своим именам:"
        )
    ]
    for item in related:
        shown = item.columns[:PROMPT_COLUMNS]
        rest = len(item.columns) - len(shown)
        tail = f" и ещё {rest}" if rest > 0 else ""
        lines.append(
            f"- {item.table}: колонки {', '.join(shown)}{tail}\n"
            f"  связь: {item.relation.describe()}\n"
            f'  соединение: {{"tool": "join_tables", "arguments": {{"right_table": '
            f'"{item.table}", "condition": "{item.relation.condition(table)}"}}}}'
        )
    return "\n".join(lines)


def table_score(db, table: str, question: str) -> int:
    """Сколько слов вопроса совпало с колонками, их названиями, именем и описанием таблицы."""
    from app.core.memory.profile_memory import ProfileMemory

    memory = ProfileMemory(db)
    titles = [row.title for row in memory.load_enrichment(table) if row.title]
    summary = memory.load_summary(table)
    text = " ".join([table, *db.columns(table), *titles, summary[0] if summary else ""])
    return matches(tokenize(question), tokenize(text.replace("_", " ")))


def pick_table(db, question: str) -> str | None:
    """Таблица под вопрос, когда в сессии таблица не выбрана (None — таблиц нет).

    Совпадение слов вопроса с колонками, названиями и описанием таблицы; при
    равенстве — таблица с большим числом связей (из неё дотянутся остальные).
    """
    tables = db.user_tables()
    if not tables:
        return None
    links = {table: len(load(db, table)) for table in tables}
    return max(tables, key=lambda table: (table_score(db, table, question), links[table]))
