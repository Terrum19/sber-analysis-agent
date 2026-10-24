"""Pydantic-схемы источников данных."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.data.profiling import TableProfile, profile_warnings


class DatasourceCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["postgres", "clickhouse"]
    dsn: str
    db_schema: str | None = Field(default=None, alias="schema")
    # Документ-описание таблиц (README, словарь колонок): из него в паспорт
    # попадут названия и описания колонок.
    description: str | None = None


class TableBrief(BaseModel):
    """Сводка паспорта таблицы для карточки на фронте."""

    table: str
    rows: int
    columns: int
    period_from: str | None = None
    period_to: str | None = None
    description_status: Literal["none", "pending", "ready", "failed"] = "none"
    summary: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def from_profile(
        cls, profile: TableProfile, description_status: str, warnings: list[str]
    ) -> "TableBrief":
        """Период — min_date/max_date первой temporal-колонки профиля.

        warnings — что исправил загрузчик, за ними флаги качества профиля (profile_warnings).
        """
        temporal = next((column for column in profile.columns if column.kind == "temporal"), None)
        return cls(
            table=profile.table,
            rows=profile.row_count,
            columns=profile.column_count,
            period_from=None if temporal is None else iso(temporal.min_date),
            period_to=None if temporal is None else iso(temporal.max_date),
            description_status=description_status,
            warnings=warnings + profile_warnings(profile),
        )


def iso(value: object) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


class DatasourceOut(BaseModel):
    datasource_id: str
    kind: Literal["csv", "postgres", "clickhouse"]
    tables: list[str]
    profiles: list[TableBrief] = Field(default_factory=list)
    profile_summary: str = ""  # «передо мной X Y Z» от LLM
