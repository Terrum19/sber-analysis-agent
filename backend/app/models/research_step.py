"""Схемы плана исследования: ResearchStep и ResearchPlan."""
from typing import Literal
from pydantic import BaseModel, Field


class ResearchStep(BaseModel):
    """Один шаг плана исследования."""
    step_id: int
    tool: str | None = None
    columns: list[str] = Field(default_factory=list)
    needs_llm_codegen: bool = False
    purpose: str = ""


class ResearchPlan(BaseModel):
    """Полный план исследования: упорядоченные шаги + мета."""
    steps: list[ResearchStep] = Field(default_factory=list)
    final_viz: str = "auto"  # bar, line, scatter, histogram, table, auto
    dashboard_requested: bool = False
