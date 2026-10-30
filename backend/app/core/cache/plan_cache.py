"""Кеш планов: exact-match по вопросу/схеме/модели (P1)."""

from __future__ import annotations

import threading
import time

from app.core.cache.models import CacheEntry, CacheKey, copy_payload
from app.models.research_step import ResearchPlan


class PlanCache:
    """Потокобезопасный in-memory TTL-кеш планов."""

    def __init__(self) -> None:
        self._entries: dict[str, CacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, key: CacheKey) -> ResearchPlan | None:
        digest = key.digest()
        with self._lock:
            entry = self._entries.get(digest)
            if entry is None:
                return None
            if entry.expired():
                self._entries.pop(digest, None)
                return None
            return ResearchPlan.model_validate(copy_payload(entry.payload))

    def put(self, key: CacheKey, plan: ResearchPlan, ttl_s: int = 3600) -> None:
        now = time.time()
        with self._lock:
            self._entries[key.digest()] = CacheEntry(
                key=key.digest(),
                payload=plan.model_dump(mode="json"),
                created_at=now,
                expires_at=now + max(1, ttl_s),
                table=key.table,
            )

    def invalidate(self, table: str) -> None:
        with self._lock:
            for digest in [d for d, entry in self._entries.items() if entry.table == table]:
                self._entries.pop(digest, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
