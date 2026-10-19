"""Full conversion funnel by event, user and timestamp.

Оставлен для совместимости; планировщику показывается funnel_steps.
"""

from app.core.tools.base import BaseTool, Ident, ToolArgs
from app.core.tools.funnel.funnel_steps import FunnelStepsArgs, FunnelStepsTool


class ConversionFunnelArgs(ToolArgs):
    event_column: Ident
    user_column: Ident
    timestamp_column: Ident


class ConversionFunnelTool(BaseTool):
    name = "conversion_funnel"
    description = "Полная воронка с количеством пользователей и коэффициентами конверсии"
    Args = ConversionFunnelArgs

    def execute(self, db, input_table: str, args: ConversionFunnelArgs) -> str:
        return FunnelStepsTool().execute(
            db,
            input_table,
            FunnelStepsArgs(
                step_column=args.event_column,
                order_column=args.timestamp_column,
                user_column=args.user_column,
            ),
        )
