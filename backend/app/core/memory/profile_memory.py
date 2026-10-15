"""Память уровня 3: метапамять — паспорт данных и глоссарий.

Хранит сгенерированные при подключении метаданные: структуру таблиц,
статистику, описания и человекочитаемые алиасы колонок, бизнес-словарь.
Избавляет от сканирования источника заново на каждый вопрос — паспорт
всегда в контексте агента. Лежит в постоянных таблицах схемы `_meta`
файла DuckDB (DUCKDB_PATH) и переживает перезапуск приложения.
"""

import json

from app.core.data.profiling import ROW_FIELDS, TableProfile
from app.models.enrichment import ColumnEnrichment

SCHEMA = (
    "CREATE SCHEMA IF NOT EXISTS _meta",
    """CREATE TABLE IF NOT EXISTS _meta.table_passport (
        table_name    VARCHAR PRIMARY KEY,
        row_count     BIGINT NOT NULL,
        column_count  INTEGER NOT NULL,
        glossary      JSON,
        summary       VARCHAR,
        profiled_at   TIMESTAMPTZ NOT NULL,
        enriched_at   TIMESTAMPTZ
    )""",
    """CREATE TABLE IF NOT EXISTS _meta.column_passport (
        table_name           VARCHAR NOT NULL,
        column_name          VARCHAR NOT NULL,
        position             INTEGER NOT NULL,
        dtype                VARCHAR NOT NULL,
        kind                 VARCHAR NOT NULL,
        total                BIGINT,
        nulls                BIGINT,
        null_pct             DOUBLE,
        nan_pct              DOUBLE,
        distinct_count       BIGINT,
        distinct_pct         DOUBLE,
        min_value            JSON,
        max_value            JSON,
        mode_value           JSON,
        min_date             JSON,
        max_date             JSON,
        mean                 DOUBLE,
        median               DOUBLE,
        std                  DOUBLE,
        q1                   DOUBLE,
        q3                   DOUBLE,
        iqr                  DOUBLE,
        p05                  DOUBLE,
        p95                  DOUBLE,
        skewness             DOUBLE,
        kurtosis             DOUBLE,
        top_values           JSON,
        min_len              INTEGER,
        max_len              INTEGER,
        is_constant          BOOLEAN NOT NULL,
        is_unique            BOOLEAN NOT NULL,
        outlier_count        BIGINT,
        lower_fence          DOUBLE,
        upper_fence          DOUBLE,
        description          VARCHAR,
        description_original VARCHAR,
        description_source   VARCHAR,
        aliases              VARCHAR[] NOT NULL DEFAULT [],
        fingerprint          VARCHAR,
        enriched_at          TIMESTAMPTZ,
        PRIMARY KEY (table_name, column_name)
    )""",
)

SAVE_TABLE = """INSERT INTO _meta.table_passport
    (table_name, row_count, column_count, glossary, profiled_at)
    VALUES ($table, $row_count, $column_count, $glossary, $profiled_at)
    ON CONFLICT (table_name) DO UPDATE SET
        row_count = excluded.row_count,
        column_count = excluded.column_count,
        glossary = COALESCE(excluded.glossary, glossary),
        profiled_at = excluded.profiled_at"""

COLUMN_FIELDS = ("table_name", "position", *ROW_FIELDS)
UPDATED_FIELDS = [name for name in COLUMN_FIELDS if name not in ("table_name", "column_name")]
SAVE_COLUMN = (
    f"INSERT INTO _meta.column_passport ({', '.join(COLUMN_FIELDS)}) "
    f"VALUES ({', '.join(f'${name}' for name in COLUMN_FIELDS)}) "
    "ON CONFLICT (table_name, column_name) DO UPDATE SET "
    + ", ".join(f"{name} = excluded.{name}" for name in UPDATED_FIELDS)
)

DROP_COLUMNS = """DELETE FROM _meta.column_passport
    WHERE table_name = $table AND NOT list_contains($columns, column_name)"""

SAVE_ENRICHMENT = """UPDATE _meta.column_passport SET
        description = $description,
        description_original = $description_original,
        description_source = $description_source,
        aliases = $aliases::VARCHAR[],
        fingerprint = $fingerprint,
        enriched_at = now()
    WHERE table_name = $table AND column_name = $column"""

LOAD_TABLE = """SELECT table_name, row_count, column_count,
        strftime(profiled_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S+00:00') AS profiled_at
    FROM _meta.table_passport WHERE table_name = $table"""

LOAD_COLUMNS = (
    f"SELECT position, {', '.join(ROW_FIELDS)} FROM _meta.column_passport "
    "WHERE table_name = $table ORDER BY position"
)

LOAD_ENRICHMENT = """SELECT column_name, description, aliases, description_original,
        description_source, fingerprint
    FROM _meta.column_passport WHERE table_name = $table ORDER BY position"""


class ProfileMemory:
    """Хранилище «паспортов данных» (по одной записи на таблицу)."""

    def __init__(self, db) -> None:
        """db — экземпляр DuckDBManager."""
        self.db = db
        for statement in SCHEMA:
            db.execute(statement)

    def save_profile(self, profile: TableProfile, glossary: dict | None = None) -> None:
        """Сохранить статистику таблицы; колонок, которых больше нет, — удалить."""
        table = profile.table
        with self.db.transaction():
            self.db.execute(
                SAVE_TABLE,
                {
                    "table": table,
                    "row_count": profile.row_count,
                    "column_count": profile.column_count,
                    "glossary": None
                    if glossary is None
                    else json.dumps(glossary, ensure_ascii=False),
                    "profiled_at": profile.profiled_at,
                },
            )
            names = [column.name for column in profile.columns]
            self.db.execute(DROP_COLUMNS, {"table": table, "columns": names})
            for position, column in enumerate(profile.columns):
                row = column.to_row() | {"table_name": table, "position": position}
                self.db.execute(SAVE_COLUMN, row)

    def load_profile(self, table: str) -> TableProfile | None:
        """Восстановить профиль таблицы (None, если паспорта ещё нет).

        profiled_at читается строкой в UTC: результат не зависит от TimeZone сессии.
        """
        with self.db.lock:
            tables = self.db.query(LOAD_TABLE, {"table": table})
            if not tables:
                return None
            columns = self.db.query(LOAD_COLUMNS, {"table": table})
        return TableProfile.from_rows(tables[0], columns)

    def load_glossary(self, table: str) -> dict | None:
        """Бизнес-словарь, сохранённый вместе с паспортом таблицы."""
        rows = self.db.query(
            "SELECT glossary FROM _meta.table_passport WHERE table_name = $table",
            {"table": table},
        )
        if not rows or rows[0]["glossary"] is None:
            return None
        return json.loads(rows[0]["glossary"])

    def save_enrichment(self, table: str, rows: list[ColumnEnrichment]) -> None:
        """Сохранить описания и алиасы колонок (статистику не трогает)."""
        with self.db.transaction():
            for row in rows:
                self.db.execute(
                    SAVE_ENRICHMENT,
                    {
                        "table": table,
                        "column": row.column,
                        "description": row.description,
                        "description_original": row.description_original,
                        "description_source": row.description_source,
                        "aliases": row.aliases,
                        "fingerprint": row.fingerprint,
                    },
                )
            self.db.execute(
                "UPDATE _meta.table_passport SET enriched_at = now() WHERE table_name = $table",
                {"table": table},
            )

    def load_enrichment(self, table: str) -> list[ColumnEnrichment]:
        """Описания и алиасы колонок в порядке колонок таблицы."""
        rows = self.db.query(LOAD_ENRICHMENT, {"table": table})
        return [
            ColumnEnrichment(
                column=row["column_name"],
                description=row["description"],
                aliases=row["aliases"],
                description_original=row["description_original"],
                description_source=row["description_source"],
                fingerprint=row["fingerprint"],
            )
            for row in rows
        ]

    def delete(self, table: str) -> None:
        """Удалить паспорт таблицы."""
        with self.db.transaction():
            self.db.execute(
                "DELETE FROM _meta.column_passport WHERE table_name = $table", {"table": table}
            )
            self.db.execute(
                "DELETE FROM _meta.table_passport WHERE table_name = $table", {"table": table}
            )
