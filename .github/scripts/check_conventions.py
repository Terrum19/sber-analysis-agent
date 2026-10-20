import re
import subprocess
import sys

CYRILLIC = re.compile(r"[А-Яа-яЁё]")
TRAILER = re.compile(r"^[A-Za-z-]+:\s")
ATTRIBUTION = re.compile(
    r"co-authored-by:.*(claude|anthropic)"
    r"|generated with \[?claude"
    r"|noreply@anthropic\.com",
    re.IGNORECASE,
)
FORBIDDEN_PATHS = re.compile(r"(^|/)\.claude/|(^|/)CLAUDE\.md$")
SCISSORS = "# ------------------------ >8 ------------------------"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def text_problems(message: str) -> list[str]:
    subject, _, body = message.lstrip("\n").partition("\n")
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


def message_problems(commit: str) -> list[str]:
    return text_problems(git("log", "-1", "--format=%B", commit))


def editor_message(text: str) -> str:
    kept = []
    for line in text.splitlines():
        if line == SCISSORS:
            break
        if not line.startswith("#"):
            kept.append(line)
    return "\n".join(kept)


def check_message_file(path: str) -> int:
    with open(path, encoding="utf-8") as message_file:
        problems = text_problems(editor_message(message_file.read()))
    if not problems:
        return 0
    print("✗ сообщение коммита не прошло проверку конвенций:", file=sys.stderr)
    for problem in problems:
        print(f"    {problem}", file=sys.stderr)
    return 1


def changed_files(commit: str) -> list[tuple[str, str]]:
    output = git("diff-tree", "--root", "--no-commit-id", "-r", "--name-status", commit)
    entries = []
    for line in output.splitlines():
        status, *paths = line.split("\t")
        entries.append((status[0], paths[-1]))
    return entries


def check(commit: str) -> list[str]:
    problems = message_problems(commit)
    for status, path in changed_files(commit):
        if status == "D":
            continue
        if FORBIDDEN_PATHS.search(path):
            problems.append(f"служебный файл Claude в коммите: {path}")
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
    if sys.argv[1:2] == ["--message-file"] and len(sys.argv) == 3:
        sys.exit(check_message_file(sys.argv[2]))
    sys.exit(main(sys.argv[1:]))
