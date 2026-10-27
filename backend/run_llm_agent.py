#!/usr/bin/env python3
"""
Ручной запуск агентного цикла с реальной LLM.

Предварительно:
1. Запустить Ollama: ollama serve
2. Убедиться, что модель доступна: ollama list
3. Указать правильную модель в app/config.py (llm_model)

Запуск:
    USE_FAKE_LLM=false python3 run_llm_agent.py
"""

import asyncio
import sys
import time

sys.path.insert(0, "/home/terrum/Desktop/Jupyter/sberAnalyticalAgent/backend")

from app.api.schemas.chat import ChatRequest
from app.config import settings
from app.core.agent.insight import InsightGenerator
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.planner import StepPlanner
from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.client import LLMClient


def print_banner():
    print("=" * 60)
    print("  Агентный цикл с реальной LLM")
    print("=" * 60)
    print(f"  Модель: {settings.llm_model}")
    print(f"  Endpoint: {settings.llm_base_url}")
    print(f"  USE_FAKE_LLM: {settings.__dict__.get('USE_FAKE_LLM', 'not set')}")
    print("=" * 60)


async def main():
    print_banner()

    # Подключаемся к существующей in-memory БД или создаём новую
    db = DuckDBManager(path=":memory:")
    db.execute(
        """
        CREATE TABLE raw AS
        SELECT *
        FROM (
            VALUES
                ('Электроника', 100000, '2024-01-01'),
                ('Электроника', 150000, '2024-02-01'),
                ('Одежда', 50000, '2024-01-01'),
                ('Одежда', 75000, '2024-02-01'),
                ('Продукты', 200000, '2024-01-01'),
                ('Продукты', 180000, '2024-02-01')
        ) AS t(category, revenue, date)
        """
    )
    print("\n[*] База данных готова: таблица 'raw' с 6 строками\n")

    # Создаём LLM клиент (реальный, если USE_FAKE_LLM != true)
    t0 = time.time()
    llm = LLMClient()
    t1 = time.time()
    print(f"[*] LLMClient создан за {t1 - t0:.2f}s, модель: {llm.model}\n")

    # Проверка подключения к LLM
    print("[*] Проверка подключения к LLM...\n")
    try:
        test_resp = await llm.complete(
            system="Ты полезный ассистент.",
            user="Скажи просто: привет",
        )
        print(f"    LLM отвечает: {test_resp[:80]}...\n")
    except Exception as e:  # noqa: BLE001
        print(f"    [ERROR] LLM недоступен: {e}\n")
        print("\n[!] Убедись, что Ollama запущен и модель доступна:\n")
        print("    ollama serve\n    ollama pull qwen2.5vl:7b\n")
        return

    # Создаём оркестратор
    orch = AgentOrchestrator(
        planner=StepPlanner(llm=llm),
        executor=StepExecutor(db=db),
        insight_gen=InsightGenerator(llm=llm, db=db),
        db=db,
    )

    # Основной цикл вопросов
    print("\n[*] Введите вопрос (или 'exit' для выхода):\n")
    while True:
        try:
            question = input("> ").strip()
        except EOFError:
            break
        if not question or question.lower() in ("exit", "quit", "q"):
            break

        print(f'\n[*] Обработка вопроса: "{question}"\n')
        print("-" * 60)

        start = time.time()
        event_count = 0
        try:
            async for event in orch.run(
                ChatRequest(session_id="manual-session", question=question)
            ):
                event_count += 1
                etype = event["type"]
                data = event["data"]

                if etype == "text":
                    print(f"\n[TEXT] {data.get('headline', '')}\n")
                    for d in data.get("details", []):
                        print(f"       - {d}")
                elif etype == "chart":
                    print(f"\n[CHART] {data.get('caption', '')}\n")
                    spec = data.get("spec", {})
                    print(f"       Тип графика: {spec.get('mark', 'неизвестно')}")
                elif etype == "dashboard":
                    print(f"\n[DASHBOARD] ID: {data.get('dashboard_id', '')}\n")
                elif etype == "error":
                    print(f"\n[ERROR] Шаг {data.get('step', '?')}: {data.get('message', '')}\n")
                elif etype == "done":
                    print("\n[DONE] Цикл завершён\n")

                print("-" * 60)
        except Exception as e:  # noqa: BLE001
            print(f"\n[ERROR] Исключение при выполнении: {e}\n")
            import traceback

            traceback.print_exc()

        elapsed = time.time() - start
        print(f"\n[*] Готово за {elapsed:.2f}s, событий: {event_count}\n")

    print("\n[*] Завершение работы.\n")


if __name__ == "__main__":
    asyncio.run(main())
