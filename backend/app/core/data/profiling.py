"""Профилирование таблиц при подключении (без предобработки датасета).

Считает основные метрики по колонкам: процент пропусков (NaN),
min/max, медиана, квартили, стандартное отклонение — основание для
описания данных и алиасов.
"""


def profile_table(db, table: str) -> dict:
    """Собрать «паспорт» таблицы: схема + статистика по колонкам."""
    columns = db.query(f"PRAGMA table_info('{table}')")
    profile: dict[str, dict] = {"table": table, "columns": {}}
    for col in columns:
        name = col["name"]
        stats = db.query(
            f"""
            SELECT
              COUNT(*) AS total,
              COUNT(*) - COUNT({name}) AS nulls,
              MIN({name}) AS min, MAX({name}) AS max,
              MEDIAN({name}) AS median,
              STDDEV({name}) AS std
            FROM {table}
            """
        )
        profile["columns"][name] = stats[0] if stats else {}
    profile["row_count"] = profile["columns"][columns[0]["name"]]["total"] if columns else 0
    return profile
