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
from collections import Counter
from dataclasses import dataclass, field

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
PROMPT_TABLES = 5
# Самая длинная цепочка соединений: orders -> clients -> cities -> regions.
MAX_HOPS = 3
# Сколько таблиц показать планировщику, если по словам вопроса группу данных не выбрать.
CATALOG_TABLES = 6
# Слова вопроса короче этого не участвуют в выборе таблицы («в», «по»).
MIN_WORD = 3
# Сколько слов вопроса должно совпасть с другой группой данных, чтобы уйти к ней
# посреди сессии, если она не названа в вопросе по имени или названию таблицы.
SWITCH_WORDS = 2
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


@dataclass(frozen=True)
class Hop:
    """Шаг пути соединения: from_table.from_columns = to_table.to_columns."""

    relation: Relation
    from_table: str
    from_columns: tuple[str, ...]
    to_table: str
    to_columns: tuple[str, ...]


def _hop(relation: Relation, from_table: str) -> Hop:
    if from_table == relation.table:
        return Hop(
            relation, relation.table, relation.columns, relation.ref_table, relation.ref_columns
        )
    return Hop(relation, relation.ref_table, relation.ref_columns, relation.table, relation.columns)


def paths(relations: list[Relation], start: str, max_hops: int = MAX_HOPS) -> dict[str, list[Hop]]:
    """Кратчайшие пути от start до связанных таблиц (не длиннее max_hops переходов).

    Связь проходится в обе стороны: от фактов к справочнику и обратно.
    Пути образуют дерево: у каждой таблицы один предшественник, поэтому
    пути к нескольким таблицам складываются в одну цепочку соединений.
    """
    found: dict[str, list[Hop]] = {start: []}
    frontier = [start]
    for _ in range(max_hops):
        reached = []
        for table in frontier:
            for relation in relations:
                if table not in (relation.table, relation.ref_table):
                    continue
                other = relation.other(table)
                if other not in found:
                    found[other] = [*found[table], _hop(relation, table)]
                    reached.append(other)
        frontier = reached
    del found[start]
    return found


def component(relations: list[Relation], table: str) -> set[str]:
    """Таблица и все таблицы, связанные с ней ключами через любое число переходов."""
    return {table, *paths(relations, table, max_hops=len(relations))}


def join_hops(relations: list[Relation], start: str, tables: list[str]) -> list[Hop]:
    """Соединения, которые доводят start до всех tables, в порядке выполнения.

    ValueError — таблица не связана со start ключами.
    """
    routes = paths(relations, start)
    hops: list[Hop] = []
    joined = {start}
    for table in tables:
        if table in joined:
            continue
        if table not in routes:
            linked = ", ".join(routes) or "нет"
            raise ValueError(
                f"таблица «{table}» не связана с «{start}» ключами (связанные таблицы: {linked})"
            )
        for hop in routes[table]:
            if hop.to_table not in joined:
                hops.append(hop)
                joined.add(hop.to_table)
    return hops


def output_names(db, relations: list[Relation], start: str) -> dict[str, dict[str, str]]:
    """Имена колонок связанных таблиц после join_related: {таблица: {колонка: имя}}.

    Имя, которое есть в нескольких таблицах группы (name у городов и регионов),
    получает приставку с именем таблицы: cities_name, regions_name. Правило не
    зависит от того, какие таблицы соединены в шаге, поэтому подсказка
    планировщику показывает те же имена, что получатся в данных.
    """
    group = sorted(component(relations, start))
    columns = {table: db.columns(table) for table in group}
    counts = Counter(name.lower() for names in columns.values() for name in names)
    return {
        table: {name: f"{table}_{name}" if counts[name.lower()] > 1 else name for name in names}
        for table, names in columns.items()
        if table != start
    }


def row_count(db, table: str) -> int:
    from app.core.memory.profile_memory import ProfileMemory

    profile = ProfileMemory(db).load_profile(table)
    return profile.row_count if profile is not None else 0


def references(relations: list[Relation], table: str) -> list[str]:
    """Таблицы, на которые table ссылается своими ключами (справочники таблицы)."""
    return list(
        dict.fromkeys(r.ref_table for r in relations if r.table == table and r.ref_table != table)
    )


def is_reference(relations: list[Relation], table: str) -> bool:
    """Справочник — таблица, на которую ссылаются другие (клиенты, города, регионы)."""
    return any(r.ref_table == table and r.table != table for r in relations)


def priority_order(db, tables, relations: list[Relation] | None = None) -> list[str]:
    """Таблицы по важности данных: сначала факты, затем справочники.

    Факты — таблицы, на которые никто не ссылается (заказы); справочники — те,
    на которые ссылаются (клиенты, города, регионы). Внутри группы выше
    таблица с большим числом строк: в цепочке справочников это ближние к фактам.
    """
    relations = load(db) if relations is None else relations
    rows = {table: row_count(db, table) for table in tables}
    return sorted(
        tables,
        key=lambda table: (is_reference(relations, table), -rows[table], table),
    )


def role(relations: list[Relation], table: str) -> str:
    return "справочник" if is_reference(relations, table) else "факты"


def table_title(db, table: str) -> str | None:
    """Название таблицы для пользователя (display_name паспорта, #211) или None."""
    from app.core.memory.profile_memory import ProfileMemory

    title = ProfileMemory(db).load_display_name(table)
    return (title.strip() or None) if title else None


@dataclass
class Related:
    """Таблица, связанная с текущей (напрямую или через другие), для промпта планировщика.

    columns — колонки так, как они будут названы после join_related.
    """

    table: str
    path: list[Hop]
    columns: list[str]
    score: int


def related_tables(db, table: str, question: str, limit: int = PROMPT_TABLES) -> list[Related]:
    """Таблицы, связанные с table ключами (до MAX_HOPS переходов), по словам вопроса.

    Без вызова LLM: вопрос только упорядочивает таблицы и колонки; при равенстве
    выше таблицы ближе по цепочке.
    """
    from app.core.memory.profile_memory import ProfileMemory

    memory = ProfileMemory(db)
    words = question_words(question)
    links = load(db)
    renamed = output_names(db, links, table)
    related = []
    for other, path in paths(links, table).items():
        profile = memory.load_profile(other)
        names = (
            [column.name for column in profile.columns]
            if profile is not None
            else db.columns(other)
        )
        titles = {row.column: row.title for row in memory.load_enrichment(other) if row.title}
        ranked = rank_columns(question, names, profile, titles)
        text = " ".join([other, table_title(db, other) or "", *names, *titles.values()])
        score = matches(words, tokenize(text.replace("_", " ")))
        # Ключи, по которым таблица присоединяется, в данные не попадают.
        shown = [renamed[other][name] for name in ranked if name not in path[-1].to_columns]
        related.append(Related(other, path, shown, score))
    related.sort(key=lambda item: (-item.score, len(item.path), item.table))
    return related[:limit]


def _columns_line(columns: list[str]) -> str:
    shown = columns[:PROMPT_COLUMNS]
    rest = len(columns) - len(shown)
    return ", ".join(shown) + (f" и ещё {rest}" if rest > 0 else "")


def describe_related(table: str, related: list[Related]) -> str:
    """Блок промпта: связанные таблицы, их колонки, пути по ключам и пример join_related."""
    if not related:
        return ""
    lines = [
        (
            f"Связанные таблицы (текущая таблица $source — {table}). Колонки связанной "
            "таблицы появляются после шага join_related: перечисли в tables нужные "
            "таблицы, путь и условия соединения — в том числе через промежуточные "
            "таблицы — инструмент найдёт сам по ключам ниже. Колонки названы так, как "
            "они будут названы после соединения (имя, которое есть в нескольких "
            "таблицах, — с приставкой таблицы: regions_name). Соединяй до group_by и "
            "select_columns, пока в данных есть ключи:"
        )
    ]
    for item in related:
        via = [hop.to_table for hop in item.path[:-1]]
        through = f" (через {', '.join(via)})" if via else ""
        lines.append(f"- {item.table}{through}: колонки {_columns_line(item.columns)}")
        lines.extend(f"  связь: {hop.relation.describe()}" for hop in item.path)
    lines.append(
        f'Пример: {{"tool": "join_related", "arguments": {{"tables": ["{related[0].table}"]}}}}'
    )
    return "\n".join(lines)


def describe_linked(db, table: str, related: list[Related]) -> str:
    """Блок промпта идей и плана дашборда: связанные таблицы и их колонки, без инструментов."""
    if not related:
        return ""
    lines = [
        (
            f"Связанные таблицы — соединяются с {table} по ключам, их колонки тоже "
            "можно брать в плитки:"
        )
    ]
    for item in related:
        via = [hop.to_table for hop in item.path[:-1]]
        through = f" (через {', '.join(via)})" if via else ""
        title = table_title(db, item.table)
        named = f" «{title}»" if title else ""
        lines.append(f"- {item.table}{named}{through}: колонки {_columns_line(item.columns)}")
    return "\n".join(lines)


def question_words(question: str) -> set[str]:
    """Слова вопроса для выбора таблицы: без коротких («в», «по»), они есть везде."""
    return {word for word in tokenize(question) if len(word) >= MIN_WORD}


def table_words(db, table: str) -> set[str]:
    """Чем таблица названа: имя, название (#211), колонки и их названия.

    Описание таблицы не берётся: в нём много общих слов, и оно перевешивало
    колонки (вопрос про заказы уходил в таблицу, чьё описание упоминает регионы).
    """
    from app.core.memory.profile_memory import ProfileMemory

    memory = ProfileMemory(db)
    titles = [row.title for row in memory.load_enrichment(table) if row.title]
    text = " ".join([table, table_title(db, table) or "", *db.columns(table), *titles])
    return tokenize(text.replace("_", " "))


@dataclass
class TableChoice:
    """Как ответить на вопрос без выбранной таблицы.

    table — таблица хода (None — спросить пользователя или таблиц нет);
    catalog — таблицы, из которых выберет планировщик; options — варианты
    уточнения у пользователя: название таблицы -> таблица.
    """

    table: str | None = None
    catalog: list[str] = field(default_factory=list)
    options: dict[str, str] = field(default_factory=dict)


@dataclass
class _Groups:
    """Группы связанных таблиц и их совпадение со словами вопроса."""

    words: set[str]
    named: dict[str, set[str]]
    links: list[Relation]
    groups: list[list[str]]
    scores: list[int]

    def leader(self, group: list[str]) -> str:
        """Самая подходящая таблица группы; при равенстве — факты (priority_order)."""
        return max(
            group, key=lambda name: (matches(self.words, self.named[name]), -group.index(name))
        )


def _groups(db, question: str) -> _Groups | None:
    tables = db.user_tables()
    if not tables:
        return None
    links = load(db)
    words = question_words(question)
    named = {table: table_words(db, table) for table in tables}
    order = priority_order(db, tables, links)
    groups: list[list[str]] = []
    for table in order:
        if not any(table in group for group in groups):
            linked = component(links, table)
            groups.append([name for name in order if name in linked])
    scores = [matches(words, set().union(*(named[name] for name in group))) for group in groups]
    return _Groups(words, named, links, groups, scores)


def _pick(db, found: _Groups, tied: list[list[str]]) -> TableChoice:
    """Одна группа — её таблица; несколько — уточнение по названиям или каталог."""
    leaders = [found.leader(group) for group in tied]
    if len(leaders) == 1:
        return TableChoice(table=leaders[0])
    leaders = leaders[:CATALOG_TABLES]
    titles = {table: table_title(db, table) for table in leaders}
    if all(titles.values()) and len(set(titles.values())) == len(leaders):
        return TableChoice(options={titles[table]: table for table in leaders})
    rest = [name for group in tied for name in group if name not in leaders]
    return TableChoice(table=leaders[0], catalog=[*leaders, *rest][:CATALOG_TABLES])


def choose_table(db, question: str) -> TableChoice:
    """Таблица под вопрос, когда в сессии таблица не выбрана.

    Без LLM. Вопрос сравнивается с группами таблиц, связанных ключами: «заказы
    по регионам» — одна группа (заказы -> клиенты -> города -> регионы), хотя
    слова разошлись по разным таблицам. В лучшей группе берётся таблица с
    наибольшим совпадением (при равенстве — факты, priority_order), остальные
    дотянет join_related.

    Неоднозначность — когда равны несвязанные группы. Тогда, если у главной
    таблицы каждой группы есть название (display_name), спрашиваем
    пользователя, какие данные взять; технические имена таблиц не показываем —
    без названий таблицу выберет планировщик по каталогу.
    """
    found = _groups(db, question)
    if found is None:
        return TableChoice()
    best = max(found.scores)
    return _pick(db, found, [g for g, score in zip(found.groups, found.scores) if score == best])


def switch_table(db, table: str, question: str) -> TableChoice | None:
    """Вопрос посреди сессии явно про другие, несвязанные данные: куда перейти.

    None — остаёмся на таблице сессии. Остаёмся, если вопрос совпал с её группой
    хоть одним словом или ни с чем: это продолжение («а теперь только VIP»,
    «покажи по месяцам»). Уходим к другой группе, только если она названа в
    вопросе по имени или названию таблицы («а теперь погода») либо совпала
    SWITCH_WORDS словами: одно слово колонки («по месяцам») есть во многих
    таблицах, а ложный переход хуже, чем остаться. Несколько равных групп —
    уточнение по названиям; без названий остаёмся.
    """
    found = _groups(db, question)
    if found is None:
        return None
    current = component(found.links, table)
    others = []
    for group, score in zip(found.groups, found.scores):
        if current & set(group):
            if score:
                return None
            continue
        named = any(
            matches(
                found.words, tokenize(f"{name} {table_title(db, name) or ''}".replace("_", " "))
            )
            for name in group
        )
        if named or score >= SWITCH_WORDS:
            others.append((group, score))
    if not others:
        return None
    best = max(score for _, score in others)
    choice = _pick(db, found, [group for group, score in others if score == best])
    return None if choice.catalog else choice


def describe_catalog(db, tables: list[str], question: str) -> str:
    """Блок промпта: таблица не выбрана — каталог таблиц, их колонки и связи."""
    lines = [
        (
            "Таблица для вопроса не выбрана, по словам вопроса подходят несколько. "
            "Выбери таблицу с нужными колонками и укажи её имя в input первого шага "
            "(вместо $source); если нужны колонки связанных таблиц — добавь их шагом "
            "join_related по связям. Таблицы:"
        )
    ]
    for table in tables:
        names = db.columns(table)
        lines.append(f"- {table}: колонки {_columns_line(rank_columns(question, names))}")
        lines.extend(
            f"  связь: {relation.describe()}"
            for relation in load(db, table)
            if relation.table == table
        )
    return "\n".join(lines)
