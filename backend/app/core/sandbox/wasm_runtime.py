"""WASM-песочница для кода, сгенерированного LLM.

Вместо Docker (слишком тяжёлого для локального запуска): WASM-рантайм
с лимитами CPU/RAM и жёстким таймаутом. Замкнутый контур — без сети.
"""

import logging

logger = logging.getLogger(__name__)

from app.config import settings


class WASMSandbox:
    """Исполнение кода агента в изолированном WASM-окружении."""

    def __init__(self, timeout_s: int | None = None, mem_mb: int | None = None) -> None:
        self.timeout_s = timeout_s or settings.wasm_timeout_s
        self.mem_mb = mem_mb or settings.wasm_mem_mb

    def run(self, code: str, payload: dict | None = None) -> dict:
        """Выполнить код с лимитами ресурсов и таймаутом.

        STUB IMPLEMENTATION: Возвращает payload как есть (pass-through).
        TODO(round-2): поднять WASM-рантайм (wasmtime-py / Pyodide-мост),
        передать payload, ограничить CPU/RAM/таймаут, вернуть результат.
        """
        logger.warning("WASM sandbox — STUB: код не исполняется, возвращается исходные данные")
        
        return {"result": payload.get("data", []) if payload else [], "status": "stub"}
