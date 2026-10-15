"""Pydantic-схемы источников данных."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DatasourceCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["postgres", "clickhouse"]
    dsn: str
    db_schema: str | None = Field(default=None, alias="schema")


class DatasourceOut(BaseModel):
    datasource_id: str
    kind: Literal["csv", "postgres", "clickhouse"]
    tables: list[str]
    profile_summary: str = ""  # «передо мной X Y Z» от LLM
