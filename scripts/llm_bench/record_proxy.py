#!/usr/bin/env python3
"""Записывающий прокси между агентом и OpenAI-совместимым LLM (Ollama).

Ничего не меняет в трафике: принимает запросы, пересылает upstream и
пишет в JSONL каждый вызов — запрос, ответ, время, usage. По логу потом
видно, сколько токенов в промптах, сколько вызовов делает один ход и
какие ответы ломают парсинг. Только stdlib — ставить ничего не нужно.

С --retry N ошибки провайдера (429, 5xx и 200 с "error" в теле без
"choices", как у бесплатных моделей OpenRouter) повторяются до N раз с
паузой: прогон eval не падает из-за чужих лимитов.

Запуск:
    python record_proxy.py --port 11500 --upstream http://localhost:11434 \
        --log calls.jsonl
    python record_proxy.py --upstream https://openrouter.ai/api --retry 12 --log calls.jsonl
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


def _transient(status: int, payload: bytes) -> bool:
    """Ошибка провайдера, которую стоит повторить."""
    if status in (429, 500, 502, 503):
        return True
    return status == 200 and b'"error"' in payload[:300] and b'"choices"' not in payload


def make_handler(upstream: str, log_path: Path, retry: int = 0) -> type[BaseHTTPRequestHandler]:
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
            attempt = 0
            while True:
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
                if attempt >= retry or not _transient(status, payload):
                    break
                attempt += 1
                time.sleep(min(5 * attempt, 30))
            if attempt:
                record["retries"] = attempt
            if status == 200 and _transient(status, payload):
                # Ошибка в теле с кодом 200: клиент должен увидеть сбой, а не пустой ответ.
                status = 502
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
    parser.add_argument("--retry", type=int, default=0, help="повторы ошибок провайдера")
    args = parser.parse_args()
    handler = make_handler(args.upstream, args.log, args.retry)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
