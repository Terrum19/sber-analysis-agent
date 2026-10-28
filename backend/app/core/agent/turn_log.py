import logging
import time
from collections.abc import AsyncIterator

from app.core.logging_setup import TurnContext, bound_turn
from app.models.research_step import ResearchPlan

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_CLARIFICATION = "clarification"
STATUS_DISCONNECTED = "disconnected"
START_STAGE = "start"
DISCONNECT_REASON = "клиент отключился"
NOT_FATAL_STAGES = ("dashboard",)
TIMED_STAGES = {"text": ("insight", "инсайт"), "chart": ("chart", "график")}


def describe_plan(plan: ResearchPlan) -> str:
    steps = ", ".join(f"{step.step_id} {step.tool}" for step in plan.steps)
    return (
        f"шагов={len(plan.steps)} [{steps}] final_viz={plan.final_viz}"
        f" dashboard={plan.dashboard_requested}"
    )


def _stage_label(data: dict) -> str:
    if data.get("stage") == "step":
        return f"step {data.get('step_id')}"
    return str(data.get("stage"))


class TurnLog:
    def __init__(self, session_id: str) -> None:
        self.context = TurnContext(session_id=session_id)
        self.started_at = time.perf_counter()
        self.stage = START_STAGE
        self.failed_stage: str | None = None
        self.reason: str | None = None
        self.status: str | None = None
        self.finished = False
        self.stage_started: dict[str, float] = {}

    async def next_event(self, events: AsyncIterator[dict]) -> dict | None:
        with bound_turn(self.context):
            return await anext(events, None)

    def observe(self, event: dict) -> None:
        kind = event["type"]
        data = event.get("data") or {}
        if kind == "progress":
            self._progress(data)
        elif kind in TIMED_STAGES:
            self._timed(kind, data)
        elif kind == "error":
            self.status = STATUS_FAILED
            self.reason = self.reason or data.get("message")
        elif kind == "clarification_needed":
            self.status = STATUS_CLARIFICATION
            self.reason = data.get("error")
            self._finish(data)
        elif kind == "done":
            self._finish(data)

    def crashed(self, error: BaseException) -> None:
        self.status = STATUS_FAILED
        self.reason = repr(error)
        self._finish({})

    def close(self) -> None:
        if self.finished:
            return
        self.status = STATUS_DISCONNECTED
        self.reason = DISCONNECT_REASON
        self._finish({})

    def _progress(self, data: dict) -> None:
        stage = data.get("stage")
        status = data.get("status")
        if status == "started":
            self.stage = _stage_label(data)
            self.stage_started[str(stage)] = time.perf_counter()
        elif status == "failed" and stage not in NOT_FATAL_STAGES:
            self.failed_stage = _stage_label(data)
            self.reason = data.get("error")

    def _timed(self, kind: str, data: dict) -> None:
        stage, title = TIMED_STAGES[kind]
        started_at = self.stage_started.pop(stage, None)
        if started_at is None:
            return
        chart_type = (data.get("spec") or {}).get("usermeta", {}).get("chart_type")
        suffix = f" {chart_type}" if chart_type else ""
        with bound_turn(self.context):
            logger.info("%s%s за %.1f с", title, suffix, time.perf_counter() - started_at)

    def _finish(self, data: dict) -> None:
        self.finished = True
        status = self.status or STATUS_OK
        elapsed = time.perf_counter() - self.started_at
        message = f"итог хода: {status} за {elapsed:.1f} с"
        if status != STATUS_OK:
            reason = " ".join(str(self.reason).split())
            message += f", этап {self.failed_stage or self.stage}: {reason}"
        if data.get("prompt_truncated"):
            message += f", обрезано промптов: {data['prompt_truncated']}"
        level = logging.INFO if status in (STATUS_OK, STATUS_CLARIFICATION) else logging.WARNING
        with bound_turn(self.context):
            logger.log(level, "%s", message)
