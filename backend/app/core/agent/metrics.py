"""Метрики одного хода: этапы, вызовы LLM, токены и итоговый лог.

Метрики живут в ContextVar и доступны из LLM-клиента, планировщика,
исполнителя и оркестратора без передачи объекта через все слои. В
событие ``done`` и итоговую строку лога попадают route, llm_calls,
prompt_tokens и total_ms.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import settings

# Последняя задержка LLM в процессе: /api/llm/status обслуживается вне хода,
# где контекстные метрики пусты.
_last_latency_ms: int | None = None


@dataclass
class TurnMetrics:
    """Счётчики и тайминги одного хода диалога."""

    session_id: str
    turn_id: str
    question: str
    route: str = "complex"
    model: str = ""
    stages: dict[str, float] = field(default_factory=dict)
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    budget_exceeded: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    last_llm_latency_ms: int | None = None

    def record_llm(
        self, prompt_tokens: int, completion_tokens: int, latency_ms: int | None = None
    ) -> None:
        """Учесть вызов LLM и его токены."""
        global _last_latency_ms
        self.llm_calls += 1
        self.prompt_tokens += max(0, int(prompt_tokens or 0))
        self.completion_tokens += max(0, int(completion_tokens or 0))
        if latency_ms is not None:
            self.last_llm_latency_ms = latency_ms
            _last_latency_ms = latency_ms

    def record_stage(self, name: str, seconds: float) -> None:
        """Накопить время этапа (этап может выполняться несколько раз)."""
        self.stages[name] = round(self.stages.get(name, 0.0) + max(0.0, seconds), 3)

    def total_ms(self) -> int:
        """Длительность хода от создания метрик, мс."""
        return max(1, round((time.monotonic() - self.started_at) * 1000))

    def last_latency_ms(self) -> int | None:
        """Задержка последнего вызова LLM, мс."""
        return self.last_llm_latency_ms

    def as_log(self) -> dict[str, Any]:
        """Плоская запись для лога и метрик-хранилища."""
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "route": self.route,
            "model": self.model,
            "stages": dict(self.stages),
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_ms": self.total_ms(),
            "budget_exceeded": self.budget_exceeded,
        }


_metrics: ContextVar[TurnMetrics | None] = ContextVar("turn_metrics", default=None)


def current_metrics() -> TurnMetrics | None:
    """Метрики текущего хода (None вне хода)."""
    return _metrics.get()


def set_metrics(metrics: TurnMetrics | None) -> object:
    """Установить метрики в контекст; вернуть токен для reset_metrics."""
    return _metrics.set(metrics)


def reset_metrics(token: object) -> None:
    """Снять метрики, установленные set_metrics."""
    _metrics.reset(token)  # type: ignore[arg-type]


def last_latency_ms() -> int | None:
    """Задержка последнего ответа LLM в процессе (для /api/llm/status)."""
    current = current_metrics()
    if current is not None and current.last_llm_latency_ms is not None:
        return current.last_llm_latency_ms
    return _last_latency_ms


class MetricsStore:
    """SQLite-хранилище метрик ходов для /api/metrics и SLO-отчётов."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or settings.sqlite_path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=5)
        connection.execute(
            """CREATE TABLE IF NOT EXISTS turn_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                route TEXT,
                model TEXT,
                stages TEXT,
                llm_calls INTEGER NOT NULL DEFAULT 0,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_ms INTEGER NOT NULL DEFAULT 0,
                budget_exceeded TEXT,
                created_at REAL NOT NULL
            )"""
        )
        return connection

    def record(self, metrics: TurnMetrics) -> None:
        """Записать метрики хода; ошибки хранилища не должны ронять ход."""
        payload = metrics.as_log()
        try:
            connection = self._connect()
            try:
                connection.execute(
                    """INSERT INTO turn_metrics
                    (session_id, turn_id, route, model, stages, llm_calls, prompt_tokens,
                     completion_tokens, total_ms, budget_exceeded, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        payload["session_id"],
                        payload["turn_id"],
                        payload["route"],
                        payload["model"],
                        json.dumps(payload["stages"], ensure_ascii=False),
                        payload["llm_calls"],
                        payload["prompt_tokens"],
                        payload["completion_tokens"],
                        payload["total_ms"],
                        payload["budget_exceeded"],
                        time.time(),
                    ),
                )
                connection.commit()
            finally:
                connection.close()
        except Exception:
            import logging

            logging.getLogger(__name__).warning("метрики хода не сохранены", exc_info=True)

    def list_turns(self, limit: int = 100, session_id: str | None = None) -> list[dict]:
        connection = self._connect()
        try:
            if session_id:
                rows = connection.execute(
                    "SELECT * FROM turn_metrics WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM turn_metrics ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            columns = [
                description[0]
                for description in connection.execute(
                    "SELECT * FROM turn_metrics LIMIT 0"
                ).description
            ]
        finally:
            connection.close()
        result = []
        for row in rows:
            item = dict(zip(columns, row))
            item["stages"] = json.loads(item.get("stages") or "{}")
            result.append(item)
        return result

    def summary(self, days: int = 7) -> dict:
        """Агрегаты за период: количество, p50/p95, ошибки бюджета."""
        since = time.time() - max(1, days) * 86400
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT total_ms, llm_calls, prompt_tokens, budget_exceeded "
                "FROM turn_metrics WHERE created_at >= ?",
                (since,),
            ).fetchall()
        finally:
            connection.close()
        totals = sorted(row[0] for row in rows)
        return {
            "days": days,
            "turns": len(rows),
            "p50_ms": _percentile(totals, 0.5),
            "p95_ms": _percentile(totals, 0.95),
            "llm_calls": sum(row[1] for row in rows),
            "prompt_tokens": sum(row[2] for row in rows),
            "budget_exceeded": sum(1 for row in rows if row[3]),
        }


def _percentile(values: list[int], share: float) -> int | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, round(share * (len(values) - 1))))
    return values[index]
