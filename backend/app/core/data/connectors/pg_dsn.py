from dataclasses import dataclass, field
from urllib.parse import parse_qsl, unquote, urlsplit

URI_SCHEMES = ("postgresql", "postgres")
DEFAULT_CONNECT_TIMEOUT_S = "10"
MASK = "***"


def quote_value(value: str) -> str:
    if value and not any(symbol.isspace() or symbol in "'\\" for symbol in value):
        return value
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


@dataclass(frozen=True)
class PostgresDsn:
    params: dict[str, str]
    password: str | None = field(default=None, repr=False)

    @property
    def host(self) -> str:
        return self.params.get("host") or self.params.get("hostaddr", "")

    def connection_string(self) -> str:
        return " ".join(f"{key}={quote_value(value)}" for key, value in self.params.items())

    def masked(self) -> str:
        user = self.params.get("user", "")
        secret = f":{MASK}" if self.password else ""
        auth = f"{user}{secret}@" if user or secret else ""
        port = self.params.get("port")
        location = f"{self.host}:{port}" if port else self.host
        return f"postgresql://{auth}{location}/{self.params.get('dbname', '')}"

    def scrub(self, text: str) -> str:
        if not self.password:
            return text
        return text.replace(self.password, MASK)

    def __repr__(self) -> str:
        return f"PostgresDsn({self.masked()!r})"

    def __str__(self) -> str:
        return self.masked()


def parse_uri(raw: str) -> dict[str, str]:
    parts = urlsplit(raw)
    if parts.scheme.lower() not in URI_SCHEMES:
        raise ValueError(
            f"Схема «{parts.scheme}» не поддерживается: ожидается postgresql:// или postgres://."
        )
    try:
        port = parts.port
    except ValueError:
        raise ValueError("В строке подключения PostgreSQL указан некорректный порт.") from None
    params: dict[str, str] = {}
    if parts.hostname:
        params["host"] = unquote(parts.hostname)
    if port is not None:
        params["port"] = str(port)
    if parts.username:
        params["user"] = unquote(parts.username)
    if parts.password is not None:
        params["password"] = unquote(parts.password)
    dbname = unquote(parts.path.lstrip("/"))
    if dbname:
        params["dbname"] = dbname
    params.update(parse_qsl(parts.query, keep_blank_values=True))
    return params


def parse_keywords(raw: str) -> dict[str, str]:
    params: dict[str, str] = {}
    length = len(raw)
    cursor = 0
    while True:
        while cursor < length and raw[cursor].isspace():
            cursor += 1
        if cursor >= length:
            return params
        equals = raw.find("=", cursor)
        key = raw[cursor:equals].strip() if equals != -1 else ""
        if not key or any(symbol.isspace() for symbol in key):
            raise ValueError("Строка подключения PostgreSQL должна иметь вид «ключ=значение ...».")
        cursor = equals + 1
        while cursor < length and raw[cursor].isspace():
            cursor += 1
        if cursor < length and raw[cursor] == "'":
            cursor += 1
            chars: list[str] = []
            while True:
                if cursor >= length:
                    raise ValueError("В строке подключения PostgreSQL не закрыта кавычка.")
                symbol = raw[cursor]
                if symbol == "\\" and cursor + 1 < length:
                    chars.append(raw[cursor + 1])
                    cursor += 2
                    continue
                cursor += 1
                if symbol == "'":
                    break
                chars.append(symbol)
            value = "".join(chars)
        else:
            start = cursor
            while cursor < length and not raw[cursor].isspace():
                cursor += 1
            value = raw[start:cursor]
        params[key] = value


def parse_dsn(raw: str) -> PostgresDsn:
    text = (raw or "").strip()
    if not text:
        raise ValueError("Строка подключения PostgreSQL пуста.")
    if "://" in text:
        params = parse_uri(text)
    elif "=" in text:
        params = parse_keywords(text)
    else:
        raise ValueError(
            "Не удалось разобрать строку подключения PostgreSQL: ожидается "
            "postgresql://user:password@host:port/db или host=... dbname=..."
        )
    password = params.pop("password", None)
    dsn = PostgresDsn(params=params, password=password or None)
    if not dsn.host:
        raise ValueError("В строке подключения PostgreSQL не указан хост.")
    params.setdefault("connect_timeout", DEFAULT_CONNECT_TIMEOUT_S)
    return dsn
