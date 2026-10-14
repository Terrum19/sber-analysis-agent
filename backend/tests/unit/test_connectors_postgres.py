import pytest

from app.core.data.connectors.pg_dsn import parse_dsn


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
    assert dsn.connection_string() == (
        "host=localhost dbname='my db' user=bob connect_timeout=3"
    )


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
