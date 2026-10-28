"""Индекс объектов схемы и гибридный retrieval для schema linking (P1).

Лексический скор работает всегда. Эмбеддинги подключаются, только если
установлен ``sentence-transformers`` и задан ``EMBED_MODEL``; при любой
ошибке — тихий fallback на lexical, без падения планировщика.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.core.data.profiling import TableProfile
from app.models.semantic import SemanticModel


@dataclass(frozen=True)
class SchemaObject:
    kind: str
    name: str
    text: str
    score: float = 0.0


class SchemaIndex:
    """In-memory индекс колонок/метрик/измерений; embeddings — опционально."""

    def __init__(self) -> None:
        self._objects: dict[str, list[SchemaObject]] = {}
        self._vectors: dict[str, list[list[float]]] = {}
        self._model: Any | None = None
        self._model_failed = False

    def rebuild(
        self,
        table: str,
        profile: TableProfile,
        semantic: SemanticModel | None = None,
    ) -> None:
        objects: list[SchemaObject] = []
        for column in profile.columns:
            if column.kind == "identifier":
                continue
            objects.append(
                SchemaObject(
                    kind="column",
                    name=column.name,
                    text=f"{column.name} {column.kind} {column.dtype}",
                )
            )
            for entry in column.top_values[:5]:
                objects.append(
                    SchemaObject(
                        kind="value",
                        name=column.name,
                        text=f"{column.name} значение {entry.get('value')}",
                    )
                )
        if semantic is not None:
            for metric in semantic.metrics:
                objects.append(
                    SchemaObject(
                        kind="metric",
                        name=metric.name,
                        text=f"{metric.name} {metric.title} {metric.description} "
                        + " ".join(metric.synonyms),
                    )
                )
            for dimension in semantic.dimensions:
                objects.append(
                    SchemaObject(
                        kind="dimension",
                        name=dimension.name,
                        text=f"{dimension.name} {dimension.column} "
                        + " ".join(dimension.synonyms),
                    )
                )
        self._objects[table] = objects
        self._vectors[table] = self._embed([item.text for item in objects])

    def search(self, question: str, table: str, top_k: int = 8) -> list[SchemaObject]:
        objects = self._objects.get(table, [])
        if not objects:
            return []
        lexical = self._lexical(question, objects)
        vectors = self._vectors.get(table) or []
        query_vector = self._embed([question])
        if vectors and query_vector:
            scored = []
            for item, vector, base in zip(objects, vectors, lexical):
                score = base + _cosine(query_vector[0], vector) * 2
                scored.append(SchemaObject(item.kind, item.name, item.text, score))
        else:
            scored = [
                SchemaObject(item.kind, item.name, item.text, score)
                for item, score in zip(objects, lexical)
            ]
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_k]

    def _lexical(self, question: str, objects: list[SchemaObject]) -> list[float]:
        tokens = _tokens(question)
        scores = []
        for item in objects:
            item_tokens = _tokens(item.text)
            overlap = len(tokens & item_tokens)
            partial = sum(
                1
                for token in tokens
                if len(token) >= 4 and any(token[:4] == other[:4] for other in item_tokens)
            )
            scores.append(float(overlap * 2 + partial))
        return scores

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if not settings.embed_model or self._model_failed:
            return []
        try:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(settings.embed_model)
            return [list(map(float, vector)) for vector in self._model.encode(texts)]
        except Exception:  # noqa: BLE001 — embeddings необязательны, fallback на lexical
            self._model_failed = True
            return []


class SchemaRetriever:
    """Тонкая обёртка: таблица -> top-k объектов (lexical + vector)."""

    def __init__(self, index: SchemaIndex | None = None) -> None:
        self.index = index or SchemaIndex()

    def retrieve(self, question: str, table: str, top_k: int = 8) -> list[SchemaObject]:
        return self.index.search(question, table, top_k)


def _tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^\w]+", text.lower()) if token}


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0
