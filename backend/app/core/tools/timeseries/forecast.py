"""TS-тул: экстраполяция/прогноз (scenario-based).

Пример сценария: «Какой рост X ожидается в ближайшие кварталы при
текущих условиях?», «Как изменится Y, если изменить цену X?».
"""
from app.core.tools.base import BaseTool


class ForecastTool(BaseTool):
    """Прогноз на N периодов вперёд по линейному тренду."""

    name = "forecast"
    description = "Экстраполировать метрику на ближайшие периоды (тренд + сценарии)"

    def run(self, db, input_table: str, columns: list[str]) -> str:
        """TODO(round-2): регрессия по периоду, прогноз + сценарии изменения."""
        raise NotImplementedError
