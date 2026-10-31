"""WASM-песочница для кода, сгенерированного LLM.

Вместо Docker (слишком тяжёлого для локального запуска): Python в Pyodide
(WASM) под Node.js с лимитами RAM и жёстким таймаутом. Замкнутый контур — без сети.

Слои изоляции:
- код видит только файловую систему Pyodide в памяти и одну смонтированную
  папку /sandbox (своя временная папка на каждый запуск);
- Node.js запускается с моделью разрешений: чтение — только рантайм Pyodide
  и папка запуска, запись — только папка запуска, без дочерних процессов,
  воркеров и нативных аддонов;
- мост в JavaScript (js, pyodide.code, pyodide.ffi, pyodide.http), сокеты,
  subprocess, os.system и симлинки внутри Pyodide отключены;
- если ядро разрешает непривилегированные user namespaces, процесс ещё и
  запускается в пустом сетевом пространстве имён (unshare -rn);
- RSS процесса не больше лимита (и не больше свободной RAM системы),
  одновременно идёт не больше wasm_max_parallel запусков, приоритет понижен.
"""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache
from pathlib import Path

from app.config import settings

RUNTIME_DIR = Path(__file__).resolve().parents[3] / "sandbox"
INPUT_FILE = "input.csv"
RESULT_FILE = "result.csv"
POLL_S = 0.1
STDERR_TAIL = 2000

_slots = threading.BoundedSemaphore(max(1, settings.wasm_max_parallel))


class SandboxError(RuntimeError):
    """Код в песочнице упал, превысил лимиты или рантайм недоступен."""


@cache
def _permission_flag(node: str) -> str:
    """Флаг модели разрешений Node.js: --permission с 22-й версии."""
    version = subprocess.run([node, "--version"], capture_output=True, text=True, check=True)
    major = int(version.stdout.strip().lstrip("v").split(".")[0])
    if major < 20:
        raise SandboxError(f"Node.js >= 20 is required for the sandbox, found {version.stdout}")
    return "--permission" if major >= 22 else "--experimental-permission"


@cache
def _netns_prefix() -> tuple[str, ...]:
    """unshare -rn, если ядро позволяет создать пустой сетевой namespace."""
    unshare = shutil.which("unshare")
    if unshare is None:
        return ()
    probe = subprocess.run([unshare, "-rn", "true"], check=False, capture_output=True)
    return (unshare, "-rn") if probe.returncode == 0 else ()


def available_mb() -> int:
    """Свободная RAM системы (MemAvailable) в мегабайтах."""
    try:
        with open("/proc/meminfo") as meminfo:
            for line in meminfo:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // 2**20


def _rss_mb(pid: int) -> int:
    """Резидентная память процесса в мегабайтах (0, если процесса уже нет)."""
    try:
        with open(f"/proc/{pid}/status") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


@cache
def _nice_prefix() -> tuple[str, ...]:
    """nice -n 10, чтобы песочница не мешала интерактивной работе."""
    nice = shutil.which("nice")
    return (nice, "-n", "10") if nice else ()


class WASMSandbox:
    """Исполнение кода агента в изолированном WASM-окружении."""

    def __init__(
        self,
        timeout_s: int | None = None,
        mem_mb: int | None = None,
        runtime_dir: Path | None = None,
        node: str | None = None,
    ) -> None:
        self.timeout_s = timeout_s or settings.wasm_timeout_s
        self.mem_mb = mem_mb or settings.wasm_mem_mb
        self.runtime_dir = Path(runtime_dir or settings.wasm_runtime_dir or RUNTIME_DIR)
        self.node = node or settings.wasm_node_bin

    def available(self) -> bool:
        """Установлены ли Node.js и рантайм Pyodide."""
        return (
            shutil.which(self.node) is not None
            and (self.runtime_dir / "node_modules" / "pyodide" / "pyodide.mjs").is_file()
        )

    @contextmanager
    def workspace(self) -> Iterator[Path]:
        """Отдельная временная папка запуска; удаляется после выхода."""
        root = Path(settings.storage_dir) / "sandbox"
        root.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix="run_", dir=root)).resolve()
        try:
            yield workdir
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def limit_mb(self) -> int:
        """Лимит RSS: настройка, но не больше свободной RAM системы."""
        return max(64, min(self.mem_mb, available_mb()))

    def command(self, workdir: Path) -> list[str]:
        """Командная строка запуска runner.mjs с моделью разрешений Node.js."""
        runtime = self.runtime_dir.resolve()
        return [
            *_nice_prefix(),
            *_netns_prefix(),
            shutil.which(self.node) or self.node,
            "--no-warnings",
            _permission_flag(self.node),
            f"--allow-fs-read={runtime / 'runner.mjs'}",
            f"--allow-fs-read={runtime / 'package.json'}",
            f"--allow-fs-read={runtime / 'node_modules' / 'pyodide'}",
            f"--allow-fs-read={workdir}",
            f"--allow-fs-write={workdir}",
            str(runtime / "runner.mjs"),
            str(workdir),
        ]

    def run(self, code: str, workdir: Path) -> dict:
        """Выполнить код с лимитами ресурсов и таймаутом.

        В коде доступен DataFrame df из workdir/input.csv; результат —
        переменная result, которую раннер сохраняет в workdir/result.csv.
        Возвращает ответ раннера: {"ok": True, "stdout": ...}.
        """
        if not self.available():
            raise SandboxError(f"Pyodide runtime is not installed in {self.runtime_dir}")
        with _slots:
            return self._spawn(code, Path(workdir).resolve())

    def _spawn(self, code: str, workdir: Path) -> dict:
        limit = self.limit_mb()
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(
                self.command(workdir),
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                cwd=workdir,
                env={"PATH": os.environ.get("PATH", ""), "HOME": str(workdir)},
                start_new_session=True,
            )
            process.stdin.write(json.dumps({"code": code}).encode())
            process.stdin.close()
            reason = self._watch(process, limit)
            stdout.seek(0)
            stderr.seek(0)
            out = stdout.read().decode(errors="replace")
            err = stderr.read().decode(errors="replace")[-STDERR_TAIL:]
        if reason is not None:
            raise SandboxError(reason)
        try:
            reply = json.loads(out.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            raise SandboxError(
                f"sandbox exited with code {process.returncode}: {err.strip()}"
            ) from None
        if not reply.get("ok"):
            raise SandboxError(reply.get("error") or "sandbox failed")
        return reply

    def _watch(self, process: subprocess.Popen, limit: int) -> str | None:
        """Ждать завершения; при таймауте или перерасходе RAM убить группу."""
        deadline = time.monotonic() + self.timeout_s
        while process.poll() is None:
            if time.monotonic() > deadline:
                reason = f"sandbox timed out after {self.timeout_s} s"
            elif _rss_mb(process.pid) > limit:
                reason = f"sandbox exceeded the memory limit of {limit} MB"
            else:
                time.sleep(POLL_S)
                continue
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            return reason
        return None
