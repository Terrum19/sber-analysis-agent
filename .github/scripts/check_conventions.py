import io
import re
import subprocess
import sys
import tokenize

CYRILLIC = re.compile(r"[А-Яа-яЁё]")
TRAILER = re.compile(r"^[A-Za-z-]+:\s")
ATTRIBUTION = re.compile(
    r"co-authored-by:.*(claude|anthropic)|generated with \[?claude|noreply@anthropic\.com",
    re.IGNORECASE,
)
FORBIDDEN_PATHS = re.compile(r"(^|/)\.claude/|(^|/)CLAUDE\.md$")
ALLOWED_COMMENT = re.compile(r"SPDX-License-Identifier|Copyright|^#!")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def message_problems(commit: str) -> list[str]:
    subject, _, body = git("log", "-1", "--format=%B", commit).partition("\n")
    problems = []
    if CYRILLIC.search(subject):
        problems.append(f"заголовок коммита должен быть на английском: «{subject}»")
    prose = [
        line for line in body.splitlines() if line.strip() and not TRAILER.match(line)
    ]
    if prose and not any(CYRILLIC.search(line) for line in prose):
        problems.append("описание коммита должно быть на русском")
    for line in (subject + "\n" + body).splitlines():
        if ATTRIBUTION.search(line):
            problems.append(f"упоминание Claude в сообщении коммита: «{line.strip()}»")
    return problems


def changed_files(commit: str) -> list[tuple[str, str]]:
    output = git("diff-tree", "--root", "--no-commit-id", "-r", "--name-status", commit)
    entries = []
    for line in output.splitlines():
        status, *paths = line.split("\t")
        entries.append((status[0], paths[-1]))
    return entries


def added_lines(commit: str, path: str) -> set[int]:
    diff = git("diff-tree", "--root", "-p", "-U0", "--no-commit-id", commit, "--", path)
    numbers: set[int] = set()
    for line in diff.splitlines():
        match = HUNK.match(line)
        if match:
            start = int(match.group(1))
            count = int(match.group(2)) if match.group(2) is not None else 1
            numbers.update(range(start, start + count))
    return numbers


def comment_problems(commit: str, path: str) -> list[str]:
    source = git("show", f"{commit}:{path}")
    fresh = added_lines(commit, path)
    problems = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError):
        return [f"{path}: не удалось разобрать файл"]
    for token in tokens:
        row = token.start[0]
        if token.type != tokenize.COMMENT or row not in fresh:
            continue
        if ALLOWED_COMMENT.search(token.string):
            continue
        problems.append(f"{path}:{row}: комментарий в .py запрещён: {token.string.strip()}")
    return problems


def check(commit: str) -> list[str]:
    problems = message_problems(commit)
    for status, path in changed_files(commit):
        if status == "D":
            continue
        if FORBIDDEN_PATHS.search(path):
            problems.append(f"служебный файл Claude в коммите: {path}")
        if path.endswith(".py"):
            problems.extend(comment_problems(commit, path))
    return problems


def main(commits: list[str]) -> int:
    failed = 0
    for commit in commits:
        if len(git("rev-list", "--parents", "-n", "1", commit).split()) > 2:
            continue
        problems = check(commit)
        title = git("log", "-1", "--format=%h %s", commit).strip()
        if problems:
            failed += 1
            print(f"✗ {title}")
            for problem in problems:
                print(f"    {problem}")
        else:
            print(f"✓ {title}")
    print(f"\nПроверено коммитов: {len(commits)}, с нарушениями: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
