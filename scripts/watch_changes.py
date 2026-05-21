#!/usr/bin/env python3
import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def latest_change_heading(path: Path) -> str:
    if not path.exists():
        return ""
    headings = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("## Change ")]
    return headings[-1] if headings else ""


def load_status(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_status(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def mark_processed(repo: Path, role: str) -> int:
    coord = repo / ".agent-coordination"
    changes = coord / "changes.md"
    status_path = coord / "status" / f"{role}.json"

    status = load_status(status_path)
    current_hash = sha256_file(changes)
    heading = latest_change_heading(changes)
    processed_hash = status.get("pending_changes_sha256") or current_hash

    if not processed_hash:
        print("NO_CHANGE_FILE")
        return 2

    status.update({
        "role": role,
        "last_processed_changes_sha256": processed_hash,
        "last_processed_heading": status.get("pending_heading") or heading,
        "pending_changes_sha256": "",
        "pending_heading": "",
        "processed_at": datetime.now(timezone.utc).isoformat(),
    })
    save_status(status_path, status)

    print("MARKED_PROCESSED")
    print(f"role={role}")
    print(f"heading={status.get('last_processed_heading', '')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Wait until .agent-coordination/changes.md changes.")
    parser.add_argument("--repo", default=".", help="Repository root.")
    parser.add_argument("--role", required=True, help="Watcher role name, e.g. reviewer or tester.")
    parser.add_argument("--interval", type=float, default=60.0, help="Polling interval in seconds.")
    parser.add_argument("--once", action="store_true", help="Check once and exit with code 2 if unchanged.")
    parser.add_argument("--mark-processed", action="store_true", help="Mark the pending/current change as processed after writing a report.")
    args = parser.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    coord = repo / ".agent-coordination"
    changes = coord / "changes.md"
    status_path = coord / "status" / f"{args.role}.json"

    if args.mark_processed:
        return mark_processed(repo, args.role)

    status = load_status(status_path)
    last_processed_hash = status.get("last_processed_changes_sha256", "")

    while True:
        current_hash = sha256_file(changes)
        heading = latest_change_heading(changes)

        if current_hash and not heading:
            # Baseline file exists but Main Codex has not published a real change yet.
            if current_hash != last_processed_hash:
                status.update({
                    "role": args.role,
                    "last_processed_changes_sha256": current_hash,
                    "last_processed_heading": "",
                    "baseline_recorded_at": datetime.now(timezone.utc).isoformat(),
                })
                save_status(status_path, status)
                last_processed_hash = current_hash

        elif current_hash and current_hash != last_processed_hash:
            status.update({
                "role": args.role,
                "pending_changes_sha256": current_hash,
                "pending_heading": heading,
                "detected_at": datetime.now(timezone.utc).isoformat(),
            })
            save_status(status_path, status)
            print("CHANGE_DETECTED")
            print(f"role={args.role}")
            print(f"changes={changes}")
            print(f"heading={heading}")
            print("Next steps: read the latest change, perform your assigned review/test, append the result, then run:")
            print(f"python3 ~/.codex/skills/agent-coordination/scripts/watch_changes.py --repo {repo} --role {args.role} --mark-processed")
            return 0

        if args.once:
            print("NO_CHANGE")
            return 2

        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
