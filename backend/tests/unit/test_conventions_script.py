import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "check_conventions.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "conventions.yml"
HOOK = REPO_ROOT / ".githooks" / "commit-msg"
TRAILER = "Co-Authored-By: Claude Opus <noreply@anthropic.com>"


def load_script():
    spec = importlib.util.spec_from_file_location("check_conventions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect_commits_script() -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    step = next(index for index, line in enumerate(lines) if "name: Collect commits" in line)
    start = next(index for index in range(step, len(lines)) if lines[index].strip() == "run: |")
    indent = len(lines[start]) - len(lines[start].lstrip()) + 2
    body = []
    for line in lines[start + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo: Path, message: str, files: dict[str, str] | None = None) -> str:
    for name, content in (files or {}).items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        git(repo, "add", "-f", name)
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def work(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    repo = tmp_path / "work"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "dev", str(origin)], check=True, capture_output=True
    )
    subprocess.run(["git", "init", "-q", "-b", "dev", str(repo)], check=True, capture_output=True)
    git(repo, "config", "user.name", "Tester")
    git(repo, "config", "user.email", "tester@example.com")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "remote", "add", "origin", str(origin))
    commit(repo, "Initial commit", {"README.md": "readme\n"})
    git(repo, "push", "-q", "origin", "dev")
    return repo


def collect(work: Path, branch: str, before: str, after: str) -> list[str]:
    runner = work.parent / "runner"
    subprocess.run(
        ["git", "clone", "-q", str(work.parent / "origin.git"), str(runner)],
        check=True,
        capture_output=True,
    )
    temp = work.parent / "runner_temp"
    temp.mkdir()
    environment = {
        **os.environ,
        "EVENT": "push",
        "BASE": "",
        "HEAD_SHA": "",
        "BEFORE": before,
        "AFTER": after,
        "BRANCH": branch,
        "RUNNER_TEMP": str(temp),
    }
    subprocess.run(
        ["bash", "-e", "-c", collect_commits_script()],
        cwd=runner,
        env=environment,
        check=True,
        capture_output=True,
    )
    return (temp / "commits").read_text(encoding="utf-8").split()


def test_english_subject_and_russian_body_are_accepted(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(work)
    sha = commit(work, "Clean up the planner branch\n\nУбраны временные файлы.")
    assert load_script().check(sha) == []


def test_russian_subject_is_rejected(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(work)
    sha = commit(work, "chore: прибрать ветку planner\n\nУбраны временные файлы.")
    assert load_script().check(sha) == [
        "заголовок коммита должен быть на английском: «chore: прибрать ветку planner»"
    ]


def test_english_body_is_rejected(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(work)
    sha = commit(work, "Clean up the planner branch\n\nRemoves temporary files.")
    assert load_script().check(sha) == ["описание коммита должно быть на русском"]


def test_claude_trailer_is_rejected(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(work)
    sha = commit(work, f"Add a feature\n\nОписание.\n\n{TRAILER}")
    problems = load_script().check(sha)
    assert len(problems) == 1
    assert "Claude" in problems[0]


@pytest.mark.parametrize("path", [".claude/settings.local.json", "CLAUDE.md"])
def test_claude_files_are_rejected(work: Path, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.chdir(work)
    sha = commit(work, "Add settings", {path: "{}\n"})
    assert load_script().check(sha) == [f"служебный файл Claude в коммите: {path}"]


def test_main_reports_failures(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(work)
    clean = commit(work, "Add a feature")
    violating = commit(work, f"Add another feature\n\n{TRAILER}")
    script = load_script()
    assert script.main([clean]) == 0
    assert script.main([clean, violating]) == 1


def test_push_skips_commits_already_in_dev(work: Path) -> None:
    git(work, "switch", "-q", "-c", "feature/x")
    commit(work, "Start the feature")
    git(work, "push", "-q", "origin", "feature/x")
    before = git(work, "rev-parse", "HEAD")
    git(work, "switch", "-q", "dev")
    in_dev = commit(work, f"Old commit in dev\n\n{TRAILER}")
    git(work, "push", "-q", "origin", "dev")
    git(work, "switch", "-q", "feature/x")
    git(work, "merge", "-q", "--no-edit", "dev")
    new = commit(work, "Continue the feature")
    git(work, "push", "-q", "origin", "feature/x")
    commits = collect(work, "feature/x", before, new)
    assert in_dev not in commits
    assert new in commits


def test_push_still_checks_new_violations(work: Path) -> None:
    git(work, "switch", "-q", "-c", "feature/x")
    before = commit(work, "Start the feature")
    git(work, "push", "-q", "origin", "feature/x")
    violating = commit(work, f"Continue the feature\n\n{TRAILER}")
    git(work, "push", "-q", "origin", "feature/x")
    assert collect(work, "feature/x", before, violating) == [violating]


def test_push_to_dev_checks_its_own_commits(work: Path) -> None:
    before = git(work, "rev-parse", "HEAD")
    new = commit(work, "Add a feature to dev")
    git(work, "push", "-q", "origin", "dev")
    assert collect(work, "dev", before, new) == [new]


def test_text_problems_reject_cyrillic_subject() -> None:
    assert load_script().text_problems("Добавил фичу") == [
        "заголовок коммита должен быть на английском: «Добавил фичу»"
    ]


def test_text_problems_reject_english_body() -> None:
    assert load_script().text_problems("Add a feature\n\nAdds the feature.") == [
        "описание коммита должно быть на русском"
    ]


def test_text_problems_reject_claude_trailer() -> None:
    problems = load_script().text_problems(f"Add a feature\n\nОписание.\n\n{TRAILER}")
    assert problems == [f"упоминание Claude в сообщении коммита: «{TRAILER}»"]


def test_text_problems_accept_conventional_message() -> None:
    assert load_script().text_problems("Add a feature\n\nОписание фичи.\n") == []


def test_editor_comments_and_diff_are_ignored() -> None:
    message = (
        "Add a feature\n\nОписание фичи.\n"
        "# Please enter the commit message for your changes.\n"
        "# ------------------------ >8 ------------------------\n"
        "diff --git a/README.md b/README.md\n+Some English text\n"
    )
    script = load_script()
    assert script.text_problems(script.editor_message(message)) == []


@pytest.mark.parametrize(
    ("message", "code"),
    [("Add a feature\n\nОписание фичи.\n", 0), ("Добавил фичу\n", 1)],
)
def test_message_file_mode(tmp_path: Path, message: str, code: int) -> None:
    message_file = tmp_path / "COMMIT_EDITMSG"
    message_file.write_text(message, encoding="utf-8")
    result = subprocess.run(
        ["python3", str(SCRIPT), "--message-file", str(message_file)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == code


@pytest.fixture
def hooked(work: Path) -> Path:
    scripts = work / ".github" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "check_conventions.py").write_text(
        SCRIPT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    hooks = work / ".githooks"
    hooks.mkdir()
    (hooks / "commit-msg").write_text(HOOK.read_text(encoding="utf-8"), encoding="utf-8")
    (hooks / "commit-msg").chmod(0o755)
    git(work, "config", "core.hooksPath", ".githooks")
    return work


@pytest.mark.parametrize("message", ["Добавил фичу", f"Add a feature\n\nОписание.\n\n{TRAILER}"])
def test_hook_rejects_commit(hooked: Path, message: str) -> None:
    before = git(hooked, "rev-parse", "HEAD")
    result = subprocess.run(
        ["git", "-C", str(hooked), "commit", "--allow-empty", "-m", message],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "проверку конвенций" in result.stderr
    assert git(hooked, "rev-parse", "HEAD") == before


def test_hook_accepts_conventional_commit(hooked: Path) -> None:
    before = git(hooked, "rev-parse", "HEAD")
    commit(hooked, "Add a feature\n\nОписание фичи.")
    assert git(hooked, "rev-parse", "HEAD") != before
