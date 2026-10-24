from app.core.data.connectors.base import BaseConnector
from app.core.data.connectors.clickhouse import ClickHouseConnector
from app.core.data.connectors.postgres import PostgresConnector


def build_connector(kind: str, dsn: str, schema: str | None = None) -> BaseConnector:
    if kind == "postgres":
        return PostgresConnector(dsn, schema=schema)
    if kind == "clickhouse":
        return ClickHouseConnector(dsn, database=schema)
    raise ValueError(f"Неизвестный тип источника данных: {kind}.")
