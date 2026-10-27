#!/usr/bin/env python3
"""
Проверка LLMClient с НАСТОЯЩЕЙ моделью (Qwen через Ollama).

Ручная проверка, не входит в pytest. Запуск из корня репозитория:
    python3 scripts/check_llm_client.py [CHARS]

CHARS — длина промпта для проверки контекста (по умолчанию 12000, как у
промпта планировщика без колонок).

Требования:
    - ollama serve запущен
    - модель доступна: ollama list | grep qwen2.5-coder:7b
    - .env с llm_base_url=http://localhost:11434/v1, llm_model=qwen2.5-coder:7b
"""

import asyncio
import os
import sys
from pathlib import Path

os.environ.pop("USE_FAKE_LLM", None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import settings
from app.core.llm import budget
from app.core.llm.client import LLMClient
from app.models.research_step import ResearchPlan, ResearchStep


async def main() -> None:
    llm = LLMClient()
    print(f"LLM type: {type(llm).__name__}")
    print(f"Model: {llm.model}")
    print(f"Base URL: {llm._client.base_url}")

    # 1. Простой текстовый completion
    print("\n--- Test 1: plain text completion ---")
    text = await llm.complete(
        system="Ты полезный ассистент.",
        user="Скажи просто: привет",
    )
    print(f"Text: {text[:100]}")

    # 2. Длина контекста: доходит ли длинный промпт до модели целиком
    chars = int(sys.argv[1]) if len(sys.argv) > 1 else budget.PROBE_CHARS
    print(f"\n--- Test 2: context, prompt {chars} chars ---")
    probe = await budget.probe_context(llm, chars)
    for key, value in probe.items():
        print(f"{key}: {value}")
    if probe["truncated"] or not probe["password_seen"]:
        print(
            f"✗ Промпт обрезан: поднимите контекст сервера до LLM_NUM_CTX={settings.llm_num_ctx} "
            "(Ollama — OLLAMA_CONTEXT_LENGTH, vLLM — --max-model-len)"
        )
        sys.exit(1)

    # 3. Structured output -> ResearchPlan
    print("\n--- Test 3: structured output (ResearchPlan) ---")
    plan = await llm.complete_structured(
        system="Ты — BI-аналитик.",
        user="Составь план анализа данных по продажам по категориям.",
        schema=ResearchPlan,
    )
    print(f"Plan: {plan.model_dump()}")

    # 4. Structured output -> ResearchStep
    print("\n--- Test 4: structured output (ResearchStep) ---")
    step = await llm.complete_structured(
        system="Ты — BI-аналитик.",
        user="Опиши один шаг агрегации данных по колонке category.",
        schema=ResearchStep,
    )
    print(f"Step: {step.model_dump()}")

    print("\n✓ Все проверки LLMClient пройдены")


if __name__ == "__main__":
    asyncio.run(main())
