from typing import Literal

from pydantic import BaseModel, Field


class ResearchStep(BaseModel):
    step_id: int = 0
    tool: str | None = None
    columns: list[str] = Field(default_factory=list)
    parameters: dict[str, object] = Field(default_factory=dict)
    needs_llm_codegen: bool = False
    purpose: str = ""


class ResearchPlan(BaseModel):
    steps: list[ResearchStep] = Field(default_factory=list)
    final_viz: Literal["auto", "bar", "line", "scatter", "histogram", "table"] = "auto"
    dashboard_requested: bool = False
