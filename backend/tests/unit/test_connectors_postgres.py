import logging

import duckdb
import pytest

from app.config import settings
from app.core.data.connectors.pg_dsn import parse_dsn
from app.core.data.connectors.postgres import (
    PostgresConnectionError,
    PostgresConnector,
    PostgresExtensionUnavailableError,
)


def test_uri_dsn_is_parsed_and_password_kept_apart() -> None:
    dsn = parse_dsn("postgresql://analyst:s3cr%40t@db.local:6432/shop?sslmode=require")
    assert dsn.password == "s3cr@t"
    assert dsn.params == {
        "host": "db.local",
        "port": "6432",
        "user": "analyst",
        "dbname": "shop",
        "sslmode": "require",
        "connect_timeout": "10",
    }
    assert "s3cr" not in dsn.connection_string()


def test_postgres_scheme_alias_is_accepted() -> None:
    assert parse_dsn("postgres://user@localhost/db").host == "localhost"


def test_keyword_dsn_is_parsed_with_quotes() -> None:
    dsn = parse_dsn("host=localhost dbname='my db' user=bob password='it\\'s' connect_timeout=3")
    assert dsn.params["dbname"] == "my db"
    assert dsn.params["connect_timeout"] == "3"
    assert dsn.password == "it's"
    assert dsn.connection_string() == ("host=localhost dbname='my db' user=bob connect_timeout=3")


@pytest.mark.parametrize(
    "raw",
    [
        "postgres://",
        "postgresql:///db",
        "postgres://user:pw@/db",
        "dbname=shop user=bob",
        "",
        "   ",
        "just-a-hostname",
        "mysql://user:pw@localhost/db",
        "host=localhost dbname='unterminated",
        "postgresql://localhost:notaport/db",
    ],
)
def test_invalid_dsn_raises_value_error(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_dsn(raw)


def test_validation_error_does_not_leak_password() -> None:
    with pytest.raises(ValueError) as error:
        parse_dsn("mysql://user:TopSecret@localhost/db")
    assert "TopSecret" not in str(error.value)


def test_password_is_masked_in_repr_and_str() -> None:
    dsn = parse_dsn("postgresql://analyst:TopSecret@db.local:5432/shop")
    for text in (repr(dsn), str(dsn), dsn.masked()):
        assert "TopSecret" not in text
        assert "***" in text
    assert dsn.masked() == "postgresql://analyst:***@db.local:5432/shop"
    assert dsn.scrub("auth failed for TopSecret") == "auth failed for ***"


class FakeDuckDB:
    def __init__(self, tables=(), existing=(), failing=()) -> None:
        self.tables = list(tables)
        self.existing = list(existing)
        self.failing = failing
        self.statements: list[str] = []

    def execute(self, sql: str, params: dict | None = None) -> None:
        self.statements.append(sql)
        for prefix, error in self.failing:
            if sql.startswith(prefix):
                raise error

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        self.statements.append(sql)
        for prefix, error in self.failing:
            if sql.startswith(prefix):
                raise error
        if "information_schema.tables" in sql:
            return [{"table_name": table} for table in self.tables]
        if "duckdb_tables()" in sql:
            return [{"name": name} for name in self.existing]
        return []

    def index_of(self, prefix: str) -> int:
        return next(
            position
            for position, statement in enumerate(self.statements)
            if statement.startswith(prefix)
        )


DSN = "postgresql://analyst:TopSecret@db.local:5432/shop"


def test_connect_runs_install_load_attach_in_order_and_creates_views() -> None:
    db = FakeDuckDB(tables=["orders", "users"])
    connector = PostgresConnector(DSN)
    assert connector.connect(db) == ["orders", "users"]
    install = db.index_of("INSTALL postgres_scanner")
    load = db.index_of("LOAD postgres_scanner")
    attach = db.index_of("ATTACH ")
    assert install < load < attach
    assert db.statements[attach] == (
        "ATTACH 'host=db.local port=5432 user=analyst dbname=shop connect_timeout=10' "
        f"AS {connector.alias} (TYPE postgres, READ_ONLY, SECRET {connector.alias}_secret)"
    )
    assert (
        f'CREATE OR REPLACE TEMP VIEW "orders" AS SELECT * FROM {connector.alias}."public"."orders"'
        in db.statements
    )
    assert (
        f'CREATE OR REPLACE TEMP VIEW "users" AS SELECT * FROM {connector.alias}."public"."users"'
        in db.statements
    )


def test_password_goes_only_to_the_secret() -> None:
    db = FakeDuckDB(tables=["orders"])
    PostgresConnector(DSN).connect(db)
    leaking = [statement for statement in db.statements if "TopSecret" in statement]
    assert len(leaking) == 1
    assert leaking[0].startswith("CREATE OR REPLACE TEMPORARY SECRET")


def test_no_secret_without_password() -> None:
    db = FakeDuckDB(tables=["orders"])
    PostgresConnector("postgresql://analyst@db.local/shop").connect(db)
    assert not any(
        "SECRET" in statement for statement in db.statements if statement.startswith("ATTACH")
    )
    assert not any(
        statement.startswith("CREATE OR REPLACE TEMPORARY SECRET") for statement in db.statements
    )


def test_identifiers_are_quoted() -> None:
    db = FakeDuckDB(tables=['we"ird table'])
    connector = PostgresConnector(DSN, schema="Odd Schema")
    assert connector.connect(db) == ['we"ird table']
    assert (
        f'CREATE OR REPLACE TEMP VIEW "we""ird table" AS SELECT * FROM '
        f'{connector.alias}."Odd Schema"."we""ird table"'
    ) in db.statements


def test_max_tables_truncates_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    db = FakeDuckDB(tables=[f"table_{number:02d}" for number in range(10)])
    with caplog.at_level(logging.WARNING):
        views = PostgresConnector(DSN, max_tables=3).connect(db)
    assert views == ["table_00", "table_01", "table_02"]
    assert (
        sum(statement.startswith("CREATE OR REPLACE TEMP VIEW") for statement in db.statements) == 3
    )
    assert "POSTGRES_MAX_TABLES" in caplog.text


def test_max_tables_defaults_to_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "postgres_max_tables", 2)
    db = FakeDuckDB(tables=["alpha", "beta", "gamma"])
    assert PostgresConnector(DSN).connect(db) == ["alpha", "beta"]


def test_default_schema_is_public() -> None:
    assert PostgresConnector(DSN).schema == "public"
    assert PostgresConnector(DSN, schema="  ").schema == "public"


def test_empty_schema_returns_empty_list_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    db = FakeDuckDB(tables=[])
    connector = PostgresConnector(DSN, schema="empty")
    with caplog.at_level(logging.WARNING):
        assert connector.connect(db) == []
    assert "нет таблиц" in caplog.text
    assert not any(
        statement.startswith("CREATE OR REPLACE TEMP VIEW") for statement in db.statements
    )
    assert f"DETACH DATABASE IF EXISTS {connector.alias}" in db.statements


def test_name_collision_gets_prefixed() -> None:
    db = FakeDuckDB(tables=["events", "users"], existing=["Events"])
    assert PostgresConnector(DSN).connect(db) == ["shop_events", "users"]


def test_invalid_dsn_fails_before_any_sql() -> None:
    with pytest.raises(ValueError):
        PostgresConnector("postgres://")


def test_non_positive_max_tables_is_rejected() -> None:
    with pytest.raises(ValueError):
        PostgresConnector(DSN, max_tables=0)


def test_repr_masks_password() -> None:
    connector = PostgresConnector(DSN)
    assert "TopSecret" not in repr(connector)
    assert "***" in repr(connector)


def test_attach_failure_is_readable_and_secret_free(caplog: pytest.LogCaptureFixture) -> None:
    error = duckdb.IOException(
        "IO Error: Unable to connect: FATAL:  password authentication failed for user TopSecret"
    )
    db = FakeDuckDB(tables=["orders"], failing=[("ATTACH", error)])
    with caplog.at_level(logging.INFO), pytest.raises(PostgresConnectionError) as raised:
        PostgresConnector(DSN).connect(db)
    message = str(raised.value)
    assert "учётные данные" in message
    assert "TopSecret" not in message
    assert "TopSecret" not in caplog.text
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_unreachable_server_message() -> None:
    error = duckdb.IOException("connection to server failed: Connection refused")
    db = FakeDuckDB(failing=[("ATTACH", error)])
    with pytest.raises(PostgresConnectionError, match="недоступен"):
        PostgresConnector(DSN).connect(db)


def test_extension_offline_install_is_tolerated() -> None:
    db = FakeDuckDB(tables=["orders"], failing=[("INSTALL", duckdb.IOException("offline"))])
    assert PostgresConnector(DSN).connect(db) == ["orders"]


def test_extension_missing_raises_readable_error() -> None:
    db = FakeDuckDB(
        failing=[
            ("INSTALL", duckdb.IOException("offline")),
            ("LOAD", duckdb.IOException("offline")),
        ]
    )
    with pytest.raises(PostgresExtensionUnavailableError, match="postgres_scanner"):
        PostgresConnector(DSN).connect(db)
    assert not any(statement.startswith("ATTACH") for statement in db.statements)


def test_success_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    db = FakeDuckDB(tables=["orders", "users"])
    with caplog.at_level(logging.INFO):
        PostgresConnector(DSN).connect(db)
    assert "подключено 2 таблиц" in caplog.text
    assert "db.local" in caplog.text
    assert "TopSecret" not in caplog.text
