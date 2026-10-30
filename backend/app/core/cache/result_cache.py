"""Кеш результатов: SQL-выборки и семантические факты (P1)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.cache.models import CacheEntry, CacheKey, copy_payload


@dataclass
class CachedResult:
    """Результат, сохранённый под ключом."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


class ResultCache:
    """Потокобезопасный in-memory TTL-кеш результатов."""

    def __init__(self) -> None:
        self._entries: dict[str, CacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, key: CacheKey) -> CachedResult | None:
        digest = key.digest()
        with self._lock:
            entry = self._entries.get(digest)
            if entry is None:
                return None
            if entry.expired():
                self._entries.pop(digest, None)
                return None
            payload = copy_payload(entry.payload)
        if not isinstance(payload, dict):
            return None
        return CachedResult(rows=payload.get("rows") or [], meta=payload.get("meta") or {})

    def put(self, key: CacheKey, result: CachedResult, ttl_s: int = 3600) -> None:
        now = time.time()
        with self._lock:
            self._entries[key.digest()] = CacheEntry(
                key=key.digest(),
                payload={"rows": copy_payload(result.rows), "meta": copy_payload(result.meta)},
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
