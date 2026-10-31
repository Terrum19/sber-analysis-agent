from typing import Literal

from pydantic import BaseModel, Field


class Datasource(BaseModel):
    datasource_id: str
    kind: Literal["csv", "postgres", "clickhouse"]
    location: str
    tables: list[str] = Field(default_factory=list)
