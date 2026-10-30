"""Pydantic-схемы источников данных."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.data.overview import DISPLAY_NAME_MAX_CHARS, key_columns, starter_questions
from app.core.data.profiling import TableProfile, profile_warnings
from app.models.enrichment import ColumnEnrichment
from app.models.semantic import SemanticModel

# Ответы CRUD semantic model (P1) — та же модель, что хранится в _meta.
SemanticModelOut = SemanticModel


class DatasourceCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["postgres", "clickhouse"]
    dsn: str
    db_schema: str | None = Field(default=None, alias="schema")
    # Документ-описание таблиц (README, словарь колонок): из него в паспорт
    # попадут названия и описания колонок.
    description: str | None = None


class TableBrief(BaseModel):
    """Сводка паспорта таблицы для карточки на фронте.

    table — имя таблицы в DuckDB для SQL; display_name — название для
    пользователя (его или от LLM), None — названия ещё нет.
    """

    table: str
    display_name: str | None = None
    rows: int
    columns: int
    period_from: str | None = None
    period_to: str | None = None
    description_status: Literal["none", "pending", "ready", "failed"] = "none"
    summary: str | None = None
    warnings: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)

    @classmethod
    def from_profile(
        cls,
        profile: TableProfile,
        description_status: str,
        warnings: list[str],
        summary: str | None = None,
        display_name: str | None = None,
        enrichment: list[ColumnEnrichment] | None = None,
    ) -> "TableBrief":
        """Период — min_date/max_date первой temporal-колонки профиля.

        warnings — что исправил загрузчик, за ними флаги качества профиля (profile_warnings).
        Сущности, метрики и подсказки к вопросам — из профиля и названий колонок, без LLM.
        """
        temporal = profile.temporal_column()
        enrichment = enrichment or []
        entities, metrics = key_columns(profile, enrichment)
        return cls(
            table=profile.table,
            display_name=display_name,
            rows=profile.row_count,
            columns=profile.column_count,
            period_from=None if temporal is None else iso(temporal.min_date),
            period_to=None if temporal is None else iso(temporal.max_date),
            description_status=description_status,
            summary=summary,
            warnings=warnings + profile_warnings(profile),
            entities=entities,
            metrics=metrics,
            suggested_questions=starter_questions(profile, enrichment),
        )


class TableRename(BaseModel):
    """Тело PATCH /api/datasources/tables/{table}: название таблицы от пользователя."""

    display_name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=DISPLAY_NAME_MAX_CHARS),
    ]


def iso(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


class DatasourceOut(BaseModel):
    datasource_id: str
    kind: Literal["csv", "postgres", "clickhouse"]
    tables: list[str]
    profiles: list[TableBrief] = Field(default_factory=list)
