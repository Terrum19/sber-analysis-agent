"""Full conversion funnel by event, user and timestamp."""

from app.core.tools.base import BaseTool, identifier
from app.core.tools.funnel.funnel_steps import FunnelStepsTool


class ConversionFunnelTool(BaseTool):
    name = "conversion_funnel"
    description = (
        "Полная воронка по пользователям: columns=[event_col, user_id_col, timestamp_col]; "
        "parameters: steps — порядок шагов"
    )

    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        if len(columns) != 3:
            raise ValueError("conversion_funnel requires [event_col, user_id_col, timestamp_col]")
        event_col, user_col, timestamp_col = (identifier(column) for column in columns)
        return FunnelStepsTool().run(
            db, input_table, [event_col, timestamp_col, user_col], **kwargs
        )
