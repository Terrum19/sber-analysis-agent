#!/usr/bin/env python3
"""Записывающий прокси между агентом и OpenAI-совместимым LLM (Ollama).

Ничего не меняет в трафике: принимает запросы, пересылает upstream и
пишет в JSONL каждый вызов — запрос, ответ, время, usage. По логу потом
видно, сколько токенов в промптах, сколько вызовов делает один ход и
какие ответы ломают парсинг. Только stdlib — ставить ничего не нужно.

Запуск:
    python record_proxy.py --port 11500 --upstream http://localhost:11434 \
        --log calls.jsonl
    LLM_BASE_URL=http://localhost:11500/v1 python scripts/run_full_llm_cycle.py ...
"""

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_lock = threading.Lock()
_counter = [0]


def make_handler(upstream: str, log_path: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._forward(None)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            self._forward(self.rfile.read(length))

        def _forward(self, body: bytes | None) -> None:
            with _lock:
                _counter[0] += 1
                call_id = _counter[0]
            started = time.time()
            record: dict = {"id": call_id, "path": self.path, "started": started}
            try:
                record["request"] = json.loads(body) if body else None
            except ValueError:
                record["request"] = body.decode("utf-8", "replace") if body else None
            skip = ("host", "content-length", "connection", "accept-encoding")
            headers = {k: v for k, v in self.headers.items() if k.lower() not in skip}
            req = urllib.request.Request(
                upstream + self.path, data=body, headers=headers, method=self.command
            )
            try:
                with urllib.request.urlopen(req, timeout=None) as resp:
                    status, payload = resp.status, resp.read()
                    ctype = resp.headers.get("Content-Type", "application/json")
            except urllib.error.HTTPError as exc:
                status, payload = exc.code, exc.read()
                ctype = exc.headers.get("Content-Type", "text/plain")
            except Exception as exc:  # noqa: BLE001 — пишем в лог и отдаём 502
                record.update(error=repr(exc), seconds=time.time() - started)
                _append(log_path, record)
                self.send_error(502, str(exc))
                return
            record["seconds"] = time.time() - started
            record["status"] = status
            try:
                record["response"] = json.loads(payload)
            except ValueError:
                record["response"] = payload.decode("utf-8", "replace")[:5000]
            try:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                # Клиент не дождался (таймаут) — upstream работу уже сделал зря.
                record["client_disconnected"] = True
            _append(log_path, record)

        def log_message(self, *args) -> None:  # тихо
            pass

    return Handler


def _append(path: Path, record: dict) -> None:
    with _lock, path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=11500)
    parser.add_argument("--upstream", default="http://localhost:11434")
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.upstream, args.log))
    server.serve_forever()


if __name__ == "__main__":
    main()
