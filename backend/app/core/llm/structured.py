"""Толерантный разбор structured-ответа LLM в Pydantic-схему.

Прототип (docs/reports/llm_perf/hypotheses.md, H-P1..H-P4): один ответ
модели стоит минуты на CPU, поэтому выбрасывать его из-за формы обёртки
дороже, чем починить. Порядок:

1. ``loads_lenient`` — достать JSON из текста: снять ```-ограждения,
   найти сбалансированный объект/массив (с учётом строк), починить
   висячие запятые, комментарии, Python-литералы True/False/None.
2. ``coerce`` — привести найденное к форме схемы: снять обёртку
   ``{"ResearchPlan": {...}}``, список шагов -> ``{"steps": [...]}``,
   один шаг -> план из одного шага, несколько tool_calls -> слить шаги.
3. Схема валидируется как обычно; ошибку валидации можно вернуть модели
   текстом (``repair_prompt``) — это дешевле, чем менять режим запроса.
"""

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.DOTALL)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_PY_LITERALS = {"True": "true", "False": "false", "None": "null"}


class StructuredParseError(ValueError):
    """Ни одна стратегия не дала объект, проходящий схему."""


def loads_lenient(text: str) -> Any:
    """JSON из ответа модели или ValueError.

    Пробует по очереди: текст как есть, содержимое ```-блоков, каждый
    сбалансированный {...}/[...] — и для каждого кандидата ещё и
    починенную версию.
    """
    candidates = [text.strip()]
    candidates += [block.strip() for block in _FENCE.findall(text)]
    candidates += list(_balanced_chunks(text))
    for candidate in candidates:
        for variant in (candidate, _repair(candidate)):
            try:
                return json.loads(variant)
            except (json.JSONDecodeError, ValueError):
                continue
    raise ValueError("в ответе нет разбираемого JSON")


def _balanced_chunks(text: str):
    """Сбалансированные {...} и [...] верхнего уровня, скобки в строках не считаются."""
    stack: list[str] = []
    start = None
    in_string = escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = stack != []
            continue
        if ch in "{[":
            if not stack:
                start = i
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            if ch != stack[-1]:
                stack, start = [], None
                continue
            stack.pop()
            if not stack and start is not None:
                yield text[start : i + 1]
                start = None
    if stack and start is not None:
        # Ответ оборван (лимит токенов): пробуем закрыть скобки.
        yield text[start:] + "".join(reversed(stack))


def _repair(text: str) -> str:
    """Частые огрехи JSON у LLM: комментарии, висячие запятые, Python-литералы."""
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = _TRAILING_COMMA.sub(r"\1", text)
    return re.sub(
        r'("(?:[^"\\]|\\.)*")|\b(True|False|None)\b',
        lambda m: m.group(1) or _PY_LITERALS[m.group(2)],
        text,
    )


def coerce(schema: type[BaseModel], data: Any) -> Any:
    """Привести разобранный JSON к форме схемы (не валидируя её)."""
    fields = set(schema.model_fields)
    if "steps" in fields and isinstance(data, dict) and "steps" not in data and "tool" in data:
        return {"steps": [data]}
    # {"ResearchPlan": {...}} / {"name": "ResearchPlan", "arguments": {...}}
    if isinstance(data, dict) and not (fields & set(data)):
        if len(data) == 1 and isinstance(next(iter(data.values())), dict | list):
            return coerce(schema, next(iter(data.values())))
        for key in ("arguments", "parameters"):
            if isinstance(data.get(key), dict | str):
                inner = data[key]
                return coerce(schema, loads_lenient(inner) if isinstance(inner, str) else inner)
    if "steps" in fields:
        if isinstance(data, list):
            return {"steps": [_step(item) for item in data]}
    elif isinstance(data, list) and data:
        # Схема — один объект, а пришёл список: берём первый подходящий.
        return data[0]
    return data


def _step(item: Any) -> Any:
    """Элемент списка шагов: tool_call {"name":..,"arguments":..} -> шаг."""
    if isinstance(item, dict) and "tool" not in item and isinstance(item.get("arguments"), dict):
        inner = item["arguments"]
        if "tool" in inner:
            return inner
        return {"tool": item.get("name"), "arguments": inner}
    return item


def merge_tool_calls(schema: type[T], calls: list[tuple[str, str]]) -> T:
    """Несколько tool_calls вместо одного (Qwen так делает) -> один объект схемы.

    calls — (имя функции, arguments JSON). Для плана шаги всех вызовов
    сливаются по порядку; для прочих схем берётся первый валидный.
    """
    parsed = [(fn, loads_lenient(args) if isinstance(args, str) else args) for fn, args in calls]
    if "steps" in schema.model_fields:
        steps: list = []
        meta: dict = {}
        for fn, args in parsed:
            if fn == schema.__name__ and isinstance(args, dict) and "steps" in args:
                steps.extend(args["steps"])
                meta |= {k: v for k, v in args.items() if k != "steps"}
            elif isinstance(args, dict):
                # Вызов «тула» шага напрямую: имя функции = имя инструмента.
                steps.append(args if "tool" in args else {"tool": fn, "arguments": args})
        return schema.model_validate({**meta, "steps": steps})
    errors = []
    for _, args in parsed:
        try:
            return schema.model_validate(coerce(schema, args))
        except ValidationError as exc:
            errors.append(str(exc))
    raise StructuredParseError("; ".join(errors) or "нет tool_calls")


def parse_structured(schema: type[T], text: str) -> T:
    """Текст ответа -> объект схемы (StructuredParseError с причиной)."""
    try:
        data = loads_lenient(text)
    except ValueError as exc:
        raise StructuredParseError(str(exc)) from exc
    try:
        return schema.model_validate(coerce(schema, data))
    except ValidationError as exc:
        raise StructuredParseError(str(exc)) from exc


REPAIR_PROMPT = """

Твой предыдущий ответ не подошёл по формату:
{answer}

Ошибка разбора: {error}
Верни исправленный ответ — только JSON-объект по схеме, без пояснений."""


def repair_prompt(answer: str, error: Exception, limit: int = 1500) -> str:
    """Добавка к промпту для переспроса: ответ модели и причина отказа."""
    return REPAIR_PROMPT.format(answer=answer[:limit], error=str(error)[:500])
