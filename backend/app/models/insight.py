"""Схема итогового инсайта."""
from pydantic import BaseModel


class Insight(BaseModel):
    """Финальный вывод на бизнес-языке."""
    headline: str
    details: list[str] = []
    limits: str = ""
