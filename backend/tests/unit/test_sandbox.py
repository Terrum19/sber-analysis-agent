"""WASM-песочница (Pyodide) и кодогенерация шагов без подходящего тула."""

from pathlib import Path

import pytest

from app.config import settings
from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.core.sandbox import codegen_runner
from app.core.sandbox.codegen_runner import extract_code, run_generated_code
from app.core.sandbox.wasm_runtime import SandboxError, WASMSandbox
from app.models.research_step import ResearchStep

needs_runtime = pytest.mark.skipif(
    not WASMSandbox().available(),
    reason="Pyodide runtime is not installed (cd backend/sandbox && npm ci && python fetch_packages.py)",
)

ESCAPES = """
import os
attempts = {
    "read_etc_passwd": lambda: open("/etc/passwd").read(),
    "read_host_file": lambda: open(HOST_FILE).read(),
    "write_outside": lambda: open(OUTSIDE_FILE, "w").write("pwned"),
    "symlink": lambda: os.symlink("/etc", "link"),
    "system": lambda: os.system("touch " + OUTSIDE_FILE),
    "popen": lambda: os.popen("id").read(),
    "subprocess": lambda: __import__("subprocess").run(["id"]),
    "socket": lambda: __import__("socket").create_connection(("1.1.1.1", 80), timeout=2),
    "raw_socket": lambda: [cls for cls in object.__subclasses__() if cls.__name__ == "socket"][0]()
        .connect(("1.1.1.1", 80)),
    "urllib": lambda: __import__("urllib.request").request.urlopen("http://1.1.1.1", timeout=2),
    "import_js": lambda: __import__("js"),
    "run_js": lambda: __import__("pyodide.code").code.run_js("process.exit(0)"),
    "pyodide_http": lambda: __import__("pyodide.http"),
    "micropip": lambda: __import__("micropip"),
}
rows = []
for name, attempt in attempts.items():
    try:
        outcome, escaped = repr(attempt()), True
    except BaseException as exc:
        outcome, escaped = type(exc).__name__, False
    rows.append({"attempt": name, "escaped": escaped, "outcome": outcome[:200]})
result = rows
"""


class FakeLLM:
    """LLM-заглушка: выдаёт заготовленные ответы по очереди и запоминает промпты."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def complete(self, system: str, user: str, **kwargs) -> str:
        self.prompts.append(user)
        return self.answers.pop(0)


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE raw AS SELECT * FROM (VALUES (1, 'a', 10.0), (2, 'b', 20.0), "
        "(3, 'a', 30.0), (4, 'b', 40.0)) AS t(id, segment, amount)"
    )
    yield manager
    manager.close()


@pytest.fixture(autouse=True)
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    return tmp_path


def fenced(code: str) -> str:
    return f"Вот код:\n```python\n{code}\n```\nГотово."


def test_extract_code_strips_fences() -> None:
    assert extract_code(fenced("result = df")) == "result = df"
    assert extract_code("```\nresult = 1\n```") == "result = 1"
    assert extract_code("result = df.head()") == "result = df.head()"


async def test_unknown_tool_goes_to_codegen(
    db: DuckDBManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    async def fake_runner(step, input_table, manager, llm, sandbox):
        calls.append((step.tool, input_table))
        return "temp_result_v9"

    monkeypatch.setattr(codegen_runner, "run_generated_code", fake_runner)
    executor = StepExecutor(db)
    assert (
        await executor.execute(ResearchStep(tool="no_such_tool", purpose="x"), "raw")
        == "temp_result_v9"
    )
    assert await executor.execute(ResearchStep(tool=None, needs_llm_codegen=True), "raw")
    assert calls == [("no_such_tool", "raw"), (None, "raw")]


async def test_codegen_fixes_code_after_sandbox_error(db: DuckDBManager) -> None:
    class FakeSandbox(WASMSandbox):
        def __init__(self) -> None:
            super().__init__()
            self.codes: list[str] = []

        def run(self, code: str, workdir: Path) -> dict:
            self.codes.append(code)
            assert (workdir / "input.csv").read_text().startswith("id,segment,amount")
            if code == "broken":
                raise SandboxError("NameError: name 'broken' is not defined")
            (workdir / "result.csv").write_text("segment,total\na,40\nb,60\n")
            return {"ok": True, "stdout": ""}

    llm = FakeLLM(fenced("broken"), fenced("fixed"))
    sandbox = FakeSandbox()
    step = ResearchStep(needs_llm_codegen=True, purpose="Сумма по сегментам", columns=["segment"])
    table = await run_generated_code(step, "raw", db, llm, sandbox)
    assert table == "temp_result_v1"
    assert db.query(f"SELECT * FROM {table} ORDER BY segment") == [
        {"segment": "a", "total": 40},
        {"segment": "b", "total": 60},
    ]
    assert sandbox.codes == ["broken", "fixed"]
    assert "Сумма по сегментам" in llm.prompts[0]
    assert '"segment": "VARCHAR"' in llm.prompts[0]
    assert "NameError" in llm.prompts[1]
    assert not any((Path(settings.storage_dir) / "sandbox").iterdir())


async def test_codegen_gives_up_after_attempts(db: DuckDBManager) -> None:
    class FailingSandbox(WASMSandbox):
        def run(self, code: str, workdir: Path) -> dict:
            raise SandboxError("boom")

    llm = FakeLLM("a", "b")
    with pytest.raises(SandboxError, match="boom"):
        await run_generated_code(
            ResearchStep(needs_llm_codegen=True), "raw", db, llm, FailingSandbox()
        )


def test_missing_runtime_is_reported(tmp_path: Path) -> None:
    sandbox = WASMSandbox(runtime_dir=tmp_path)
    assert not sandbox.available()
    with pytest.raises(SandboxError, match="not installed"):
        sandbox.run("result = df", tmp_path)


def test_memory_limit_never_exceeds_free_ram(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.sandbox.wasm_runtime.available_mb", lambda: 600)
    assert WASMSandbox(mem_mb=4096).limit_mb() == 600
    assert WASMSandbox(mem_mb=100).limit_mb() == 100


def test_command_grants_only_runtime_and_workdir(tmp_path: Path) -> None:
    command = WASMSandbox().command(tmp_path)
    grants = [arg for arg in command if arg.startswith("--allow")]
    assert f"--allow-fs-write={tmp_path}" in grants
    assert [arg for arg in grants if arg.startswith("--allow-fs-write")] == [
        f"--allow-fs-write={tmp_path}"
    ]
    assert not any("child-process" in arg or "worker" in arg or "addons" in arg for arg in grants)


@needs_runtime
async def test_agent_runs_generated_code_in_pyodide(db: DuckDBManager) -> None:
    code = (
        "import numpy as np\n"
        "from scipy import stats\n"
        "from sklearn.linear_model import LinearRegression\n"
        "model = LinearRegression().fit(df[['id']], df['amount'])\n"
        "result = df.groupby('segment', as_index=False)['amount'].sum()\n"
        "result['slope'] = round(float(model.coef_[0]), 6)\n"
        "result['zmax'] = round(float(np.max(stats.zscore(df['amount']))), 6)\n"
        "print('done')\n"
    )
    executor = StepExecutor(db, llm=FakeLLM(fenced(code)))
    step = ResearchStep(tool=None, needs_llm_codegen=True, purpose="Сумма по сегментам")
    table = await executor.execute(step, "raw")
    assert table == "temp_result_v1"
    rows = db.query(f"SELECT * FROM {table} ORDER BY segment")
    assert [(row["segment"], row["amount"]) for row in rows] == [("a", 40.0), ("b", 60.0)]
    assert rows[0]["slope"] == pytest.approx(10.0)
    assert rows[0]["zmax"] == pytest.approx(1.341641)


@needs_runtime
def test_sandbox_blocks_escape_attempts(tmp_path: Path) -> None:
    host_file = tmp_path / "secret.txt"
    host_file.write_text("secret")
    outside = tmp_path / "pwned.txt"
    sandbox = WASMSandbox()
    with sandbox.workspace() as workdir:
        (workdir / "input.csv").write_text("x\n1\n")
        header = f"HOST_FILE = {str(host_file)!r}\nOUTSIDE_FILE = {str(outside)!r}\n"
        sandbox.run(header + ESCAPES, workdir)
        rows = (workdir / "result.csv").read_text().splitlines()[1:]
        assert not (workdir / "link").exists()
    report = {row.split(",", 2)[0]: row for row in rows}
    assert len(report) == 14
    assert [row for row in report.values() if row.split(",", 2)[1] != "False"] == []
    assert not outside.exists()
    assert host_file.read_text() == "secret"


@needs_runtime
def test_sandbox_timeout_kills_process() -> None:
    sandbox = WASMSandbox(timeout_s=15)
    with sandbox.workspace() as workdir:
        (workdir / "input.csv").write_text("x\n1\n")
        with pytest.raises(SandboxError, match="timed out"):
            sandbox.run("while True:\n    pass\n", workdir)


@needs_runtime
def test_sandbox_memory_limit_kills_process() -> None:
    sandbox = WASMSandbox(mem_mb=700)
    with sandbox.workspace() as workdir:
        (workdir / "input.csv").write_text("x\n1\n")
        sandbox.run("result = df", workdir)
        code = "chunks = []\nwhile True:\n    chunks.append(bytearray(50 * 2**20))\n"
        with pytest.raises(SandboxError, match="memory limit"):
            sandbox.run(code, workdir)


@needs_runtime
def test_sandbox_reports_python_errors() -> None:
    sandbox = WASMSandbox()
    with sandbox.workspace() as workdir:
        (workdir / "input.csv").write_text("x\n1\n")
        with pytest.raises(SandboxError, match="ZeroDivisionError"):
            sandbox.run("result = 1 / 0", workdir)
        with pytest.raises(SandboxError, match="result"):
            sandbox.run("value = 1", workdir)
