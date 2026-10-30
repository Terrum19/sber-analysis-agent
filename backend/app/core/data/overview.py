"""«Что передо мной?»: описание таблицы от LLM, закэшированное в паспорте.

describe_dataset() собирает из паспорта короткий промпт SCHEMA_DESCRIBE и
вызывает LLM, только если промпт изменился с прошлого раза: отпечаток
промпта хранится рядом с описанием в _meta.table_passport. Поменялись
профиль, названия колонок или сам промпт — поменяется и отпечаток.
Факты (строки, колонки, период) и таблица колонок строятся из профиля
без LLM, поэтому числа в ответе точные.

Тот же вызов даёт и название таблицы для пользователя (display_name):
LLM отвечает JSON DatasetDescription. Не разобрался JSON — описание
просится ещё раз простым текстом, уже без названия. Ключевые колонки
(key_columns) и подсказки к вопросам (starter_questions) — из профиля
и названий колонок, без LLM.

Промпт помещается в контекст CONTEXT_TOKENS (по умолчанию у Ollama) даже
при CHARS_PER_TOKEN символа на токен: PROMPT_MAX_CHARS — это контекст за
вычетом ответа (ANSWER_MAX_TOKENS) и запаса. Однотипные колонки (Q1A…Q20A)
идут одной строкой; замечания о качестве — по строке на вид замечания со
списком колонок («пропуски: «A» (10%), «B» (15%)»). Остальное обрезается
с пометкой «… и ещё N».
"""

import hashlib
import logging
import re
from datetime import datetime

from app.config import settings
from app.core.data.profiling import (
    ColumnStats,
    TableProfile,
    is_identifier_name,
    profile_table,
    quality_notes,
)
from app.core.data.semantic_select import column_facts, describe_columns
from app.core.llm.client import LLMClient
from app.core.llm.prompts.schema_describe import (
    SCHEMA_DESCRIBE,
    SCHEMA_DESCRIBE_JSON,
    SCHEMA_DESCRIBE_SYSTEM,
)
from app.core.memory.profile_memory import ProfileMemory
from app.models.enrichment import ColumnEnrichment, DatasetDescription

logger = logging.getLogger(__name__)

CONTEXT_TOKENS = 4096
ANSWER_MAX_TOKENS = 600
RESERVE_TOKENS = 400
CHARS_PER_TOKEN = 1.5
PROMPT_MAX_CHARS = int((CONTEXT_TOKENS - ANSWER_MAX_TOKENS - RESERVE_TOKENS) * CHARS_PER_TOKEN)
QUALITY_MAX_CHARS = 1000
FAMILY_MIN_COLUMNS = 4
TOP_VALUES = 5
DISPLAY_NAME_MAX_CHARS = 60
KEY_COLUMNS = 5
CATEGORY_MAX_DISTINCT = 50
OVERVIEW_QUESTION = "Что передо мной?"

KIND_LABELS = {
    "numeric": "число",
    "temporal": "дата",
    "text": "текст",
    "identifier": "код",
    "other": "другое",
}
COLUMN_WORDS = ("колонка", "колонки", "колонок")
ROW_WORDS = ("строка", "строки", "строк")
NOTE_WORDS = ("замечание", "замечания", "замечаний")

_NOTE = re.compile(
    r"(?P<kind>.+?)(?: (?P<share>\d[\d.]*%)| \((?P<mode>.*)\)| — (?P<count>\d+)|: (?P<number>\d+))"
)
KIND_TITLES = (("пропусков", "пропуски"), ("константа", "константы"), ("выбросов", "выбросы"))
_DATA = r"(?:эти |этих |этой |этом )?(?:данн\w*|таблиц\w*|датасет\w*|набор\w* данных|файл\w*)"
_OVERVIEW = re.compile(
    "|".join(
        (
            r"что (?:это )?передо мной",
            r"что (?:это(?: такое)?|(?:здесь|тут)(?: есть| лежит)?)",
            rf"что (?:это )?за {_DATA}",
            rf"что (?:есть |лежит |содержится )?в {_DATA}",
            rf"что содерж\w* {_DATA}",
            rf"(?:опиши|описание|расскажи (?:о|об|про)) {_DATA}",
            rf"за какой период(?: (?:собраны )?(?:они|{_DATA}))?",
            rf"какой период(?: охватыва\w*| покрыва\w*)?(?: {_DATA})?",
            (
                rf"сколько (?:в {_DATA} |всего |их )?(?:записей|строк|колонок|столбцов|полей)"
                rf"(?: в {_DATA}| всего| в ней| там)?"
            ),
            r"сколько их",
            rf"какие (?:есть )?(?:колонки|столбцы|поля)(?: есть)?(?: в {_DATA})?",
        )
    )
)
_FILLER = re.compile(r"(?:расскажи|скажи|подскажи|покажи)(?: мне)?|пожалуйста|привет")
_SEPARATOR = re.compile(r"\s*(?:[,;:?!.]|\bи\b|\bа\b)\s*")


def is_overview_question(question: str) -> bool:
    """Вопрос целиком про таблицу: «что передо мной?», «сколько строк и за какой период?».

    Любое уточнение сверх обзорных фраз («сколько строк по Москве?») —
    уже вопрос к данным, его решает планировщик.
    """
    text = " ".join(question.lower().replace("ё", "е").split())
    parts = [part for part in _SEPARATOR.split(text) if part]
    return any(_OVERVIEW.fullmatch(part) for part in parts) and all(
        _OVERVIEW.fullmatch(part) or _FILLER.fullmatch(part) for part in parts
    )


def passport(db, table: str) -> TableProfile:
    """Профиль из паспорта; паспорта нет — профилировать таблицу и сохранить."""
    memory = ProfileMemory(db)
    profile = memory.load_profile(table)
    if profile is None:
        with db.lock:
            profile = profile_table(db, table, approx=settings.profile_approx_quantile)
            memory.save_profile(profile)
    return profile


def plural(count: int, words: tuple[str, str, str]) -> str:
    """«1 строка», «3 строки», «5 строк»."""
    if count % 10 == 1 and count % 100 != 11:
        word = words[0]
    elif 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        word = words[1]
    else:
        word = words[2]
    return f"{count:,}".replace(",", " ") + f" {word}"


def _value(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, float):
        return f"{value:g}"
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def facts(profile: TableProfile) -> str:
    """Объём и период: «raw: 6 строк, 3 колонки, данные с 2024-01-01 по 2024-02-01 («date»)».

    Период — по TableProfile.temporal_column, как в TableBrief.from_profile.
    """
    text = f"{profile.table}: {plural(profile.row_count, ROW_WORDS)}, "
    text += plural(profile.column_count, COLUMN_WORDS)
    temporal = profile.temporal_column()
    if temporal is None or temporal.min_date is None:
        return f"{text}, колонок с датами нет — период не определить"
    return (
        f"{text}, данные с {_value(temporal.min_date)} по {_value(temporal.max_date)} "
        f"(«{temporal.name}»)"
    )


def family_key(column: ColumnStats) -> tuple[str | None, str, str]:
    """Шаблон имени (цифры → #), вид и тип: Q3A и Q17A — одно семейство Q#A."""
    pattern = re.sub(r"\d+", "#", column.name)
    return (pattern if pattern != column.name else None), column.kind, column.dtype


def family_line(members: list[ColumnStats]) -> str:
    """Одна строка на семейство: «- Q1A…Q20A (колонок: 20) [numeric, BIGINT]: от 1 до 5»."""
    first = members[0]
    line = (
        f"- {first.name}…{members[-1].name} (колонок: {len(members)}) [{first.kind}, {first.dtype}]"
    )
    lows = [column.min for column in members if column.min is not None]
    highs = [column.max for column in members if column.max is not None]
    if first.kind in ("numeric", "temporal") and lows and highs:
        line += f": от {_value(min(lows))} до {_value(max(highs))}"
    return line


def families(profile: TableProfile) -> dict[str, list[ColumnStats]]:
    """Колонка → все колонки её семейства (от FAMILY_MIN_COLUMNS); одиночных колонок нет."""
    groups: dict[tuple, list[ColumnStats]] = {}
    for column in profile.columns:
        groups.setdefault(family_key(column), []).append(column)
    return {
        column.name: members
        for key, members in groups.items()
        if key[0] is not None and len(members) >= FAMILY_MIN_COLUMNS
        for column in members
    }


def profile_lines(
    profile: TableProfile, enrichment: list[ColumnEnrichment]
) -> list[tuple[str, int]]:
    """Строки профиля для промпта и сколько колонок в каждой (семейство — одна строка)."""
    titles = {row.column: row.title for row in enrichment if row.title}
    descriptions = {row.column: row.description for row in enrichment if row.description}
    grouped = families(profile)
    lines = []
    shown = set()
    for column in profile.columns:
        members = grouped.get(column.name)
        if members is not None:
            if members[0].name not in shown:
                shown.add(members[0].name)
                lines.append((family_line(members), len(members)))
            continue
        line = describe_columns([column.name], profile, descriptions, titles, TOP_VALUES)
        lines.append((line, 1))
    return lines


def split_note(text: str) -> tuple[str, str | None]:
    """Вид замечания и значение: «пропусков 15%» → («пропуски», «15%»)."""
    match = _NOTE.fullmatch(text)
    if match is None:
        return text, None
    kind = match["kind"]
    for prefix, title in KIND_TITLES:
        if kind.startswith(prefix):
            kind = title + kind[len(prefix) :]
    return kind, next((value for value in match.groups()[1:] if value is not None), None)


def grouped_notes(profile: TableProfile) -> tuple[list[str], dict[str, list[str]]]:
    """Замечания таблицы строками и замечания колонок по видам: вид → «колонка» (значение)."""
    prefixes = [f"«{column.name}»: " for column in profile.columns]
    table_notes = []
    groups: dict[str, list[str]] = {}
    for note in quality_notes(profile):
        prefix = next((prefix for prefix in prefixes if note.startswith(prefix)), None)
        if prefix is None:
            table_notes.append(f"- {note}")
            continue
        kind, value = split_note(note[len(prefix) :])
        name = prefix[: -len(": ")]
        groups.setdefault(kind, []).append(name if value is None else f"{name} ({value})")
    return table_notes, groups


def group_line(kind: str, names: list[str], room: int) -> str:
    """Строка вида замечания не длиннее room: все колонки, первые и «и ещё N» или только число."""
    for count in range(len(names), 0, -1):
        line = f"- {kind}: {', '.join(names[:count])}"
        if count < len(names):
            line += f" и ещё {plural(len(names) - count, COLUMN_WORDS)}"
        if len(line) <= room:
            return line
    return f"- {kind}: {plural(len(names), COLUMN_WORDS)}"


def quality_block(profile: TableProfile, limit: int = QUALITY_MAX_CHARS) -> str:
    """«Качество данных» не длиннее limit: по строке на вид замечания со списком колонок.

    «- пропуски: «Версия приложения» (15.3%), «Текст ошибки» (99.7%)».
    Каждому виду остаётся место хотя бы на число колонок («- константы:
    3 колонки»), остальное место идёт под имена по порядку видов.
    """
    table_notes, groups = grouped_notes(profile)
    if not table_notes and not groups:
        return "- замечаний нет"
    lines = list(table_notes)
    used = sum(len(line) + 1 for line in lines)
    shortest = [len(group_line(kind, names, 0)) + 1 for kind, names in groups.items()]
    for index, (kind, names) in enumerate(groups.items()):
        line = group_line(kind, names, limit - used - sum(shortest[index + 1 :]) - 1)
        lines.append(line)
        used += len(line) + 1
    block = "\n".join(lines)
    if len(block) > limit:
        return clip([(line, 1) for line in lines], limit, NOTE_WORDS)
    return block


def clip(lines: list[tuple[str, int]], limit: int, words: tuple[str, str, str]) -> str:
    """Строки, пока помещаются в limit символов; остальное — «- … и ещё N колонок»."""
    if len("\n".join(line for line, _ in lines)) <= limit:
        return "\n".join(line for line, _ in lines)
    total = sum(count for _, count in lines)
    room = limit - len(f"- … и ещё {plural(total, words)}") - 1
    kept = []
    used = 0
    for line, _ in lines:
        if used + len(line) + 1 > room:
            break
        kept.append(line)
        used += len(line) + 1
    rest = sum(count for _, count in lines[len(kept) :])
    return "\n".join([*kept, f"- … и ещё {plural(rest, words)}"])


def build_prompt(profile: TableProfile, enrichment: list[ColumnEnrichment]) -> str:
    """Промпт SCHEMA_DESCRIBE; с системным и SCHEMA_DESCRIBE_JSON — до PROMPT_MAX_CHARS."""
    quality = quality_block(profile)
    header = f"{facts(profile)}."
    fixed = (
        len(SCHEMA_DESCRIBE_SYSTEM)
        + len(SCHEMA_DESCRIBE.format(profile=f"{header}\n", quality=quality))
        + len(SCHEMA_DESCRIBE_JSON)
    )
    columns = clip(profile_lines(profile, enrichment), PROMPT_MAX_CHARS - fixed, COLUMN_WORDS)
    return SCHEMA_DESCRIBE.format(profile=f"{header}\n{columns}", quality=quality)


def fingerprint(prompt: str) -> str:
    text = f"{SCHEMA_DESCRIBE_SYSTEM}\n{prompt}\n{SCHEMA_DESCRIBE_JSON}"
    return hashlib.sha256(text.encode()).hexdigest()


def cached_summary(db, table: str) -> str | None:
    """Сохранённое описание, если оно написано по текущему паспорту; иначе None. Без LLM."""
    memory = ProfileMemory(db)
    saved = memory.load_summary(table)
    profile = None if saved is None else memory.load_profile(table)
    if profile is None:
        return None
    prompt = build_prompt(profile, memory.load_enrichment(table))
    return saved[0] if saved[1] == fingerprint(prompt) else None


async def describe_dataset(db, table: str, llm: LLMClient | None = None) -> str:
    """Описание таблицы «что передо мной»: из паспорта, LLM — только если промпт изменился.

    Вместе с описанием сохраняется название таблицы от LLM. Пустое описание
    не сохраняется. Ошибка LLM пробрасывается, сохранённое описание остаётся
    прежним.
    """
    memory = ProfileMemory(db)
    prompt = build_prompt(passport(db, table), memory.load_enrichment(table))
    key = fingerprint(prompt)
    saved = memory.load_summary(table)
    if saved is not None and saved[1] == key:
        return saved[0]
    llm = llm or LLMClient()
    try:
        answer = await llm.complete_structured(
            SCHEMA_DESCRIBE_SYSTEM, prompt + SCHEMA_DESCRIBE_JSON, DatasetDescription
        )
        text, name = answer.summary.strip(), display_name(answer.display_name)
    except ValueError:
        logger.warning("Название таблицы %s не разобрано, описание — простым текстом", table)
        text = await llm.complete(SCHEMA_DESCRIBE_SYSTEM, prompt, max_tokens=ANSWER_MAX_TOKENS)
        text, name = text.strip(), None
    if text:
        memory.save_summary(table, text, key, name)
    return text


def display_name(text: str) -> str | None:
    """Название от LLM без кавычек и лишних пробелов, не длиннее DISPLAY_NAME_MAX_CHARS."""
    name = " ".join(text.split()).strip("«»\"'. ")
    return name[:DISPLAY_NAME_MAX_CHARS].rstrip() or None


def column_label(column: ColumnStats, titles: dict[str, str]) -> str:
    return titles.get(column.name) or column.name


def is_category(column: ColumnStats) -> bool:
    """Текстовая колонка, по которой имеет смысл группировать."""
    return (
        column.kind == "text"
        and not column.is_unique
        and column.distinct is not None
        and 1 < column.distinct <= CATEGORY_MAX_DISTINCT
    )


def is_entity(column: ColumnStats) -> bool:
    """Код (число-идентификатор или текст с именем вроде user_id) или категория."""
    if column.kind == "identifier" or is_category(column):
        return True
    return column.kind == "text" and is_identifier_name(column.name)


def key_columns(
    profile: TableProfile, enrichment: list[ColumnEnrichment]
) -> tuple[list[str], list[str]]:
    """Сущности (коды и категории) и метрики (числа) — до KEY_COLUMNS каждых, без констант."""
    titles = {row.column: row.title for row in enrichment if row.title}
    columns = [column for column in profile.columns if not column.is_constant]
    entities = [column_label(column, titles) for column in columns if is_entity(column)]
    metrics = [column_label(column, titles) for column in columns if column.kind == "numeric"]
    return entities[:KEY_COLUMNS], metrics[:KEY_COLUMNS]


def starter_questions(profile: TableProfile, enrichment: list[ColumnEnrichment]) -> list[str]:
    """Подсказки к первому вопросу по колонкам таблицы: обзор, динамика, сравнение, топ."""
    titles = {row.column: row.title for row in enrichment if row.title}
    columns = [column for column in profile.columns if not column.is_constant]
    metric = next((column for column in columns if column.kind == "numeric"), None)
    category = next((column for column in columns if is_category(column)), None)
    temporal = profile.temporal_column()
    questions = [OVERVIEW_QUESTION]
    if temporal is not None and temporal.min_date != temporal.max_date:
        if metric is not None:
            questions.append(f"Как менялась «{column_label(metric, titles)}» по месяцам?")
        else:
            questions.append("Сколько записей по месяцам?")
    if category is not None:
        group = column_label(category, titles)
        if metric is not None:
            value = column_label(metric, titles)
            questions.append(f"Сравни «{value}» по «{group}»")
            questions.append(f"Топ-5 «{group}» по «{value}»")
        else:
            questions.append(f"Сколько записей по «{group}»?")
    return questions


def paragraphs(text: str) -> list[str]:
    """Описание по абзацам для details события text."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def profile_rows(profile: TableProfile, enrichment: list[ColumnEnrichment]) -> list[dict]:
    """Таблица колонок для графика: имя, название, тип, заполненность, уникальные, значения."""
    titles = {row.column: row.title for row in enrichment if row.title}
    rows = []
    for column in profile.columns:
        row = {"Колонка": column.name}
        if titles:
            row["Название"] = titles.get(column.name, "")
        row["Тип"] = KIND_LABELS.get(column.kind, column.kind)
        row["Заполнено, %"] = None if column.null_pct is None else round(100 - column.null_pct, 1)
        row["Уникальных"] = column.distinct
        row["Значения"] = column_facts(column, TOP_VALUES)
        rows.append(row)
    return rows
