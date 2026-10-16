from pydantic import BaseModel, Field


class ColumnEnrichment(BaseModel):
    """Описание и алиасы колонки.

    От LLM приходят только column, description и aliases; остальные поля
    заполняет SchemaEnricher и хранит ProfileMemory (_meta.column_passport).
    """

    column: str
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    description_original: str | None = None
    description_source: str | None = None
    fingerprint: str | None = None


class EnrichmentBatch(BaseModel):
    """Ответ LLM на пачку колонок."""

    columns: list[ColumnEnrichment] = Field(default_factory=list)
