import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK = REPO_ROOT / ".githooks" / "pre-commit"
PYPROJECT = REPO_ROOT / "backend" / "pyproject.toml"
RUFF_SEARCH_PATH = os.pathsep.join([str(Path(sys.executable).parent), os.environ["PATH"]])
RUFF = shutil.which("ruff", path=RUFF_SEARCH_PATH)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "dev", str(work)], check=True, capture_output=True)
    git(work, "config", "user.name", "Tester")
    git(work, "config", "user.email", "tester@example.com")
    git(work, "config", "commit.gpgsign", "false")
    hooks = work / ".githooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text(HOOK.read_text(encoding="utf-8"), encoding="utf-8")
    (hooks / "pre-commit").chmod(0o755)
    git(work, "config", "core.hooksPath", ".githooks")
    (work / "backend").mkdir()
    (work / "backend" / "pyproject.toml").write_text(
        PYPROJECT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return work


def fake_tool(directory: Path, name: str, body: str) -> None:
    directory.mkdir(exist_ok=True)
    tool = directory / name
    tool.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    tool.chmod(0o755)


def try_commit(repo: Path, files: dict[str, str], path: str) -> subprocess.CompletedProcess:
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        git(repo, "add", name)
    return subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "Add a file"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": path},
    )


def head(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "-q", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.mark.skipif(RUFF is None, reason="ruff is not installed")
def test_unformatted_backend_file_is_rejected(repo: Path) -> None:
    path = os.pathsep.join([str(Path(RUFF).parent), os.environ["PATH"]])
    result = try_commit(repo, {"backend/app/module.py": "value=1\n"}, path)
    assert result.returncode != 0
    assert "ruff нашёл нарушения" in result.stderr
    assert head(repo) == ""


@pytest.mark.skipif(RUFF is None, reason="ruff is not installed")
def test_clean_backend_file_is_committed(repo: Path) -> None:
    path = os.pathsep.join([str(Path(RUFF).parent), os.environ["PATH"]])
    result = try_commit(repo, {"backend/app/module.py": "value = 1\n"}, path)
    assert result.returncode == 0, result.stderr
    assert head(repo) != ""


def test_commit_without_backend_files_does_not_run_ruff(repo: Path, tmp_path: Path) -> None:
    marker = tmp_path / "ruff-called"
    fake_bin = tmp_path / "bin"
    fake_tool(fake_bin, "ruff", f'touch "{marker}"\nexit 1')
    path = os.pathsep.join([str(fake_bin), os.environ["PATH"]])
    result = try_commit(repo, {"README.md": "readme\n", "frontend/app.py": "value=1\n"}, path)
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_commit_without_ruff_passes_with_warning(repo: Path, tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for tool in ("git", "xargs"):
        (fake_bin / tool).symlink_to(shutil.which(tool))
    fake_tool(fake_bin, "python3", "exit 1")
    result = try_commit(repo, {"backend/app/module.py": "value=1\n"}, str(fake_bin))
    assert result.returncode == 0, result.stderr
    assert "ruff не найден" in result.stderr
    assert head(repo) != ""
