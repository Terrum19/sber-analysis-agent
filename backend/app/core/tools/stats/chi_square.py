"""Chi-square test of independence for two categorical columns."""

from scipy import stats

from app.core.tools.base import BaseTool, identifier
from app.core.tools.stats._args import TwoColumnsArgs
from app.core.tools.stats._common import result


class ChiSquareTool(BaseTool):
    name = "chi_square"
    description = "Хи-квадрат: связаны ли две категориальные колонки (statistic, p_value)"
    kind = "analysis"
    Args = TwoColumnsArgs

    def execute(self, db, input_table: str, args: TwoColumnsArgs) -> str:
        first, second = args.column_a, args.column_b
        rows = db.query(
            f"SELECT {first} AS first_value, {second} AS second_value "
            f"FROM {identifier(input_table)} "
            f"WHERE {first} IS NOT NULL AND {second} IS NOT NULL"
        )
        if not rows:
            return result(
                db, input_table, "statistic, p_value, degrees_of_freedom", (None, None, None)
            )
        table: dict[object, dict[object, int]] = {}
        for row in rows:
            table.setdefault(row["first_value"], {}).setdefault(row["second_value"], 0)
            table[row["first_value"]][row["second_value"]] += 1
        columns_ = sorted({value for row in table.values() for value in row})
        observed = [[row.get(column, 0) for column in columns_] for row in table.values()]
        test = stats.chi2_contingency(observed)
        return result(
            db,
            input_table,
            "statistic, p_value, degrees_of_freedom",
            (test.statistic, test.pvalue, test.dof),
        )
