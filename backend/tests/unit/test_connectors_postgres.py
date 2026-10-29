import logging

import duckdb
import pytest

from app.config import settings
from app.core.data.connectors.clickhouse import ClickHouseConnector
from app.core.data.connectors.factory import build_connector
from app.core.data.connectors.pg_dsn import parse_dsn
from app.core.data.connectors.postgres import (
    PostgresConnectionError,
    PostgresConnector,
    PostgresExtensionUnavailableError,
    describe_failure,
)
from app.core.data.relations import Relation


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
        "host=localhost dbname",
        "host=localhost =shop",
    ],
)
def test_invalid_dsn_raises_value_error(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_dsn(raw)


def test_keyword_dsn_allows_spaces_around_equals() -> None:
    dsn = parse_dsn("host = db.local   dbname =shop user=  bob")
    assert dsn.params["host"] == "db.local"
    assert dsn.params["dbname"] == "shop"
    assert dsn.params["user"] == "bob"


def test_scrub_without_password_is_identity() -> None:
    dsn = parse_dsn("postgresql://analyst@db.local/shop")
    assert dsn.password is None
    assert dsn.scrub("nothing to hide") == "nothing to hide"
    assert dsn.masked() == "postgresql://analyst@db.local/shop"


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
    def __init__(
        self,
        tables=(),
        existing=(),
        failing=(),
        previous=(),
        stale_views=(),
        comments=(),
        keys=(),
    ) -> None:
        self.tables = list(tables)
        self.keys = list(keys)
        self.comments = list(comments)
        self.existing = list(existing)
        self.failing = failing
        self.previous = list(previous)
        self.stale_views = list(stale_views)
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
        if "pg_constraint" in sql:
            return [
                {"table_name": table, "ref_table": ref, "columns": cols, "ref_columns": refs}
                for table, ref, cols, refs in self.keys
            ]
        if "postgres_query(" in sql:
            return [{"column_name": name, "comment": text} for name, text in self.comments]
        if "information_schema.tables" in sql:
            return [{"table_name": table} for table in self.tables]
        if "duckdb_tables()" in sql:
            return [{"name": name} for name in self.existing]
        if "duckdb_databases()" in sql:
            return [{"database_name": alias} for alias in self.previous]
        if "contains(sql, $marker)" in sql and any(
            params and params["marker"] == f" {alias}." for alias in self.previous
        ):
            return [{"view_name": view} for view in self.stale_views]
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
    assert not any("TopSecret" in statement for statement in db.statements)
    assert db.params[db.index_of("CREATE OR REPLACE TEMPORARY SECRET")] == {"password": "TopSecret"}


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
    assert "DB_MAX_TABLES" in caplog.text


def test_max_tables_defaults_to_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "db_max_tables", 2)
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


def test_successful_reconnect_releases_previous_alias_after_listing() -> None:
    connector = PostgresConnector(DSN)
    old_alias = f"{connector.identity}_0ld0ld"
    db = FakeDuckDB(tables=["orders"], previous=[old_alias], stale_views=["dropped table"])
    assert connector.connect(db) == ["orders"]
    lookup = db.index_of("SELECT database_name FROM duckdb_databases()")
    assert db.params[lookup] == {"prefix": f"{connector.identity}_", "current": connector.alias}
    drop_stale = db.statements.index('DROP VIEW IF EXISTS temp.main."dropped table"')
    detach_old = db.statements.index(f"DETACH DATABASE IF EXISTS {old_alias}")
    drop_secret = db.statements.index(f"DROP TEMPORARY SECRET IF EXISTS {old_alias}_secret")
    listing = db.index_of("SELECT table_name FROM")
    create = db.index_of("CREATE OR REPLACE TEMP VIEW")
    assert listing < drop_stale < detach_old < drop_secret < create
    assert f"DETACH DATABASE IF EXISTS {connector.alias}" not in db.statements


def test_reconnect_to_empty_schema_still_releases_previous_alias() -> None:
    connector = PostgresConnector(DSN)
    old_alias = f"{connector.identity}_0ld0ld"
    db = FakeDuckDB(tables=[], previous=[old_alias], stale_views=["orders"])
    assert connector.connect(db) == []
    assert 'DROP VIEW IF EXISTS temp.main."orders"' in db.statements
    assert f"DETACH DATABASE IF EXISTS {old_alias}" in db.statements
    assert f"DETACH DATABASE IF EXISTS {connector.alias}" in db.statements


def test_failure_while_listing_tables_is_readable_and_releases_attachment() -> None:
    error = duckdb.CatalogException("permission denied for schema private")
    db = FakeDuckDB(failing=[("SELECT table_name FROM", error)])
    connector = PostgresConnector(DSN, schema="private")
    with pytest.raises(PostgresConnectionError, match="Недостаточно прав"):
        connector.connect(db)
    assert f"DETACH DATABASE IF EXISTS {connector.alias}" in db.statements
    assert f"DROP TEMPORARY SECRET IF EXISTS {connector.alias}_secret" in db.statements
    assert not any(
        statement.startswith("CREATE OR REPLACE TEMP VIEW") for statement in db.statements
    )


def test_failed_cleanup_does_not_mask_connection_error(caplog: pytest.LogCaptureFixture) -> None:
    db = FakeDuckDB(
        failing=[
            ("ATTACH", duckdb.IOException("Connection refused")),
            ("DETACH", duckdb.IOException("cleanup broke")),
        ]
    )
    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(PostgresConnectionError, match="недоступен"),
    ):
        PostgresConnector(DSN).connect(db)
    assert "не удалось освободить" in caplog.text


def test_case_insensitive_duplicates_get_numbered_names() -> None:
    connector = PostgresConnector(DSN)
    db = FakeDuckDB(tables=["Users", "users"], existing=["users", "shop_users"])
    views = connector.connect(db)
    assert views == [f"{connector.alias}_Users", f"{connector.alias}_users_2"]
    assert len({view.lower() for view in views}) == len(views)
    assert (
        f'CREATE OR REPLACE TEMP VIEW "{connector.alias}_users_2" AS SELECT * FROM '
        f'{connector.alias}."public"."users"'
    ) in db.statements


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('FATAL:  password authentication failed for user "bob"', "учётные данные"),
        ("fe_sendauth: no password supplied", "учётные данные"),
        ('could not translate host name "nope" to address', "Не удалось найти хост"),
        ("Name or service not known", "Не удалось найти хост"),
        ("Connection refused", "недоступен"),
        ("timeout expired", "недоступен"),
        ("Network is unreachable", "недоступен"),
        ("server closed the connection unexpectedly", "недоступен"),
        ('FATAL:  database "shop" does not exist', "не существует"),
        ("permission denied for schema private", "Недостаточно прав"),
        ("something completely different", "Не удалось подключиться к PostgreSQL."),
    ],
)
def test_describe_failure(text: str, expected: str) -> None:
    assert expected in describe_failure(text)


def test_column_comments_are_read_from_source_table() -> None:
    db = FakeDuckDB(
        tables=["events", "it's"],
        existing=["events"],
        comments=[("amount", "Сумма, руб."), ("note", "Комментарий")],
    )
    connector = PostgresConnector(DSN, schema="O'Hara")
    assert connector.connect(db) == ["shop_events", "it's"]
    assert connector.column_comments(db, "shop_events") == {
        "amount": "Сумма, руб.",
        "note": "Комментарий",
    }
    sql = db.statements[-1]
    # Только SELECT * без внешнего WHERE: DuckDB 2.0 путает колонки postgres_query
    # при проекции и фильтре снаружи, поэтому фильтр — внутри запроса PostgreSQL.
    assert sql.startswith(f"SELECT * FROM postgres_query('{connector.alias}', ")
    assert "n.nspname = ''O''''Hara'' AND c.relname = ''events''" in sql
    assert sql.endswith("col_description(a.attrelid, a.attnum) IS NOT NULL')")
    connector.column_comments(db, "it's")
    assert "c.relname = ''it''''s''" in db.statements[-1]


def test_column_comments_for_unknown_view_skip_the_source() -> None:
    db = FakeDuckDB(tables=["orders"])
    connector = PostgresConnector(DSN)
    assert connector.column_comments(db, "orders") == {}
    connector.connect(db)
    issued = len(db.statements)
    assert connector.column_comments(db, "not_from_postgres") == {}
    assert len(db.statements) == issued


def test_reconnect_forgets_previous_views() -> None:
    connector = PostgresConnector(DSN)
    connector.connect(FakeDuckDB(tables=["orders", "users"]))
    connector.connect(FakeDuckDB(tables=["orders"]))
    assert connector.sources == {"orders": "orders"}


def test_column_comments_failure_is_logged_without_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = duckdb.IOException("connection lost for TopSecret")
    db = FakeDuckDB(tables=["orders"], failing=[("SELECT * FROM postgres_query(", error)])
    connector = PostgresConnector(DSN)
    connector.connect(db)
    with caplog.at_level(logging.WARNING):
        assert connector.column_comments(db, "orders") == {}
    assert "комментарии колонок «orders»" in caplog.text
    assert "TopSecret" not in caplog.text


def test_connectors_without_comments_return_empty_dict() -> None:
    db = FakeDuckDB()
    assert ClickHouseConnector("clickhouse://localhost").column_comments(db, "events") == {}
    assert db.statements == []


def test_factory_builds_known_kinds_and_rejects_unknown() -> None:
    assert isinstance(build_connector("postgres", DSN, "sales"), PostgresConnector)
    assert build_connector("postgres", DSN, "sales").schema == "sales"
    assert isinstance(build_connector("clickhouse", "clickhouse://localhost"), ClickHouseConnector)
    with pytest.raises(ValueError, match="mysql"):
        build_connector("mysql", DSN)


def test_unreadable_table_is_skipped_and_its_view_dropped(caplog: pytest.LogCaptureFixture) -> None:
    db = FakeDuckDB(tables=["orders", "secret"])
    connector = PostgresConnector(DSN)
    connector.connect(db)
    error = duckdb.Error("permission denied for table secret, password=TopSecret")
    with caplog.at_level(logging.WARNING):
        assert connector.skip(db, "secret", error)
    assert 'DROP VIEW IF EXISTS temp.main."secret"' in db.statements
    assert "secret" not in connector.sources
    assert "нет прав" in caplog.text
    assert "TopSecret" not in caplog.text
    assert not connector.skip(db, "orders", duckdb.Error("Out of Memory"))


def test_rollback_releases_views_and_attachment() -> None:
    db = FakeDuckDB(tables=["orders"])
    connector = PostgresConnector(DSN)
    connector.connect(db)
    connector.rollback(db)
    assert f"DETACH DATABASE IF EXISTS {connector.alias}" in db.statements
    assert f"DROP TEMPORARY SECRET IF EXISTS {connector.alias}_secret" in db.statements
    assert connector.sources == {}
    assert connector.scrub("auth TopSecret") == "auth ***"


def test_foreign_keys_are_read_from_the_schema_under_view_names() -> None:
    db = FakeDuckDB(
        tables=["orders", "clients", "events"],
        existing=["events"],
        keys=[
            ("orders", "clients", '["buyer"]', '["code"]'),
            ("events", "orders", '["city", "num"]', '["shop_city", "shop_num"]'),
            ("orders", "archived", '["old"]', '["id"]'),
        ],
    )
    connector = PostgresConnector(DSN, schema="O'Hara")
    connector.connect(db)
    assert connector.foreign_keys(db) == [
        Relation("orders", ("buyer",), "clients", ("code",)),
        Relation("shop_events", ("city", "num"), "orders", ("shop_city", "shop_num")),
    ]
    sql = db.statements[-1]
    assert sql.startswith(f"SELECT * FROM postgres_query('{connector.alias}', ")
    assert "sn.nspname = ''O''''Hara'' AND dn.nspname = ''O''''Hara''" in sql


def test_foreign_keys_need_attached_tables() -> None:
    db = FakeDuckDB()
    assert PostgresConnector(DSN).foreign_keys(db) == []
    assert db.statements == []


def test_foreign_keys_failure_is_logged_without_secrets(caplog: pytest.LogCaptureFixture) -> None:
    error = duckdb.IOException("permission denied for TopSecret")
    db = FakeDuckDB(tables=["orders"], failing=[("SELECT * FROM postgres_query(", error)])
    connector = PostgresConnector(DSN)
    connector.connect(db)
    with caplog.at_level(logging.WARNING):
        assert connector.foreign_keys(db) == []
    assert "внешние ключи" in caplog.text
    assert "TopSecret" not in caplog.text


def test_connectors_without_schema_keys_return_empty_list() -> None:
    db = FakeDuckDB()
    assert ClickHouseConnector("clickhouse://localhost").foreign_keys(db) == []
    assert db.statements == []
