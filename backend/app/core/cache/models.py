"""Ключи и записи кеша планов/результатов (P1)."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CacheKey:
    namespace: str
    question_hash: str
    table: str
    schema_hash: str
    model: str
    prompt_version: str
    data_version: str

    def digest(self) -> str:
        payload = (
            f"{self.namespace}|{self.question_hash}|{self.table}|{self.schema_hash}|"
            f"{self.model}|{self.prompt_version}|{self.data_version}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class CacheEntry:
    key: str
    payload: dict | list
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    table: str = ""

    def expired(self, now: float | None = None) -> bool:
        return self.expires_at <= (now or time.time())


def hash_text(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def schema_hash(columns: list[str], descriptions: dict[str, str] | None = None) -> str:
    parts = [" ".join(sorted(columns))]
    parts.extend(f"{key}={value}" for key, value in sorted((descriptions or {}).items()))
    return hash_text("\n".join(parts))


def cache_key(
    *,
    namespace: str,
    question: str,
    table: str,
    schema: str,
    model: str,
    prompt_version: str = "v1",
    data_version: str = "",
) -> CacheKey:
    return CacheKey(
        namespace=namespace,
        question_hash=hash_text(question),
        table=table,
        schema_hash=schema,
        model=model,
        prompt_version=prompt_version,
        data_version=data_version,
    )


def copy_payload(payload: Any) -> Any:
    """Копия изменяемого payload, чтобы кеш не зависел от мутаций вызывающего."""
    import copy

    return copy.deepcopy(payload)
