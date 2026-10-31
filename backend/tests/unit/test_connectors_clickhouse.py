import logging

import duckdb
import pytest

from app.config import Settings, settings
from app.core.data.connectors.clickhouse import (
    ClickHouseConnectionError,
    ClickHouseConnector,
    ClickHouseExtensionUnavailableError,
    describe_failure,
    parse_dsn,
    quote_identifier,
    quote_literal,
)
from app.core.data.connectors.factory import build_connector


def test_uri_dsn_is_parsed_and_password_kept_apart() -> None:
    dsn = parse_dsn("clickhouse://analyst:s3cr%40t@db.local:9000/analytics")
    assert dsn.password == "s3cr@t"
    assert dsn.host == "db.local"
    assert dsn.port == 9000
    assert dsn.user == "analyst"
    assert dsn.database == "analytics"
    assert not dsn.secure
    assert "s3cr" not in dsn.connection_string()


def test_clickhouses_scheme_sets_secure_and_default_port() -> None:
    dsn = parse_dsn("clickhouses://ch.prod/bi")
    assert dsn.secure
    assert dsn.port == 9440
    assert dsn.database == "bi"
    assert dsn.connection_string().endswith(" secure=true")


def test_with_password_rebuilds_same_source() -> None:
    dsn = parse_dsn("clickhouses://an%40lyst:old@ch.prod:9440/my%20db")
    url = dsn.with_password("p@ss:/w'rd%")
    rebuilt = parse_dsn(url)
    assert rebuilt == dsn.__class__(
        host="ch.prod",
        port=9440,
        database="my db",
        user="an@lyst",
        password="p@ss:/w'rd%",
        secure=True,
    )
    assert rebuilt.masked() == dsn.masked()
    assert parse_dsn(parse_dsn("clickhouse://h/x").with_password("pw")).user == "default"


def test_default_user_port_database() -> None:
    dsn = parse_dsn("clickhouse://localhost")
    assert dsn.user == "default"
    assert dsn.port == 9000
    assert dsn.database == "default"
    assert dsn.password is None


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "clickhouse://",
        "clickhouse:///db",
        "just-a-host",
        "clickhouse://localhost:notaport/db",
        "clickhouse://localhost:8123/db",
        "mysql://user:pw@localhost/db",
        "http://localhost:8123/db",
    ],
)
def test_invalid_dsn_raises_value_error(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_dsn(raw)


def test_scrub_without_password_is_identity() -> None:
    dsn = parse_dsn("clickhouse://analyst@ch.local/sales")
    assert dsn.password is None
    assert dsn.scrub("nothing to hide") == "nothing to hide"
    assert dsn.masked() == "clickhouse://analyst@ch.local:9000/sales"


def test_password_is_masked_in_repr_and_str() -> None:
    dsn = parse_dsn("clickhouse://analyst:TopSecret@ch.local:9000/sales")
    assert "TopSecret" not in repr(dsn)
    assert "TopSecret" not in str(dsn)
    assert dsn.scrub("error with TopSecret in trace") == "error with *** in trace"


def test_quoting_helpers() -> None:
    assert quote_identifier('my"table') == '"my""table"'
    assert quote_literal("it's a test") == "'it''s a test'"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Password authentication failed for user sber", "учётные данные"),
        ("Authentication failed: password is incorrect", "учётные данные"),
        ("nodename nor servname provided, or not known", "Не удалось найти хост"),
        ("Connection refused", "недоступен"),
        ("Database missing_db doesn't exist", "база данных не существует"),
        ("Table default.missing doesn't exist", "таблица не существует"),
        ("Failed to connect to ClickHouse: Database `nope` does not exist", "база данных"),
        ("Failed to connect to ClickHouse: Connection refused", "недоступен"),
        ("Remote at port 9000 returned 401 rows", "Не удалось подключиться к ClickHouse."),
        ("Access denied for user", "Недостаточно прав"),
        ("Something completely unexpected", "Не удалось подключиться к ClickHouse."),
    ],
)
def test_describe_failure(text: str, expected: str) -> None:
    assert expected in describe_failure(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ClickHouse error 81: no such thing", "база данных не существует"),
        ("Code: 60. DB::Exception: oops", "таблица не существует"),
        ("Code: 516. DB::Exception: default: Authentication failed", "учётные данные"),
        ("Code: 193. DB::Exception: Wrong password", "учётные данные"),
        ("Code: 497. DB::Exception: Not enough privileges", "Недостаточно прав"),
        ("Code: 210. DB::NetException: Connection refused", "недоступен"),
        ("Code: 241. DB::Exception: Memory limit exceeded", "Не удалось подключиться"),
    ],
)
def test_describe_failure_by_clickhouse_error_code(text: str, expected: str) -> None:
    assert expected in describe_failure(text)


class FakeDuckDB:
    def __init__(
        self, tables=(), comments=(), failing=(), previous=(), stale_views=(), existing=()
    ) -> None:
        self.tables = list(tables)
        self.comments = list(comments)
        self.failing = failing
        self.previous = list(previous)
        self.stale_views = list(stale_views)
        self.existing = list(existing)
        self.statements: list[str] = []
        self.params: list[dict | None] = []

    def execute(self, sql: str, params: dict | None = None) -> None:
        self.statements.append(sql)
        self.params.append(params)
        for prefix, error in self.failing:
            if sql.startswith(prefix):
                raise error

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        self.statements.append(sql)
        self.params.append(params)
        for prefix, error in self.failing:
            if sql.startswith(prefix):
                raise error
        if "clickhouse_query(" in sql:
            return [{"column_name": name, "comment": text} for name, text in self.comments]
        if "FROM duckdb_tables()" in sql and "table_name AS name" in sql:
            return [{"name": table} for table in self.tables]
        if "contains(sql, $marker)" in sql:
            if any(params and params["marker"] == f" {alias}." for alias in self.previous):
                return [{"view_name": view} for view in self.stale_views]
            return []
        if "duckdb_tables()" in sql or "duckdb_views()" in sql:
            return [{"name": name} for name in self.existing]
        if "duckdb_databases()" in sql:
            return [{"database_name": alias} for alias in self.previous]
        return []

    def index_of(self, prefix: str) -> int:
        return next(
            position
            for position, statement in enumerate(self.statements)
            if statement.startswith(prefix)
        )


DSN = "clickhouse://analyst:TopSecret@ch.local:9000/analytics"


def test_connect_runs_install_load_attach_in_order_and_creates_views() -> None:
    db = FakeDuckDB(tables=["orders", "users"])
    connector = ClickHouseConnector(DSN)
    assert connector.connect(db) == ["orders", "users"]

    install = db.index_of("INSTALL clickhouse_scanner FROM community")
    load = db.index_of("LOAD clickhouse_scanner")
    attach = db.index_of("ATTACH ")
    assert install < load < attach
    assert db.statements[attach] == (
        "ATTACH 'host=ch.local port=9000 user=analyst dbname=analytics' "
        f"AS {connector.alias} (TYPE clickhouse, READ_ONLY, SECRET {connector.alias}_secret)"
    )
    assert (
        f'CREATE OR REPLACE TEMP VIEW "orders" AS SELECT * FROM {connector.alias}."analytics"."orders"'
        in db.statements
    )
    assert (
        f'CREATE OR REPLACE TEMP VIEW "users" AS SELECT * FROM {connector.alias}."analytics"."users"'
        in db.statements
    )


def test_secure_attach_includes_tls_option() -> None:
    db = FakeDuckDB(tables=["orders"])
    connector = ClickHouseConnector("clickhouses://analyst:TopSecret@ch.local/analytics")
    connector.connect(db)
    attach = next(statement for statement in db.statements if statement.startswith("ATTACH "))
    assert "secure=true" in attach


def test_password_goes_only_to_the_secret() -> None:
    db = FakeDuckDB(tables=["orders"])
    ClickHouseConnector(DSN).connect(db)
    assert not any("TopSecret" in statement for statement in db.statements)
    assert db.params[db.index_of("CREATE OR REPLACE TEMPORARY SECRET")] == {"password": "TopSecret"}


def test_no_secret_without_password() -> None:
    db = FakeDuckDB(tables=["orders"])
    ClickHouseConnector("clickhouse://analyst@ch.local:9000/analytics").connect(db)
    assert not any(
        "SECRET" in statement for statement in db.statements if statement.startswith("ATTACH")
    )
    assert not any(
        statement.startswith("CREATE OR REPLACE TEMPORARY SECRET") for statement in db.statements
    )


def test_missing_extension_raises_extension_unavailable_error() -> None:
    db = FakeDuckDB(failing=[("LOAD clickhouse_scanner", duckdb.Error("Extension not found"))])
    connector = ClickHouseConnector(DSN)
    with pytest.raises(ClickHouseExtensionUnavailableError, match="недоступно"):
        connector.connect(db)


def test_max_tables_limits_tables(caplog: pytest.LogCaptureFixture) -> None:
    db = FakeDuckDB(tables=["t1", "t2", "t3"])
    connector = ClickHouseConnector(DSN, max_tables=2)
    with caplog.at_level(logging.WARNING):
        views = connector.connect(db)
    assert views == ["t1", "t2"]
    assert "DB_MAX_TABLES" in caplog.text
    assert connector.warnings("t1") == [
        "Подключены первые 2 таблиц из-за ограничения DB_MAX_TABLES."
    ]


def test_name_collision_gets_prefixed() -> None:
    db = FakeDuckDB(tables=["users"], existing=["users"])
    connector = ClickHouseConnector(DSN)
    views = connector.connect(db)
    assert views == ["analytics_users"]


def test_column_comments_are_read_via_clickhouse_query() -> None:
    db = FakeDuckDB(tables=["orders"], comments=[("id", "Код заказа"), ("amount", "Сумма")])
    connector = ClickHouseConnector(DSN)
    views = connector.connect(db)
    assert views == ["orders"]
    comments = connector.column_comments(db, "orders")
    assert comments == {"id": "Код заказа", "amount": "Сумма"}
    assert any("clickhouse_query(" in stmt for stmt in db.statements)


def test_reconnect_cleans_views_and_databases() -> None:
    first_db = FakeDuckDB(tables=["orders"])
    first = ClickHouseConnector(DSN)
    first.connect(first_db)
    first_alias = first.alias

    second_db = FakeDuckDB(tables=["orders"], previous=[first_alias], stale_views=["orders"])
    second = ClickHouseConnector(DSN)
    second.connect(second_db)

    assert f"DETACH DATABASE IF EXISTS {first_alias}" in second_db.statements
    assert f"DROP TEMPORARY SECRET IF EXISTS {first_alias}_secret" in second_db.statements


def test_invalid_limits_raise_value_error() -> None:
    with pytest.raises(ValueError, match="Лимит таблиц"):
        ClickHouseConnector("clickhouse://localhost", max_tables=0)


def test_table_discovery_uses_clickhouse_catalog_command() -> None:
    db = FakeDuckDB(tables=["orders"])
    connector = ClickHouseConnector(DSN)
    connector.connect(db)
    discovery = next(statement for statement in db.statements if "table_name AS name" in statement)
    assert f"database_name = '{connector.alias}'" in discovery
    assert "$schema" in discovery
    assert not any("SHOW TABLES FROM" in statement for statement in db.statements)


def test_factory_builds_clickhouse_with_schema() -> None:
    connector = build_connector("clickhouse", "clickhouse://localhost/db1", schema="db2")
    assert isinstance(connector, ClickHouseConnector)
    assert connector.dsn.database == "db2"


def test_connector_scrub_masks_password() -> None:
    connector = ClickHouseConnector(DSN)
    assert connector.scrub("Binder Error: TopSecret") == "Binder Error: ***"


def test_warnings_is_a_method_and_empty_by_default() -> None:
    # BaseConnector.warnings(table) — метод; атрибут с тем же именем ломал бы его вызов.
    connector = ClickHouseConnector(DSN)
    connector.connect(FakeDuckDB(tables=["t1"]))
    assert connector.warnings("t1") == []


def test_max_tables_defaults_to_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "db_max_tables", 1)
    assert ClickHouseConnector(DSN).connect(FakeDuckDB(tables=["t1", "t2"])) == ["t1"]


def test_db_max_tables_wins_over_legacy_postgres_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_MAX_TABLES", "50")
    assert Settings(_env_file=None).db_max_tables == 50
    monkeypatch.setenv("DB_MAX_TABLES", "10")
    assert Settings(_env_file=None).db_max_tables == 10


def test_connector_repr() -> None:
    connector = ClickHouseConnector(DSN)
    assert repr(connector) == f"ClickHouseConnector(dsn={connector.dsn.masked()!r})"
    assert "TopSecret" not in repr(connector)


def test_empty_database_returns_empty_list_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    db = FakeDuckDB(tables=[])
    connector = ClickHouseConnector(DSN)
    with caplog.at_level(logging.WARNING):
        assert connector.connect(db) == []
    assert "нет таблиц" in caplog.text
    assert f"DETACH DATABASE IF EXISTS {connector.alias}" in db.statements


def test_name_collision_multi_suffix() -> None:
    connector = ClickHouseConnector(DSN)
    connector.alias = "ch_test"
    taken = {"orders", "analytics_orders", "ch_test_orders", "ch_test_orders_2"}
    view = connector.create_view(FakeDuckDB(), "orders", taken)
    assert view == "ch_test_orders_3"


def test_attach_failure_is_readable_and_secret_free(caplog: pytest.LogCaptureFixture) -> None:
    error = duckdb.IOException(
        "IO Error: Unable to connect: password authentication failed for user TopSecret"
    )
    db = FakeDuckDB(tables=["orders"], failing=[("ATTACH", error)])
    with caplog.at_level(logging.WARNING), pytest.raises(ClickHouseConnectionError) as raised:
        ClickHouseConnector(DSN).connect(db)
    message = str(raised.value)
    assert "учётные данные" in message
    assert "TopSecret" not in message
    assert "TopSecret" not in caplog.text
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_clickhouse_error_code_in_text_is_classified() -> None:
    assert "не существует" in describe_failure(
        "ClickHouse error 81 (UNKNOWN_DATABASE): Database missing does not exist"
    )
    assert "учётные данные" in describe_failure(
        "ClickHouse error 516 (AUTHENTICATION_FAILED): invalid credentials"
    )
    assert "Не удалось подключиться к ClickHouse." in describe_failure(
        "ClickHouse error 999 (UNKNOWN): unexpected failure"
    )
    assert "недоступен" in describe_failure("Failed to connect to ClickHouse at host:9000")


def test_create_view_failure_is_translated_and_released() -> None:
    error = duckdb.IOException("ClickHouse error 60: table does not exist")
    db = FakeDuckDB(tables=["orders"], failing=[("CREATE OR REPLACE TEMP VIEW", error)])
    connector = ClickHouseConnector(DSN)
    with pytest.raises(ClickHouseConnectionError, match="таблица"):
        connector.connect(db)
    assert f"DETACH DATABASE IF EXISTS {connector.alias}" in db.statements


def test_previous_aliases_are_limited_to_same_clickhouse_identity() -> None:
    db = FakeDuckDB(previous=["ch_other_123", "ch_same_123"])
    connector = ClickHouseConnector(DSN)
    connector.alias = "ch_same_current"
    connector.identity = "ch_same"
    connector.previous_aliases(db)
    statement = next(statement for statement in db.statements if "duckdb_databases()" in statement)
    assert "type = 'clickhouse' AND starts_with" in statement


def test_unreachable_server_message() -> None:
    error = duckdb.IOException("Connection refused")
    db = FakeDuckDB(failing=[("ATTACH", error)])
    with pytest.raises(ClickHouseConnectionError, match="недоступен"):
        ClickHouseConnector(DSN).connect(db)


def test_extension_offline_install_is_tolerated() -> None:
    db = FakeDuckDB(tables=["orders"], failing=[("INSTALL", duckdb.IOException("offline"))])
    assert ClickHouseConnector(DSN).connect(db) == ["orders"]


def test_failure_while_listing_tables_is_readable_and_releases_attachment() -> None:
    error = duckdb.CatalogException("permission denied for database private")
    db = FakeDuckDB(failing=[("SELECT table_name AS name", error)])
    connector = ClickHouseConnector(DSN)
    with pytest.raises(ClickHouseConnectionError, match="Недостаточно прав"):
        connector.connect(db)
    assert f"DETACH DATABASE IF EXISTS {connector.alias}" in db.statements
    assert f"DROP TEMPORARY SECRET IF EXISTS {connector.alias}_secret" in db.statements


def test_failed_cleanup_does_not_mask_connection_error(caplog: pytest.LogCaptureFixture) -> None:
    db = FakeDuckDB(
        failing=[
            ("ATTACH", duckdb.IOException("Connection refused")),
            ("DETACH", duckdb.IOException("cleanup broke")),
        ]
    )
    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(ClickHouseConnectionError, match="недоступен"),
    ):
        ClickHouseConnector(DSN).connect(db)
    assert "не удалось освободить" in caplog.text


def test_column_comments_for_unknown_view_skip_the_source() -> None:
    db = FakeDuckDB(tables=["orders"])
    connector = ClickHouseConnector(DSN)
    assert connector.column_comments(db, "orders") == {}
    connector.connect(db)
    issued = len(db.statements)
    assert connector.column_comments(db, "not_from_clickhouse") == {}
    assert len(db.statements) == issued


def test_rollback_releases_attached_source_and_is_safe_before_connect() -> None:
    db = FakeDuckDB(tables=["orders"])
    connector = ClickHouseConnector(DSN)

    connector.rollback(db)
    assert db.statements == []

    connector.connect(db)
    connector.rollback(db)
    assert any(statement.startswith("DETACH DATABASE") for statement in db.statements)


def test_reconnect_forgets_previous_views() -> None:
    connector = ClickHouseConnector(DSN)
    connector.connect(FakeDuckDB(tables=["orders", "users"]))
    connector.connect(FakeDuckDB(tables=["orders"]))
    assert connector.sources == {"orders": "orders"}


def test_column_comments_failure_is_logged_without_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = duckdb.IOException("connection lost for TopSecret")
    db = FakeDuckDB(tables=["orders"], failing=[("SELECT column_name, comment FROM", error)])
    connector = ClickHouseConnector(DSN)
    connector.connect(db)
    with caplog.at_level(logging.WARNING):
        assert connector.column_comments(db, "orders") == {}
    assert "комментарии колонок «orders»" in caplog.text
    assert "TopSecret" not in caplog.text
