from pydantic import BaseModel, Field


class Insight(BaseModel):
    headline: str
    details: list[str] = Field(default_factory=list)
    limits: list[str] = Field(default_factory=list)
