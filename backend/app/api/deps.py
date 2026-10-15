from fastapi import Request

from app.core.data.duckdb_manager import DuckDBManager


def get_db(request: Request) -> DuckDBManager:
    return request.app.state.db
