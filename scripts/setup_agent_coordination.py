#!/usr/bin/env python3
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent


def write_if_needed(path: Path, content: str, overwrite: bool) -> bool:
    if path.exists() and not overwrite:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def ensure_gitignore(repo: Path, entry: str) -> bool:
    gitignore = repo / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    lines = [line.strip() for line in existing.splitlines()]
    if entry in lines:
        return False
    suffix = "" if existing.endswith("\n") or not existing else "\n"
    gitignore.write_text(f"{existing}{suffix}\n{entry}\n", encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a local multi-Codex coordination directory.")
    parser.add_argument("--repo", default=".", help="Repository root to initialize.")
    parser.add_argument("--no-gitignore", action="store_true", help="Do not add .agent-coordination/ to .gitignore.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing coordination files.")
    args = parser.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    coord = repo / ".agent-coordination"

    for subdir in ("reports", "templates"):
        (coord / subdir).mkdir(parents=True, exist_ok=True)
    for subdir in ("artifacts", "logs"):
        (coord / subdir).mkdir(parents=True, exist_ok=True)

    created_at = datetime.now(timezone.utc).isoformat()

    changes = dedent("""\
    # Coordination Changes Ledger

    Main Codex publishes structured changes with `coord.py change create`.

    """)

    reviews = dedent("""\
    # Coordination Reviews

    Reviewer Codex conversations publish structured reports with `coord.py report review`.

    """)

    tests = dedent("""\
    # Coordination Tests

    Tester Codex conversations publish structured reports with `coord.py report test`.

    """)

    state = {
        "schema": 2,
        "created_at": created_at,
        "mode": "structured-events",
        "repo": str(repo),
        "events": "events.jsonl",
        "index": "coord.db",
        "main": {"role": "main-codex"},
        "watchers": {
            "reviewer": {"ledger": "reviews.md"},
            "tester": {"ledger": "tests.md"},
        },
    }

    main_prompt = dedent(f"""\
    # Main Codex Prompt

    You are Main Codex for this repository:
    `{repo}`

    Rules:
    1. Own implementation, git state, verification, commits/pushes, and final user communication.
    2. Before a new increment, run `coord.py blockers`, `coord.py open`, and `coord.py status`; fix valid blockers first.
    3. Keep increments small. After each meaningful implementation step, run targeted verification and publish a structured change with `coord.py change create --capture-diff` when the repo is git-backed.
    4. Treat missing secondary reports as "not reviewed yet", not as approval, but do not idle on verified low/medium-risk increments.
    5. Resolve valid blocking findings from review/test reports before unrelated work or final handoff.
    6. Do not let secondary agents edit source files unless a task explicitly grants a narrow write scope.
    7. Before final response, run `coord.py doctor`, `coord.py blockers`, and `git status --short`.

    Change entry template:

    ```bash
    python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change create --capture-diff \\
      --file src/example.py \\
      --summary "Short implementation summary" \\
      --verify "pytest tests/test_example.py" \\
      --risk medium
    ```
    """)

    reviewer_prompt = dedent(f"""\
    # Reviewer Codex Prompt

    You are Reviewer Codex for this repository:
    `{repo}`

    Rules:
    1. Do not edit source files, commit, push, reset, delete files, install dependencies, or run broad formatters.
    2. Watch and claim structured changes with `coord.py watch --claim`.
    3. When a new change appears, inspect the diff snapshot when present, touched files, and relevant contracts only.
    4. Report concrete bugs, regressions, missing tests, compatibility risks, and unsafe assumptions. Avoid style-only findings unless they hide a real defect.
    5. Publish a review with `coord.py report review`, including `--files-read` for inspected files and `--finding` for material findings.
    6. Use `pass` only when no material issue remains, `concerns` for non-blocking risks, and `blocking` for issues Main must fix before unrelated work or final handoff.
    7. After writing the review, run `coord.py mark-processed`.
    8. If no update appears, stay silent and keep waiting.

    Suggested wait command:

    ```bash
    python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role reviewer --actor reviewer-a --claim --interval 60
    ```
    """)

    tester_prompt = dedent(f"""\
    # Tester Codex Prompt

    You are Tester Codex for this repository:
    `{repo}`

    Rules:
    1. Do not edit source files unless explicitly instructed.
    2. Do not commit, push, reset, delete files, install dependencies, run destructive commands, or fake unavailable hardware/vendor coverage.
    3. Watch and claim structured changes with `coord.py watch --claim`.
    4. When a new change appears, run the listed verification commands when safe, then add focused checks based on touched files.
    5. Publish results with `coord.py report test`; include each command with `--command`, use `--untested` for anything not actually covered, and add `--finding` for material failures.
    6. Use `pass` only for commands that actually passed, `fail` for real failures, and `blocked` for missing dependencies, unavailable services, or unsafe commands.
    7. After writing the test report, run `coord.py mark-processed`.
    8. If no update appears, stay silent and keep waiting.

    Suggested wait command:

    ```bash
    python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role tester --actor tester-a --claim --interval 60
    ```
    """)

    changed = []
    files = (
        (coord / "changes.md", changes),
        (coord / "reviews.md", reviews),
        (coord / "tests.md", tests),
        (coord / "events.jsonl", ""),
        (coord / "state.json", json.dumps(state, indent=2) + "\n"),
        (coord / "templates" / "main-codex-prompt.md", main_prompt),
        (coord / "templates" / "reviewer-codex-prompt.md", reviewer_prompt),
        (coord / "templates" / "tester-codex-prompt.md", tester_prompt),
    )

    for path, content in files:
        if write_if_needed(path, content, args.overwrite):
            changed.append(path)

    if not args.no_gitignore and ensure_gitignore(repo, ".agent-coordination/"):
        changed.append(repo / ".gitignore")

    print(f"Initialized multi-Codex coordination directory: {coord}")
    for path in changed:
        print(f"wrote: {path.relative_to(repo)}")
    if not changed:
        print("No files changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
