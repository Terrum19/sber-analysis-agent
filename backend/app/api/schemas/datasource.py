"""Pydantic-схемы источников данных."""
from typing import Literal

from pydantic import BaseModel


class DatasourceCreate(BaseModel):
    kind: Literal["postgres", "clickhouse"]
    dsn: str
    schema: str | None = None


class DatasourceOut(BaseModel):
    datasource_id: str
    kind: Literal["csv", "postgres", "clickhouse"]
    tables: list[str]
    profile_summary: str = ""  # «передо мной X Y Z» от LLM
