"""Robust-парсинг вывода LLM в шаги (ResearchStep).

LLM не всегда соблюдает формат, поэтому парсер пробует несколько
стратегий: строгий JSON -> JSON внутри ```блока -> поимённый поиск
полей -> запасной вариант «один шаг без тула».
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

from app.models.research_step import ResearchStep


class StepParseError(ValueError):
    """Не удалось распарсить шаги даже всеми стратегиями."""


def parse_steps(raw: str) -> list[ResearchStep]:
    """Распарсить вывод LLM в список шагов (несколько стратегий)."""
    logger.info(">>> parse_steps() raw=%r (длина=%d)", raw, len(raw))
    for extractor in (_strict_json, _fenced_json, _field_regex):
        try:
            steps = extractor(raw)
        except (ValueError, TypeError):
            steps = []
        if steps:
            logger.info("<<< parse_steps() стратегия %s вернула %d шагов", extractor.__name__, len(steps))
            return steps
    logger.warning("parse_steps() все стратегии не сработали, fallback шаг")
    return [_fallback_step()]


def _strict_json(raw: str) -> list[ResearchStep]:
    logger.debug("_strict_json() пробуем json.loads на: %r", raw[:200])
    data = json.loads(raw)
    if not isinstance(data, dict):
        return []
    return [ResearchStep.model_validate(s) for s in data.get("steps", [])]


def _fenced_json(raw: str) -> list[ResearchStep]:
    logger.debug("_fenced_json() ищем JSON-блок в: %r", raw[:200])
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    return _strict_json(match.group(0)) if match else []


def _field_regex(raw: str) -> list[ResearchStep]:
    logger.debug("_field_regex() ищем поля через regex в: %r", raw[:200])
    steps: list[ResearchStep] = []
    for i, block in enumerate(re.findall(r"\{[^{}]*tool[^{}]*\}", raw)):
        tool = re.search(r'"tool"\s*:\s*"([^"]+)"', block)
        steps.append(ResearchStep(step_id=i, tool=tool.group(1) if tool else None))
    return steps


def _fallback_step() -> ResearchStep:
    """Запасной вариант: доверить шаг генерации кода в песочнице."""
    return ResearchStep(step_id=0, tool=None, needs_llm_codegen=True)
