"""Скачать колёса Pyodide для песочницы рядом с рантаймом (один раз, на сборке).

Во время исполнения песочница сети не имеет, поэтому пакеты анализа данных
должны лежать в node_modules/pyodide заранее.
"""

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

PACKAGES = ("numpy", "pandas", "scipy", "scikit-learn", "statsmodels")
RUNTIME = Path(__file__).resolve().parent / "node_modules" / "pyodide"
CDN = "https://cdn.jsdelivr.net/pyodide/v{version}/full/"


def resolve(lock: dict, names: tuple[str, ...]) -> set[str]:
    """Пакеты вместе со всеми зависимостями по pyodide-lock.json."""
    pending = list(names)
    found: set[str] = set()
    while pending:
        name = pending.pop()
        if name in found:
            continue
        found.add(name)
        pending.extend(lock["packages"][name]["depends"])
    return found


def main() -> int:
    lock = json.loads((RUNTIME / "pyodide-lock.json").read_text())
    version = json.loads((RUNTIME / "package.json").read_text())["version"]
    base = CDN.format(version=version)
    for name in sorted(resolve(lock, PACKAGES)):
        entry = lock["packages"][name]
        target = RUNTIME / entry["file_name"]
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == entry["sha256"]:
            continue
        with urllib.request.urlopen(base + entry["file_name"], timeout=120) as response:
            body = response.read()
        if hashlib.sha256(body).hexdigest() != entry["sha256"]:
            print(f"sha256 mismatch: {entry['file_name']}", file=sys.stderr)
            return 1
        target.write_bytes(body)
        print(entry["file_name"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
