from pydantic import BaseModel, Field


class ColumnEnrichment(BaseModel):
    """Название и описание колонки из описания таблицы.

    От LLM приходят только column, title и description (извлечены из
    документа-описания); остальные поля заполняет SchemaEnricher и хранит
    ProfileMemory (_meta.column_passport).
    """

    column: str
    title: str | None = None
    description: str | None = None
    description_source: str | None = None
    fingerprint: str | None = None


class EnrichmentBatch(BaseModel):
    """Ответ LLM на пачку колонок."""

    columns: list[ColumnEnrichment] = Field(default_factory=list)
