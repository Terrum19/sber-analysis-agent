"""Проверка ответа в режиме «Проверка» (A-29): находки шагов и вердикт критика."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Пункты чек-листа критика: id фиксированы, по ним фронт подписывает пункты.
CHECKS = {
    "answers_question": "Ответ на вопрос",
    "metric": "Метрика",
    "period": "Период",
    "filters": "Фильтры",
    "nulls": "Пропуски",
    "outliers": "Выбросы",
    "columns": "Неучтённые колонки",
    "joins": "Объединения",
    "claims": "Смелость выводов",
    "numbers": "Числа",
}

Verdict = Literal["ok", "caveat", "fix_insight", "extend_plan", "replan"]
# Вердикты, после которых ответ исправляется.
REPAIRS = ("fix_insight", "extend_plan", "replan")


class StepFinding(BaseModel):
    """Аномалия результата шага, найденная кодом (без LLM)."""

    code: str
    message: str


class ReviewCheck(BaseModel):
    id: str
    status: Literal["ok", "issue", "n/a"] = "n/a"
    evidence: str = ""

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> str:
        """Неизвестный статус — «не проверено», а не отказ всего ответа."""
        text = str(value or "").strip().lower()
        return text if text in ("ok", "issue", "n/a") else "n/a"


class ReviewIssue(BaseModel):
    check: str
    severity: Literal["material", "minor"] = "minor"
    message: str
    evidence: str = ""
    fix: str = ""

    @field_validator("severity", mode="before")
    @classmethod
    def _severity(cls, value: object) -> str:
        return "material" if str(value or "").strip().lower() == "material" else "minor"


class Review(BaseModel):
    """Ответ критика по чек-листу CHECKS."""

    checks: list[ReviewCheck] = Field(default_factory=list)
    issues: list[ReviewIssue] = Field(default_factory=list)
    verdict: Verdict = "ok"
    caveats: list[str] = Field(default_factory=list)

    @field_validator("verdict", mode="before")
    @classmethod
    def _verdict(cls, value: object) -> str:
        return str(value or "ok").strip().lower()
