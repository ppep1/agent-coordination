#!/usr/bin/env python3
import json
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COORD = ROOT / "scripts" / "coord.py"
SETUP = ROOT / "scripts" / "setup_agent_coordination.py"


def run_cmd(*args: str, repo: Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["python3", str(COORD), "--repo", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"command failed: {' '.join(args)}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def setup_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Coord Test"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "coord-test@example.com"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "example.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (repo / "tests" / "test_example.py").write_text(
        "from src.example import value\n\ndef test_value():\n    assert value() == 1\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "."], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    subprocess.run(["git", "commit", "-m", "Initial"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    subprocess.run(
        ["python3", str(SETUP), "--repo", str(repo)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    run_cmd("init", repo=repo)


def assert_contains(text: str, needle: str) -> None:
    if needle not in text:
        raise AssertionError(f"expected {needle!r} in:\n{text}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="coord-test-") as tmp:
        repo = Path(tmp)
        setup_repo(repo)

        doctor = run_cmd("doctor", repo=repo)
        assert_contains(doctor.stdout, "DOCTOR_OK")
        strict_doctor = run_cmd("doctor", "--strict", repo=repo)
        assert_contains(strict_doctor.stdout, "DOCTOR_OK")
        version = subprocess.run(
            ["python3", str(COORD), "--version"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True
        )
        assert_contains(version.stdout, "0.3.0")

        unknown = run_cmd("report", "review", "--change", "chg_9999", "--decision", "pass", repo=repo, check=False)
        assert unknown.returncode == 2
        assert_contains(unknown.stdout, "UNKNOWN_CHANGE")

        (repo / "src" / "example.py").write_text("def value():\n    return 2\n", encoding="utf-8")

        change = run_cmd(
            "change",
            "create",
            "--capture-diff",
            "--file",
            "src/example.py",
            "--summary",
            "Add example",
            "--verify",
            "pytest tests/test_example.py",
            "--risk",
            "medium",
            repo=repo,
        ).stdout.strip()
        assert change == "chg_0001"

        claim = run_cmd("claim", "--role", "reviewer", "--actor", "reviewer-a", "--ttl", "600", repo=repo)
        claim_payload = json.loads(claim.stdout)
        assert claim_payload["change_id"] == change
        assert claim_payload["claimed_by"] == "reviewer-a"
        assert claim_payload["verification"] == ["pytest tests/test_example.py"]
        assert claim_payload["diff_path"] == "artifacts/chg_0001.diff"
        assert_contains((repo / ".agent-coordination" / "artifacts" / "chg_0001.diff").read_text(encoding="utf-8"), "return 2")

        duplicate_claim = run_cmd("claim", "--role", "reviewer", "--actor", "reviewer-b", repo=repo, check=False)
        assert duplicate_claim.returncode == 2
        assert_contains(duplicate_claim.stdout, "NO_CLAIMABLE_TASK")

        blocked_report = run_cmd(
            "report",
            "review",
            "--actor",
            "reviewer-b",
            "--change",
            change,
            "--decision",
            "pass",
            repo=repo,
            check=False,
        )
        assert blocked_report.returncode == 2
        assert_contains(blocked_report.stdout, "TASK_CLAIMED_BY reviewer-a")

        bad_blocking = run_cmd(
            "report",
            "review",
            "--actor",
            "reviewer-a",
            "--change",
            change,
            "--decision",
            "blocking",
            repo=repo,
            check=False,
        )
        assert bad_blocking.returncode == 2
        assert_contains(bad_blocking.stdout, "INVALID_REPORT")

        report_id = run_cmd(
            "report",
            "review",
            "--actor",
            "reviewer-a",
            "--change",
            change,
            "--decision",
            "blocking",
            "--files-read",
            "src/example.py",
            "--finding",
            "high:src/example.py:42:Missing empty input case",
            repo=repo,
        ).stdout.strip()
        assert report_id.startswith("rpt_")

        run_cmd("mark-processed", "--role", "reviewer", "--actor", "reviewer-a", "--change", change, repo=repo)

        tester_claim = run_cmd("watch", "--role", "tester", "--actor", "tester-a", "--claim", "--once", repo=repo)
        tester_payload = json.loads(tester_claim.stdout)
        assert tester_payload["change_id"] == change

        tester_report = run_cmd(
            "report",
            "test",
            "--actor",
            "tester-a",
            "--change",
            change,
            "--decision",
            "pass",
            "--command",
            "pytest tests/test_example.py",
            repo=repo,
        ).stdout.strip()
        assert tester_report.startswith("rpt_")
        run_cmd("mark-processed", "--role", "tester", "--actor", "tester-a", "--change", change, repo=repo)

        blockers = run_cmd("blockers", repo=repo, check=False)
        assert blockers.returncode == 1
        assert_contains(blockers.stdout, report_id)
        finding_id = next(line.split()[0] for line in blockers.stdout.splitlines() if line.startswith("fnd_"))

        show = run_cmd("show", change, repo=repo)
        show_payload = json.loads(show.stdout)
        assert show_payload["change"]["id"] == change
        assert show_payload["diff_path"] == "artifacts/chg_0001.diff"
        assert show_payload["tasks"][0]["status"] == "completed"

        timeline = run_cmd("timeline", change, repo=repo)
        assert_contains(timeline.stdout, "change.created")
        assert_contains(timeline.stdout, "task.claimed")
        assert_contains(timeline.stdout, "review.completed")

        open_changes = run_cmd("open", repo=repo)
        assert_contains(open_changes.stdout, change)

        run_cmd("finding", "resolve", finding_id, "--reason", "Fixed in chg_0002", repo=repo)
        run_cmd("report", "resolve", report_id, "--reason", "Fixed and verified in chg_0002", repo=repo)

        no_blockers = run_cmd("blockers", repo=repo)
        assert_contains(no_blockers.stdout, "No open blockers.")

        run_cmd("rebuild", repo=repo)
        rebuilt = run_cmd("blockers", repo=repo)
        assert_contains(rebuilt.stdout, "No open blockers.")

        main_prompt = run_cmd("prompt", "main", repo=repo)
        assert_contains(main_prompt.stdout, "preflight review")
        assert_contains(main_prompt.stdout, "roadmap phase boundaries")
        assert_contains(main_prompt.stdout, "will not be reconfirmed phase by phase")
        assert_contains(main_prompt.stdout, "status/open/blockers")
        assert_contains(main_prompt.stdout, "do not treat a status question as a pause")
        reviewer_prompt = run_cmd("prompt", "reviewer", "--actor", "reviewer-z", repo=repo)
        assert_contains(reviewer_prompt.stdout, "reviewer-z")
        assert_contains(reviewer_prompt.stdout, "watch --role reviewer")
        assert_contains(reviewer_prompt.stdout, "Do not ask the user to relay results")
        assert_contains(reviewer_prompt.stdout, "SESSION_FINISHED")
        assert_contains(reviewer_prompt.stdout, 'Do not send periodic "still waiting" chat messages')
        tester_prompt = run_cmd("prompt", "tester", "--actor", "tester-z", repo=repo)
        assert_contains(tester_prompt.stdout, "tester-z")
        assert_contains(tester_prompt.stdout, "Do not ask the user to relay results")
        assert_contains(tester_prompt.stdout, "SESSION_FINISHED")
        assert_contains(tester_prompt.stdout, 'Do not send periodic "still waiting" chat messages')
        observer_prompt = run_cmd("prompt", "observer", "--actor", "observer-z", repo=repo)
        assert_contains(observer_prompt.stdout, "Observer Codex")
        assert_contains(observer_prompt.stdout, "lightweight model")
        assert_contains(observer_prompt.stdout, "Do not edit source files, claim tasks")

        quiet_watch = run_cmd(
            "watch",
            "--role",
            "reviewer",
            "--actor",
            "reviewer-a",
            "--claim",
            "--interval",
            "0.01",
            "--timeout",
            "0.01",
            repo=repo,
            check=False,
        )
        assert quiet_watch.returncode == 2
        assert quiet_watch.stdout == "WATCH_TIMEOUT\n"

        html_report = run_cmd("export-html", repo=repo).stdout.strip()
        html_path = Path(html_report)
        assert html_path.exists()
        assert_contains(html_path.read_text(encoding="utf-8"), "Agent Coordination Status")

        second_change = run_cmd(
            "change",
            "create",
            "--file",
            "src/second.py",
            "--summary",
            "Add second change",
            "--verify",
            "python -m compileall src",
            "--risk",
            "low",
            repo=repo,
        ).stdout.strip()
        assert second_change == "chg_0002"

        expired_claim = run_cmd("claim", "--role", "tester", "--actor", "tester-b", "--change", second_change, "--ttl", "-1", repo=repo)
        assert json.loads(expired_claim.stdout)["claimed_by"] == "tester-b"
        tester_c_claim = run_cmd("claim", "--role", "tester", "--actor", "tester-c", "--change", second_change, repo=repo)
        assert json.loads(tester_c_claim.stdout)["claimed_by"] == "tester-c"
        blocked_claim = run_cmd("claim", "--role", "tester", "--actor", "tester-b", "--change", second_change, repo=repo, check=False)
        assert blocked_claim.returncode == 2
        run_cmd("release", "--role", "tester", "--actor", "tester-c", "--change", second_change, repo=repo)
        tester_b_reclaim = run_cmd("claim", "--role", "tester", "--actor", "tester-b", "--change", second_change, repo=repo)
        assert json.loads(tester_b_reclaim.stdout)["claimed_by"] == "tester-b"

        pending_final = run_cmd("wait-final", "--once", repo=repo, check=False)
        assert pending_final.returncode == 2
        assert_contains(pending_final.stdout, "FINAL_PENDING")
        assert_contains(pending_final.stdout, f"TASK {second_change} reviewer:pending")

        run_cmd("claim", "--role", "reviewer", "--actor", "reviewer-a", "--change", second_change, repo=repo)
        run_cmd("report", "review", "--actor", "reviewer-a", "--change", second_change, "--decision", "pass", repo=repo)
        run_cmd("mark-processed", "--role", "reviewer", "--actor", "reviewer-a", "--change", second_change, repo=repo)
        run_cmd(
            "report",
            "test",
            "--actor",
            "tester-b",
            "--change",
            second_change,
            "--decision",
            "pass",
            "--command",
            "python -m compileall src",
            repo=repo,
        )
        run_cmd("mark-processed", "--role", "tester", "--actor", "tester-b", "--change", second_change, repo=repo)
        ready_final = run_cmd("wait-final", "--once", repo=repo)
        assert_contains(ready_final.stdout, "FINAL_READY")
        finish = run_cmd("finish", "--actor", "main", repo=repo)
        assert_contains(finish.stdout, "SESSION_FINISHED")
        finished_watch = run_cmd("watch", "--role", "reviewer", "--actor", "reviewer-a", "--claim", "--once", repo=repo)
        assert_contains(finished_watch.stdout, "SESSION_FINISHED")

        final_doctor = run_cmd("doctor", repo=repo)
        assert_contains(final_doctor.stdout, "DOCTOR_OK")
        final_strict_doctor = run_cmd("doctor", "--strict", repo=repo)
        assert_contains(final_strict_doctor.stdout, "DOCTOR_OK")

    with tempfile.TemporaryDirectory(prefix="coord-strict-test-") as tmp:
        repo = Path(tmp)
        setup_repo(repo)
        run_cmd(
            "change",
            "create",
            "--file",
            "src/example.py",
            "--summary",
            "Create baseline event",
            "--verify",
            "pytest tests/test_example.py",
            repo=repo,
        )
        bad_event = {
            "id": "bad_event_id",
            "schema": 1,
            "type": "change.verified",
            "actor": "main",
            "ts": "2026-05-21T00:00:00+00:00",
            "change_id": "chg_9999",
        }
        events_path = repo / ".agent-coordination" / "events.jsonl"
        with events_path.open("a", encoding="utf-8") as events_file:
            events_file.write(json.dumps(bad_event, sort_keys=True, separators=(",", ":")) + "\n")
        run_cmd("rebuild", repo=repo)
        strict_bad = run_cmd("doctor", "--strict", repo=repo, check=False)
        assert strict_bad.returncode == 1
        assert_contains(strict_bad.stdout, "invalid event id")
        assert_contains(strict_bad.stdout, "unknown change_id chg_9999")

    print("test_coord.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
