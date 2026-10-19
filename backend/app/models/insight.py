"""Схема итогового инсайта."""

from pydantic import BaseModel, field_validator


class Insight(BaseModel):
    """Финальный вывод на бизнес-языке."""

    headline: str
    details: list[str] = []
    limits: str | list[str] = ""

    @field_validator("limits", mode="before")
    @classmethod
    def _coerce_limits_to_str(cls, v: object) -> str:
        """Приводит limits к строке: принимает и строку, и список строк."""
        if v is None:
            return ""
        if isinstance(v, list):
            return "; ".join(str(x) for x in v)
        return str(v)
