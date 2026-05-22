#!/usr/bin/env python3
import argparse
import fcntl
import html
import json
import re
import sqlite3
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional


VERSION = "0.3.0"
SCHEMA = 1
BLOCKING_DECISIONS = ("blocking", "fail", "blocked")
BLOCKING_SEVERITIES = ("high", "critical", "blocking")
CHANGE_RE = re.compile(r"^chg_\d{4,}$")
JOB_RE = re.compile(r"^job_\d{4,}$")
EVENT_RE = re.compile(r"^evt_[0-9a-f]{12}$")
REPORT_RE = re.compile(r"^rpt_[0-9a-f]{12}$")
FINDING_RE = re.compile(r"^fnd_[0-9a-f]{12}$")
TERMINAL_EVENT_TYPES = ("session.finished",)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_seconds_iso(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def coord_dir(repo: Path) -> Path:
    return repo.expanduser().resolve() / ".agent-coordination"


@contextmanager
def locked(coord: Path):
    coord.mkdir(parents=True, exist_ok=True)
    lock_path = coord / "coord.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def db_path(coord: Path) -> Path:
    return coord / "coord.db"


def connect(coord: Path) -> sqlite3.Connection:
    coord.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path(coord))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        create table if not exists meta (
          key text primary key,
          value text not null
        );
        create table if not exists events (
          id text primary key,
          seq integer unique,
          type text not null,
          actor text not null,
          ts text not null,
          payload text not null
        );
        create table if not exists changes (
          id text primary key,
          task_id text,
          status text not null,
          actor text not null,
          summary text not null,
          risk text,
          created_at text not null,
          verified_at text,
          committed_at text,
          pushed_at text
        );
        create table if not exists change_files (
          change_id text not null,
          path text not null,
          primary key (change_id, path)
        );
        create table if not exists reports (
          id text primary key,
          change_id text not null,
          role text not null,
          actor text not null,
          decision text not null,
          created_at text not null,
          status text not null default 'open'
        );
        create table if not exists findings (
          id text primary key,
          report_id text not null,
          change_id text not null,
          severity text not null,
          file text,
          line integer,
          message text not null,
          status text not null default 'open'
        );
        create table if not exists agent_offsets (
          actor text primary key,
          role text not null,
          last_seq integer not null,
          updated_at text not null
        );
        create table if not exists tasks (
          role text not null,
          change_id text not null,
          status text not null,
          claimed_by text,
          lease_expires_at text,
          completed_by text,
          updated_at text not null,
          primary key (role, change_id)
        );
        create table if not exists work_items (
          id text primary key,
          status text not null,
          title text not null,
          details text not null,
          acceptance text not null,
          risk text,
          actor text not null,
          claimed_by text,
          lease_expires_at text,
          completed_by text,
          change_id text,
          created_at text not null,
          updated_at text not null
        );
        """
    )
    change_columns = {row["name"] for row in conn.execute("pragma table_info(changes)").fetchall()}
    if "task_id" not in change_columns:
        conn.execute("alter table changes add column task_id text")
    report_columns = {row["name"] for row in conn.execute("pragma table_info(reports)").fetchall()}
    if "status" not in report_columns:
        conn.execute("alter table reports add column status text not null default 'open'")
    conn.execute("insert or replace into meta(key, value) values('schema', ?)", (str(SCHEMA),))
    conn.commit()
    return conn


def event_path(coord: Path) -> Path:
    return coord / "events.jsonl"


def session_finished(coord: Path) -> bool:
    conn = connect(coord)
    row = conn.execute("select 1 from events where type = 'session.finished' order by seq desc limit 1").fetchone()
    conn.close()
    return bool(row)


def append_event(coord: Path, event_type: str, actor: str, payload: dict) -> dict:
    event_path(coord).parent.mkdir(parents=True, exist_ok=True)
    event = {
        "id": f"evt_{uuid.uuid4().hex[:12]}",
        "schema": SCHEMA,
        "type": event_type,
        "actor": actor,
        "ts": now_iso(),
        **payload,
    }
    with event_path(coord).open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    index_event(coord, event)
    return event


def index_event(coord: Path, event: dict) -> None:
    conn = connect(coord)
    payload = json.dumps(event, sort_keys=True)
    row = conn.execute("select coalesce(max(seq), 0) + 1 as next_seq from events").fetchone()
    seq = int(row["next_seq"])
    conn.execute(
        "insert or ignore into events(id, seq, type, actor, ts, payload) values(?, ?, ?, ?, ?, ?)",
        (event["id"], seq, event["type"], event["actor"], event["ts"], payload),
    )

    if event["type"] == "work.created":
        conn.execute(
            """
            insert or replace into work_items(
              id, status, title, details, acceptance, risk, actor, created_at, updated_at
            )
            values(?, 'pending', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["task_id"],
                event.get("title", ""),
                event.get("details", ""),
                event.get("acceptance", ""),
                event.get("risk", ""),
                event["actor"],
                event["ts"],
                event["ts"],
            ),
        )
    elif event["type"] == "work.claimed":
        conn.execute(
            """
            update work_items
            set status = 'claimed',
                claimed_by = ?,
                lease_expires_at = ?,
                updated_at = ?
            where id = ?
            """,
            (event["actor"], event["lease_expires_at"], event["ts"], event["task_id"]),
        )
    elif event["type"] == "work.released":
        conn.execute(
            """
            update work_items
            set status = 'pending',
                claimed_by = null,
                lease_expires_at = null,
                updated_at = ?
            where id = ? and status != 'completed'
            """,
            (event["ts"], event["task_id"]),
        )
    elif event["type"] == "work.completed":
        conn.execute(
            """
            update work_items
            set status = 'completed',
                claimed_by = null,
                lease_expires_at = null,
                completed_by = ?,
                change_id = coalesce(?, change_id),
                updated_at = ?
            where id = ?
            """,
            (event["actor"], event.get("change_id"), event["ts"], event["task_id"]),
        )
    elif event["type"] == "change.created":
        conn.execute(
            """
            insert or replace into changes(id, task_id, status, actor, summary, risk, created_at)
            values(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["change_id"],
                event.get("task_id"),
                event.get("status", "ready-for-review"),
                event["actor"],
                event.get("summary", ""),
                event.get("risk", ""),
                event["ts"],
            ),
        )
        for path in event.get("files", []):
            conn.execute(
                "insert or ignore into change_files(change_id, path) values(?, ?)",
                (event["change_id"], path),
            )
        for role in event.get("task_roles", ["reviewer", "tester"]):
            conn.execute(
                """
                insert or ignore into tasks(role, change_id, status, updated_at)
                values(?, ?, 'pending', ?)
                """,
                (role, event["change_id"], event["ts"]),
            )
    elif event["type"] == "change.verified":
        conn.execute(
            "update changes set status = 'verified', verified_at = ? where id = ?",
            (event["ts"], event["change_id"]),
        )
    elif event["type"] == "change.committed":
        conn.execute(
            "update changes set status = 'committed', committed_at = ? where id = ?",
            (event["ts"], event["change_id"]),
        )
    elif event["type"] == "change.pushed":
        conn.execute(
            "update changes set status = 'pushed', pushed_at = ? where id = ?",
            (event["ts"], event["change_id"]),
        )
    elif event["type"] in ("review.completed", "test.completed"):
        report_id = event.get("report_id") or f"rpt_{uuid.uuid4().hex[:12]}"
        role = "reviewer" if event["type"] == "review.completed" else "tester"
        conn.execute(
            """
            insert or replace into reports(id, change_id, role, actor, decision, created_at)
            values(?, ?, ?, ?, ?, ?)
            """,
            (report_id, event["change_id"], role, event["actor"], event.get("decision", ""), event["ts"]),
        )
        for finding in event.get("findings", []):
            conn.execute(
                """
                insert or replace into findings(id, report_id, change_id, severity, file, line, message, status)
                values(?, ?, ?, ?, ?, ?, ?, 'open')
                """,
                (
                    finding.get("id") or f"fnd_{uuid.uuid4().hex[:12]}",
                    report_id,
                    event["change_id"],
                    finding.get("severity", "medium"),
                    finding.get("file", ""),
                    finding.get("line"),
                    finding.get("message", ""),
                ),
            )
    elif event["type"] == "finding.resolved":
        conn.execute("update findings set status = 'resolved' where id = ?", (event["finding_id"],))
    elif event["type"] == "report.resolved":
        conn.execute("update reports set status = 'resolved' where id = ?", (event["report_id"],))
    elif event["type"] == "task.claimed":
        conn.execute(
            """
            insert into tasks(role, change_id, status, claimed_by, lease_expires_at, updated_at)
            values(?, ?, 'claimed', ?, ?, ?)
            on conflict(role, change_id) do update set
              status = 'claimed',
              claimed_by = excluded.claimed_by,
              lease_expires_at = excluded.lease_expires_at,
              updated_at = excluded.updated_at
            """,
            (event["role"], event["change_id"], event["actor"], event["lease_expires_at"], event["ts"]),
        )
    elif event["type"] == "task.released":
        conn.execute(
            """
            update tasks
            set status = 'pending', claimed_by = null, lease_expires_at = null, updated_at = ?
            where role = ? and change_id = ? and status != 'completed'
            """,
            (event["ts"], event["role"], event["change_id"]),
        )
    elif event["type"] == "task.completed":
        conn.execute(
            """
            insert into tasks(role, change_id, status, claimed_by, lease_expires_at, completed_by, updated_at)
            values(?, ?, 'completed', null, null, ?, ?)
            on conflict(role, change_id) do update set
              status = 'completed',
              claimed_by = null,
              lease_expires_at = null,
              completed_by = excluded.completed_by,
              updated_at = excluded.updated_at
            """,
            (event["role"], event["change_id"], event["actor"], event["ts"]),
        )
    if event["type"] == "change.created" and event.get("task_id"):
        conn.execute(
            """
            update work_items
            set change_id = ?, updated_at = ?
            where id = ? and change_id is null
            """,
            (event["change_id"], event["ts"], event["task_id"]),
        )

    conn.commit()
    conn.close()


def rebuild(coord: Path) -> None:
    if db_path(coord).exists():
        db_path(coord).unlink()
    connect(coord).close()
    if not event_path(coord).exists():
        event_path(coord).write_text("", encoding="utf-8")
        return
    for line in event_path(coord).read_text(encoding="utf-8").splitlines():
        if line.strip():
            index_event(coord, json.loads(line))


def next_change_id(coord: Path) -> str:
    conn = connect(coord)
    row = conn.execute(
        """
        select max(cast(substr(id, 5) as integer)) as n
        from changes
        where id glob 'chg_[0-9][0-9][0-9][0-9]*'
        """
    ).fetchone()
    conn.close()
    return f"chg_{int(row['n'] or 0) + 1:04d}"


def next_task_id(coord: Path) -> str:
    conn = connect(coord)
    row = conn.execute(
        """
        select max(cast(substr(id, 5) as integer)) as n
        from work_items
        where id glob 'job_[0-9][0-9][0-9][0-9]*'
        """
    ).fetchone()
    conn.close()
    return f"job_{int(row['n'] or 0) + 1:04d}"


def change_exists(coord: Path, change_id: str) -> bool:
    conn = connect(coord)
    row = conn.execute("select 1 from changes where id = ?", (change_id,)).fetchone()
    conn.close()
    return row is not None


def task_exists(coord: Path, task_id: str) -> bool:
    conn = connect(coord)
    row = conn.execute("select 1 from work_items where id = ?", (task_id,)).fetchone()
    conn.close()
    return row is not None


def report_exists(coord: Path, report_id: str) -> bool:
    conn = connect(coord)
    row = conn.execute("select 1 from reports where id = ?", (report_id,)).fetchone()
    conn.close()
    return row is not None


def load_change_event(conn: sqlite3.Connection, change_id: str) -> Optional[dict]:
    row = conn.execute(
        """
        select payload
        from events
        where type = 'change.created'
          and json_extract(payload, '$.change_id') = ?
        order by seq desc
        limit 1
        """,
        (change_id,),
    ).fetchone()
    return json.loads(row["payload"]) if row else None


def format_change_payload(event: dict, seq: Optional[int] = None) -> dict:
    payload = {
        "change_id": event["change_id"],
        "task_id": event.get("task_id", ""),
        "summary": event.get("summary", ""),
        "files": event.get("files", []),
        "verification": event.get("verification", []),
        "risk": event.get("risk", ""),
        "diff_path": event.get("diff_path", ""),
    }
    if seq is not None:
        payload = {"seq": seq, **payload}
    return payload


def format_task_payload(row: sqlite3.Row) -> dict:
    return {
        "task_id": row["id"],
        "status": row["status"],
        "title": row["title"],
        "details": row["details"],
        "acceptance": row["acceptance"],
        "risk": row["risk"] or "",
        "claimed_by": row["claimed_by"] or "",
        "lease_expires_at": row["lease_expires_at"] or "",
        "change_id": row["change_id"] or "",
    }


def markdown_change_id(change_id: str) -> str:
    return change_id.replace("chg_", "")


def append_markdown_change(coord: Path, event: dict) -> None:
    path = coord / "changes.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# Coordination Changes Ledger\n\n", encoding="utf-8")
    files = ", ".join(event.get("files", [])) or "unspecified"
    verification = event.get("verification") or ["not run"]
    summary = event.get("summary", "")
    entry = [
        f"## Change {markdown_change_id(event['change_id'])} - {event['ts']}",
        "",
        f"Status: {event.get('status', 'ready-for-review')}",
        f"Scope: {files}",
        "",
        "Summary:",
        f"- {summary}",
        "",
        "Verification:",
        *[f"- {item}" for item in verification],
        "",
        "Risk:",
        f"- {event.get('risk', 'medium')}",
        "",
        "Artifacts:",
        f"- diff: {event.get('diff_path') or 'none'}",
        "",
        "Open Questions:",
        "- none",
        "",
    ]
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(entry))


def append_markdown_report(coord: Path, event: dict, role: str) -> None:
    path = coord / ("reviews.md" if role == "reviewer" else "tests.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        title = "Reviews" if role == "reviewer" else "Tests"
        path.write_text(f"# Coordination {title}\n\n", encoding="utf-8")
    label = "Review" if role == "reviewer" else "Test"
    entry = [
        f"## {label} for Change {markdown_change_id(event['change_id'])} - {event['actor']} - {event['ts']}",
        "",
        "Status: completed",
        f"Decision: {event.get('decision', '')}",
        "",
    ]
    if role == "reviewer":
        entry.append("Findings:")
        findings = event.get("findings") or []
        entry.extend(
            [f"- {f.get('severity', 'medium')}: {f.get('file', '')}:{f.get('line', '')} - {f.get('message', '')}" for f in findings]
            or ["- none"]
        )
        entry.extend(["", "Files Read:"])
        entry.extend([f"- {item}" for item in event.get("files_read", [])] or ["- see report event"])
    else:
        entry.append("Commands:")
        entry.extend([f"- {cmd}" for cmd in event.get("commands", [])] or ["- none"])
        entry.extend(["", "Results:", f"- {event.get('result', event.get('decision', ''))}", "", "Untested:"])
        entry.extend([f"- {item}" for item in event.get("untested", [])] or ["- none"])
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(entry) + "\n\n")


def parse_finding(text: str) -> dict:
    parts = text.split(":", 3)
    if len(parts) == 4 and parts[2].isdigit():
        return {"severity": parts[0], "file": parts[1], "line": int(parts[2]), "message": parts[3]}
    return {"severity": "medium", "message": text}


def with_finding_ids(findings: List[dict]) -> List[dict]:
    return [{**finding, "id": finding.get("id") or f"fnd_{uuid.uuid4().hex[:12]}"} for finding in findings]


def capture_git_diff(repo: Path, coord: Path, change_id: str, files: List[str]) -> str:
    artifacts = coord / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    diff_path = artifacts / f"{change_id}.diff"
    cmd = ["git", "-C", str(repo), "diff", "--"]
    if files:
        cmd.extend(files)
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff failed")
    diff_path.write_text(result.stdout, encoding="utf-8")
    return str(diff_path.relative_to(coord))


def validate_report(role: str, args, findings: List[dict]) -> Optional[str]:
    if role == "reviewer" and args.decision == "blocking" and not findings:
        return "review blocking reports require at least one --finding"
    if role == "tester" and args.decision == "fail" and not args.command and not findings:
        return "test fail reports require at least one --command or --finding"
    if role == "tester" and args.decision == "blocked" and not args.untested:
        return "test blocked reports require at least one --untested reason"
    return None


def cmd_init(args) -> int:
    coord = coord_dir(Path(args.repo))
    with locked(coord):
        for subdir in ("artifacts", "logs", "reports", "templates"):
            (coord / subdir).mkdir(parents=True, exist_ok=True)
        event_path(coord).touch(exist_ok=True)
        rebuild(coord)
    print(f"coord initialized: {coord}")
    return 0


def cmd_rebuild(args) -> int:
    coord = coord_dir(Path(args.repo))
    with locked(coord):
        rebuild(coord)
    print(f"rebuilt index: {db_path(coord)}")
    return 0


def cmd_task_create(args) -> int:
    coord = coord_dir(Path(args.repo))
    with locked(coord):
        task_id = args.id or next_task_id(coord)
        append_event(
            coord,
            "work.created",
            args.actor,
            {
                "task_id": task_id,
                "title": args.title,
                "details": args.details,
                "acceptance": args.acceptance,
                "risk": args.risk,
            },
        )
    print(task_id)
    return 0


def cmd_task_list(args) -> int:
    coord = coord_dir(Path(args.repo))
    conn = connect(coord)
    where = "" if args.all else "where status != 'completed'"
    rows = conn.execute(
        f"""
        select *
        from work_items
        {where}
        order by created_at
        limit ?
        """,
        (args.limit,),
    ).fetchall()
    conn.close()
    if not rows:
        print("No open tasks.")
        return 0
    for row in rows:
        owner = f" claimed_by={row['claimed_by']}" if row["claimed_by"] else ""
        change = f" change={row['change_id']}" if row["change_id"] else ""
        print(f"{row['id']} {row['status']}{owner}{change} risk={row['risk'] or '-'}")
        print(f"  {row['title']}")
    return 0


def cmd_task_claim(args) -> int:
    coord = coord_dir(Path(args.repo))
    with locked(coord):
        conn = connect(coord)
        current_time = now_iso()
        params = [args.actor, current_time]
        task_filter = ""
        if args.task:
            task_filter = "and id = ?"
            params.append(args.task)
        row = conn.execute(
            f"""
            select *
            from work_items
            where status != 'completed'
              and (
                status = 'pending'
                or claimed_by = ?
                or lease_expires_at <= ?
              )
              {task_filter}
            order by created_at
            limit 1
            """,
            params,
        ).fetchone()
        if not row:
            conn.close()
            if not getattr(args, "quiet", False):
                print("NO_CLAIMABLE_TASK")
            return 2
        task_id = row["id"]
        lease_expires_at = add_seconds_iso(args.ttl)
        conn.close()
        append_event(
            coord,
            "work.claimed",
            args.actor,
            {
                "task_id": task_id,
                "lease_expires_at": lease_expires_at,
                "ttl": args.ttl,
            },
        )
    payload = format_task_payload(row)
    payload.update({"claimed_by": args.actor, "lease_expires_at": lease_expires_at})
    print(json.dumps(payload, indent=2))
    return 0


def cmd_task_release(args) -> int:
    coord = coord_dir(Path(args.repo))
    with locked(coord):
        conn = connect(coord)
        row = conn.execute("select status, claimed_by from work_items where id = ?", (args.task,)).fetchone()
        conn.close()
        if not row:
            print(f"UNKNOWN_TASK {args.task}")
            return 2
        if row["status"] != "claimed":
            print("TASK_NOT_CLAIMED")
            return 2
        if row["claimed_by"] != args.actor and not args.force:
            print(f"CLAIMED_BY {row['claimed_by']}")
            return 2
        append_event(coord, "work.released", args.actor, {"task_id": args.task})
    print(f"RELEASED {args.task}")
    return 0


def cmd_task_complete(args) -> int:
    coord = coord_dir(Path(args.repo))
    with locked(coord):
        conn = connect(coord)
        row = conn.execute("select status, claimed_by, lease_expires_at from work_items where id = ?", (args.task,)).fetchone()
        conn.close()
        if not row:
            print(f"UNKNOWN_TASK {args.task}")
            return 2
        if row["status"] == "claimed" and row["claimed_by"] != args.actor and row["lease_expires_at"] > now_iso():
            print(f"TASK_CLAIMED_BY {row['claimed_by']}")
            return 2
        if args.change and not change_exists(coord, args.change):
            print(f"UNKNOWN_CHANGE {args.change}")
            return 2
        append_event(coord, "work.completed", args.actor, {"task_id": args.task, "change_id": args.change})
    print(f"TASK_COMPLETED {args.task}")
    return 0


def cmd_task_watch(args) -> int:
    deadline = time.time() + args.timeout if args.timeout else None
    while True:
        if session_finished(coord_dir(Path(args.repo))):
            print("SESSION_FINISHED")
            return 0
        args.quiet = args.quiet or not args.once
        rc = cmd_task_claim(args)
        if rc == 0 or args.once:
            return rc
        if deadline and time.time() >= deadline:
            print("WATCH_TIMEOUT")
            return 2
        time.sleep(args.interval)


def cmd_change_create(args) -> int:
    repo = Path(args.repo).expanduser().resolve()
    coord = coord_dir(repo)
    with locked(coord):
        if args.task and not task_exists(coord, args.task):
            print(f"UNKNOWN_TASK {args.task}")
            return 2
        change_id = args.id or next_change_id(coord)
        diff_path = ""
        if args.capture_diff:
            try:
                diff_path = capture_git_diff(repo, coord, change_id, args.file)
            except RuntimeError as exc:
                print(f"DIFF_CAPTURE_FAILED {exc}")
                return 2
        event = append_event(
            coord,
            "change.created",
            args.actor,
            {
                "change_id": change_id,
                "task_id": args.task,
                "status": "ready-for-review",
                "files": args.file,
                "summary": args.summary,
                "verification": args.verify,
                "risk": args.risk,
                "diff_path": diff_path,
            },
        )
        append_markdown_change(coord, event)
    print(change_id)
    return 0


def cmd_change_event(args, event_type: str) -> int:
    coord = coord_dir(Path(args.repo))
    with locked(coord):
        if not change_exists(coord, args.change):
            print(f"UNKNOWN_CHANGE {args.change}")
            return 2
        append_event(coord, event_type, args.actor, {"change_id": args.change})
    print(f"{event_type}: {args.change}")
    return 0


def cmd_report(args, role: str) -> int:
    coord = coord_dir(Path(args.repo))
    event_type = "review.completed" if role == "reviewer" else "test.completed"
    findings = with_finding_ids([parse_finding(item) for item in getattr(args, "finding", [])])
    validation_error = validate_report(role, args, findings)
    if validation_error:
        print(f"INVALID_REPORT {validation_error}")
        return 2
    payload = {
        "report_id": args.id or f"rpt_{uuid.uuid4().hex[:12]}",
        "change_id": args.change,
        "decision": args.decision,
        "findings": findings,
    }
    if role == "tester":
        payload.update({"commands": args.command, "result": args.result or args.decision, "untested": args.untested})
    else:
        payload.update({"files_read": args.files_read})
    with locked(coord):
        if not change_exists(coord, args.change):
            print(f"UNKNOWN_CHANGE {args.change}")
            return 2
        conn = connect(coord)
        task = conn.execute(
            "select status, claimed_by, lease_expires_at from tasks where role = ? and change_id = ?",
            (role, args.change),
        ).fetchone()
        conn.close()
        if task and task["status"] == "claimed" and task["claimed_by"] != args.actor and task["lease_expires_at"] > now_iso():
            print(f"TASK_CLAIMED_BY {task['claimed_by']}")
            return 2
        event = append_event(coord, event_type, args.actor, payload)
        append_markdown_report(coord, event, role)
    print(payload["report_id"])
    return 0


def cmd_status(args) -> int:
    coord = coord_dir(Path(args.repo))
    conn = connect(coord)
    work_rows = conn.execute(
        """
        select id, status, title, coalesce(claimed_by, '') as claimed_by, coalesce(change_id, '') as change_id
        from work_items
        where status != 'completed'
        order by created_at
        limit ?
        """,
        (args.limit,),
    ).fetchall()
    rows = conn.execute(
        """
        select c.id, c.status, c.summary,
               coalesce(group_concat(distinct r.role || ':' || r.decision), '') as reports,
               coalesce(group_concat(distinct t.role || ':' || t.status), '') as tasks,
               (
                 (select count(*) from findings f where f.change_id = c.id and f.status = 'open'
                 and (f.severity in ('high','critical','blocking') or f.message like '%block%'))
                 +
                 (select count(*) from reports r where r.change_id = c.id
                  and r.status = 'open'
                  and r.decision in ('blocking','fail','blocked'))
               ) as blockers
        from changes c
        left join reports r on r.change_id = c.id
        left join tasks t on t.change_id = c.id
        group by c.id
        order by c.created_at desc
        limit ?
        """,
        (args.limit,),
    ).fetchall()
    if work_rows:
        print("Open work:")
        for row in work_rows:
            owner = f" claimed_by={row['claimed_by']}" if row["claimed_by"] else ""
            change = f" change={row['change_id']}" if row["change_id"] else ""
            print(f"{row['id']} {row['status']}{owner}{change}")
            print(f"  {row['title']}")
    if not rows:
        if not work_rows:
            print("No changes.")
        conn.close()
        return 0
    if work_rows:
        print("Changes:")
    for row in rows:
        print(f"{row['id']} {row['status']} blockers={row['blockers']} tasks={row['tasks'] or '-'} reports={row['reports'] or '-'}")
        print(f"  {row['summary']}")
    conn.close()
    return 0


def cmd_blockers(args) -> int:
    coord = coord_dir(Path(args.repo))
    conn = connect(coord)
    findings = conn.execute(
        """
        select id, change_id, severity, file, line, message
        from findings
        where status = 'open'
          and (severity in ('high','critical','blocking') or message like '%block%')
        order by change_id, id
        """
    ).fetchall()
    reports = conn.execute(
        """
        select id, change_id, role, actor, decision
        from reports
        where status = 'open'
          and decision in ('blocking','fail','blocked')
        order by change_id, id
        """
    ).fetchall()
    if not findings and not reports:
        print("No open blockers.")
        conn.close()
        return 0
    for row in reports:
        print(f"{row['id']} {row['change_id']} {row['role']}:{row['decision']} actor={row['actor']}")
    for row in findings:
        loc = f"{row['file']}:{row['line']}" if row["file"] else "-"
        print(f"{row['id']} {row['change_id']} {row['severity']} {loc} {row['message']}")
    conn.close()
    return 1


def cmd_next(args) -> int:
    coord = coord_dir(Path(args.repo))
    conn = connect(coord)
    offset = conn.execute("select last_seq from agent_offsets where actor = ?", (args.actor,)).fetchone()
    last_seq = int(offset["last_seq"]) if offset else 0
    current_time = now_iso()
    row = conn.execute(
        """
        select e.seq, e.payload
        from events e
        join tasks t
          on t.change_id = json_extract(e.payload, '$.change_id')
         and t.role = ?
        where e.seq > ? and e.type = 'change.created'
          and (
            t.status = 'pending'
            or (t.status = 'claimed' and t.claimed_by = ?)
            or (t.status = 'claimed' and t.lease_expires_at <= ?)
          )
          and not exists (
            select 1 from reports r
            where r.change_id = json_extract(e.payload, '$.change_id') and r.actor = ?
          )
        order by e.seq
        limit 1
        """,
        (args.role, last_seq, args.actor, current_time, args.actor),
    ).fetchone()
    if not row:
        if not getattr(args, "quiet", False):
            print("NO_CHANGE")
        conn.close()
        return 2
    event = json.loads(row["payload"])
    print(json.dumps(format_change_payload(event, row["seq"]), indent=2))
    conn.close()
    return 0


def cmd_mark_processed(args) -> int:
    coord = coord_dir(Path(args.repo))
    with locked(coord):
        conn = connect(coord)
        row = conn.execute(
            "select max(seq) as seq from events where type = 'change.created' and json_extract(payload, '$.change_id') = ?",
            (args.change,),
        ).fetchone()
        if not row or row["seq"] is None:
            print("UNKNOWN_CHANGE")
            conn.close()
            return 2
        task = conn.execute(
            "select status, claimed_by, lease_expires_at from tasks where role = ? and change_id = ?",
            (args.role, args.change),
        ).fetchone()
        if task and task["status"] == "claimed" and task["claimed_by"] != args.actor and task["lease_expires_at"] > now_iso():
            print(f"TASK_CLAIMED_BY {task['claimed_by']}")
            conn.close()
            return 2
        conn.execute(
            "insert or replace into agent_offsets(actor, role, last_seq, updated_at) values(?, ?, ?, ?)",
            (args.actor, args.role, int(row["seq"]), now_iso()),
        )
        conn.commit()
        conn.close()
        append_event(coord, "task.completed", args.actor, {"role": args.role, "change_id": args.change})
    print(f"MARKED_PROCESSED {args.actor} {args.change}")
    return 0


def cmd_watch(args) -> int:
    deadline = time.time() + args.timeout if args.timeout else None
    while True:
        if session_finished(coord_dir(Path(args.repo))):
            print("SESSION_FINISHED")
            return 0
        args.quiet = args.quiet or not args.once
        rc = cmd_claim(args) if args.claim else cmd_next(args)
        if rc == 0 or args.once:
            return rc
        if deadline and time.time() >= deadline:
            print("WATCH_TIMEOUT")
            return 2
        time.sleep(args.interval)


def cmd_claim(args) -> int:
    coord = coord_dir(Path(args.repo))
    with locked(coord):
        conn = connect(coord)
        current_time = now_iso()
        params = [args.role, args.actor, current_time]
        change_filter = ""
        if args.change:
            change_filter = "and t.change_id = ?"
            params.append(args.change)
        row = conn.execute(
            f"""
            select t.change_id, e.seq, e.payload
            from tasks t
            join events e
              on e.type = 'change.created'
             and json_extract(e.payload, '$.change_id') = t.change_id
            where t.role = ?
              and t.status != 'completed'
              and (
                t.status = 'pending'
                or t.claimed_by = ?
                or t.lease_expires_at <= ?
              )
              {change_filter}
            order by e.seq
            limit 1
            """,
            params,
        ).fetchone()
        if not row:
            conn.close()
            if not getattr(args, "quiet", False):
                print("NO_CLAIMABLE_TASK")
            return 2
        event = json.loads(row["payload"])
        lease_expires_at = add_seconds_iso(args.ttl)
        change_id = row["change_id"]
        seq = row["seq"]
        conn.close()
        append_event(
            coord,
            "task.claimed",
            args.actor,
            {
                "role": args.role,
                "change_id": change_id,
                "lease_expires_at": lease_expires_at,
                "ttl": args.ttl,
            },
        )
    payload = format_change_payload(event, seq)
    payload.update({"claimed_by": args.actor, "lease_expires_at": lease_expires_at})
    print(json.dumps(payload, indent=2))
    return 0


def cmd_release(args) -> int:
    coord = coord_dir(Path(args.repo))
    with locked(coord):
        conn = connect(coord)
        row = conn.execute(
            "select status, claimed_by from tasks where role = ? and change_id = ?",
            (args.role, args.change),
        ).fetchone()
        conn.close()
        if not row:
            print("UNKNOWN_TASK")
            return 2
        if row["status"] != "claimed":
            print("TASK_NOT_CLAIMED")
            return 2
        if row["claimed_by"] != args.actor and not args.force:
            print(f"CLAIMED_BY {row['claimed_by']}")
            return 2
        append_event(coord, "task.released", args.actor, {"role": args.role, "change_id": args.change})
    print(f"RELEASED {args.role} {args.change}")
    return 0


def cmd_show(args) -> int:
    coord = coord_dir(Path(args.repo))
    conn = connect(coord)
    change = conn.execute("select * from changes where id = ?", (args.change,)).fetchone()
    if not change:
        conn.close()
        print(f"UNKNOWN_CHANGE {args.change}")
        return 2
    files = [row["path"] for row in conn.execute("select path from change_files where change_id = ? order by path", (args.change,))]
    tasks = [dict(row) for row in conn.execute("select * from tasks where change_id = ? order by role", (args.change,))]
    reports = [dict(row) for row in conn.execute("select * from reports where change_id = ? order by created_at", (args.change,))]
    findings = [dict(row) for row in conn.execute("select * from findings where change_id = ? order by id", (args.change,))]
    event = load_change_event(conn, args.change) or {}
    conn.close()
    output = {
        "change": dict(change),
        "files": files,
        "verification": event.get("verification", []),
        "diff_path": event.get("diff_path", ""),
        "tasks": tasks,
        "reports": reports,
        "findings": findings,
    }
    print(json.dumps(output, indent=2))
    return 0


def cmd_open(args) -> int:
    coord = coord_dir(Path(args.repo))
    conn = connect(coord)
    rows = conn.execute(
        """
        select c.id, c.status, c.summary,
               coalesce(group_concat(distinct t.role || ':' || t.status), '') as tasks,
               (
                 (select count(*) from findings f where f.change_id = c.id and f.status = 'open'
                  and (f.severity in ('high','critical','blocking') or f.message like '%block%'))
                 +
                 (select count(*) from reports r where r.change_id = c.id
                  and r.status = 'open'
                  and r.decision in ('blocking','fail','blocked'))
               ) as blockers
        from changes c
        left join tasks t on t.change_id = c.id
        where exists (
            select 1 from tasks ot
            where ot.change_id = c.id and ot.status != 'completed'
          )
          or exists (
            select 1 from findings f
            where f.change_id = c.id and f.status = 'open'
              and (f.severity in ('high','critical','blocking') or f.message like '%block%')
          )
          or exists (
            select 1 from reports r
            where r.change_id = c.id and r.status = 'open'
              and r.decision in ('blocking','fail','blocked')
          )
        group by c.id
        order by c.created_at desc
        limit ?
        """,
        (args.limit,),
    ).fetchall()
    conn.close()
    if not rows:
        print("No open changes.")
        return 0
    for row in rows:
        print(f"{row['id']} {row['status']} blockers={row['blockers']} tasks={row['tasks'] or '-'}")
        print(f"  {row['summary']}")
    return 0


def final_state(coord: Path) -> tuple[bool, list[str]]:
    conn = connect(coord)
    open_work = conn.execute(
        """
        select id, status, title, coalesce(claimed_by, '') as claimed_by
        from work_items
        where status != 'completed'
        order by created_at
        """
    ).fetchall()
    open_tasks = conn.execute(
        """
        select role, change_id, status, coalesce(claimed_by, '') as claimed_by
        from tasks
        where status != 'completed'
        order by change_id, role
        """
    ).fetchall()
    open_findings = conn.execute(
        """
        select id, change_id, severity, file, line, message
        from findings
        where status = 'open'
          and (severity in ('high','critical','blocking') or message like '%block%')
        order by change_id, id
        """
    ).fetchall()
    open_reports = conn.execute(
        """
        select id, change_id, role, decision
        from reports
        where status = 'open'
          and decision in ('blocking','fail','blocked')
        order by change_id, id
        """
    ).fetchall()
    conn.close()
    details = []
    for row in open_work:
        owner = f" claimed_by={row['claimed_by']}" if row["claimed_by"] else ""
        details.append(f"WORK {row['id']} {row['status']}{owner} {row['title']}")
    for row in open_tasks:
        owner = f" claimed_by={row['claimed_by']}" if row["claimed_by"] else ""
        details.append(f"TASK {row['change_id']} {row['role']}:{row['status']}{owner}")
    for row in open_reports:
        details.append(f"REPORT {row['id']} {row['change_id']} {row['role']}:{row['decision']}")
    for row in open_findings:
        loc = f"{row['file']}:{row['line']}" if row["file"] else "-"
        details.append(f"FINDING {row['id']} {row['change_id']} {row['severity']} {loc} {row['message']}")
    return not details, details


def cmd_wait_final(args) -> int:
    coord = coord_dir(Path(args.repo))
    deadline = time.time() + args.timeout if args.timeout else None
    while True:
        ready, details = final_state(coord)
        if ready:
            print("FINAL_READY")
            return 0
        if args.once or (deadline and time.time() >= deadline):
            print("FINAL_PENDING")
            for detail in details:
                print(f"- {detail}")
            return 2
        time.sleep(args.interval)


def cmd_finish(args) -> int:
    coord = coord_dir(Path(args.repo))
    ready, details = final_state(coord)
    if not ready and not args.force:
        print("FINISH_BLOCKED")
        for detail in details:
            print(f"- {detail}")
        return 2
    with locked(coord):
        append_event(coord, "session.finished", args.actor, {"reason": args.reason})
    print("SESSION_FINISHED")
    return 0


def cmd_timeline(args) -> int:
    coord = coord_dir(Path(args.repo))
    conn = connect(coord)
    exists = conn.execute("select 1 from changes where id = ?", (args.change,)).fetchone()
    if not exists:
        conn.close()
        print(f"UNKNOWN_CHANGE {args.change}")
        return 2
    rows = conn.execute(
        """
        select seq, type, actor, ts, payload
        from events
        where json_extract(payload, '$.change_id') = ?
           or json_extract(payload, '$.finding_id') in (
                select id from findings where change_id = ?
              )
           or json_extract(payload, '$.report_id') in (
                select id from reports where change_id = ?
              )
        order by seq
        """,
        (args.change, args.change, args.change),
    ).fetchall()
    conn.close()
    for row in rows:
        payload = json.loads(row["payload"])
        detail = payload.get("decision") or payload.get("summary") or payload.get("reason") or payload.get("lease_expires_at") or ""
        print(f"{row['seq']:04d} {row['ts']} {row['type']} actor={row['actor']} {detail}")
    return 0


def cmd_finding_resolve(args) -> int:
    coord = coord_dir(Path(args.repo))
    with locked(coord):
        conn = connect(coord)
        row = conn.execute("select 1 from findings where id = ?", (args.finding,)).fetchone()
        conn.close()
        if not row:
            print(f"UNKNOWN_FINDING {args.finding}")
            return 2
        append_event(coord, "finding.resolved", args.actor, {"finding_id": args.finding, "reason": args.reason})
    print(f"RESOLVED {args.finding}")
    return 0


def cmd_report_resolve(args) -> int:
    coord = coord_dir(Path(args.repo))
    with locked(coord):
        conn = connect(coord)
        row = conn.execute("select 1 from reports where id = ?", (args.report,)).fetchone()
        conn.close()
        if not row:
            print(f"UNKNOWN_REPORT {args.report}")
            return 2
        append_event(coord, "report.resolved", args.actor, {"report_id": args.report, "reason": args.reason})
    print(f"RESOLVED {args.report}")
    return 0


def cmd_doctor(args) -> int:
    coord = coord_dir(Path(args.repo))
    problems = []
    event_count = 0
    strict_state = {"changes": set(), "reports": set(), "findings": set(), "work": set()}
    if not coord.exists():
        problems.append(f"missing coordination directory: {coord}")
    events = event_path(coord)
    if not events.exists():
        problems.append("missing events.jsonl")
    else:
        for lineno, line in enumerate(events.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append(f"events.jsonl:{lineno}: invalid json: {exc}")
                continue
            event_count += 1
            for key in ("id", "schema", "type", "actor", "ts"):
                if key not in event:
                    problems.append(f"events.jsonl:{lineno}: missing {key}")
            if args.strict:
                problems.extend(validate_event_strict(event, lineno, strict_state))
    try:
        conn = connect(coord)
        indexed = conn.execute("select count(*) as n from events").fetchone()["n"]
        conn.close()
    except sqlite3.DatabaseError as exc:
        problems.append(f"coord.db error: {exc}")
        indexed = None
    if indexed is not None and indexed != event_count:
        problems.append(f"coord.db indexed_events={indexed} but events.jsonl has {event_count}; run coord.py rebuild")
    if problems:
        print("DOCTOR_FAIL")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("DOCTOR_OK")
    print(f"events={event_count}")
    return 0


def require_fields(event: dict, lineno: int, fields: List[str]) -> List[str]:
    return [f"events.jsonl:{lineno}: {event.get('type', '<unknown>')} missing {field}" for field in fields if field not in event]


def validate_event_strict(event: dict, lineno: int, state: dict) -> List[str]:
    problems = []
    event_type = event.get("type")
    if not EVENT_RE.match(str(event.get("id", ""))):
        problems.append(f"events.jsonl:{lineno}: invalid event id")
    if event.get("schema") != SCHEMA:
        problems.append(f"events.jsonl:{lineno}: unsupported schema {event.get('schema')}")

    if event_type == "change.created":
        problems.extend(require_fields(event, lineno, ["change_id", "files", "summary", "verification", "risk"]))
        change_id = event.get("change_id", "")
        if not CHANGE_RE.match(str(change_id)):
            problems.append(f"events.jsonl:{lineno}: invalid change_id {change_id}")
        if not isinstance(event.get("files", []), list):
            problems.append(f"events.jsonl:{lineno}: files must be a list")
        if not isinstance(event.get("verification", []), list):
            problems.append(f"events.jsonl:{lineno}: verification must be a list")
        if change_id in state["changes"]:
            problems.append(f"events.jsonl:{lineno}: duplicate change_id {change_id}")
        task_id = event.get("task_id")
        if task_id and task_id not in state.setdefault("work", set()):
            problems.append(f"events.jsonl:{lineno}: unknown task_id {task_id}")
        state["changes"].add(change_id)
    elif event_type == "work.created":
        problems.extend(require_fields(event, lineno, ["task_id", "title", "details", "acceptance", "risk"]))
        task_id = event.get("task_id", "")
        if not JOB_RE.match(str(task_id)):
            problems.append(f"events.jsonl:{lineno}: invalid task_id {task_id}")
        if task_id in state.setdefault("work", set()):
            problems.append(f"events.jsonl:{lineno}: duplicate task_id {task_id}")
        state["work"].add(task_id)
    elif event_type in ("work.claimed", "work.released", "work.completed"):
        problems.extend(require_fields(event, lineno, ["task_id"]))
        if event.get("task_id") not in state.setdefault("work", set()):
            problems.append(f"events.jsonl:{lineno}: unknown task_id {event.get('task_id')}")
        if event_type == "work.claimed" and "lease_expires_at" not in event:
            problems.append(f"events.jsonl:{lineno}: work.claimed missing lease_expires_at")
        if event_type == "work.completed" and event.get("change_id") and event.get("change_id") not in state["changes"]:
            problems.append(f"events.jsonl:{lineno}: unknown change_id {event.get('change_id')}")
    elif event_type in ("change.verified", "change.committed", "change.pushed"):
        problems.extend(require_fields(event, lineno, ["change_id"]))
        if event.get("change_id") not in state["changes"]:
            problems.append(f"events.jsonl:{lineno}: unknown change_id {event.get('change_id')}")
    elif event_type in ("review.completed", "test.completed"):
        problems.extend(require_fields(event, lineno, ["report_id", "change_id", "decision", "findings"]))
        report_id = event.get("report_id", "")
        change_id = event.get("change_id")
        if not REPORT_RE.match(str(report_id)):
            problems.append(f"events.jsonl:{lineno}: invalid report_id {report_id}")
        if change_id not in state["changes"]:
            problems.append(f"events.jsonl:{lineno}: unknown change_id {change_id}")
        if report_id in state["reports"]:
            problems.append(f"events.jsonl:{lineno}: duplicate report_id {report_id}")
        state["reports"].add(report_id)
        allowed = ("pass", "concerns", "blocking") if event_type == "review.completed" else ("pass", "fail", "blocked")
        if event.get("decision") not in allowed:
            problems.append(f"events.jsonl:{lineno}: invalid decision {event.get('decision')}")
        findings = event.get("findings", [])
        if not isinstance(findings, list):
            problems.append(f"events.jsonl:{lineno}: findings must be a list")
            findings = []
        if event_type == "review.completed" and event.get("decision") == "blocking" and not findings:
            problems.append(f"events.jsonl:{lineno}: blocking review requires a finding")
        if event_type == "test.completed" and event.get("decision") == "fail" and not event.get("commands") and not findings:
            problems.append(f"events.jsonl:{lineno}: failing test report requires command or finding")
        if event_type == "test.completed" and event.get("decision") == "blocked" and not event.get("untested"):
            problems.append(f"events.jsonl:{lineno}: blocked test report requires untested reason")
        for finding in findings:
            finding_id = finding.get("id", "")
            if not FINDING_RE.match(str(finding_id)):
                problems.append(f"events.jsonl:{lineno}: invalid finding id {finding_id}")
            if finding.get("severity", "medium") not in ("low", "medium", "high", "critical", "blocking"):
                problems.append(f"events.jsonl:{lineno}: invalid severity {finding.get('severity')}")
            if finding_id in state["findings"]:
                problems.append(f"events.jsonl:{lineno}: duplicate finding id {finding_id}")
            state["findings"].add(finding_id)
    elif event_type == "finding.resolved":
        problems.extend(require_fields(event, lineno, ["finding_id"]))
        if event.get("finding_id") not in state["findings"]:
            problems.append(f"events.jsonl:{lineno}: unknown finding_id {event.get('finding_id')}")
    elif event_type == "report.resolved":
        problems.extend(require_fields(event, lineno, ["report_id"]))
        if event.get("report_id") not in state["reports"]:
            problems.append(f"events.jsonl:{lineno}: unknown report_id {event.get('report_id')}")
    elif event_type in ("task.claimed", "task.released", "task.completed"):
        problems.extend(require_fields(event, lineno, ["role", "change_id"]))
        if event.get("role") not in ("reviewer", "tester"):
            problems.append(f"events.jsonl:{lineno}: invalid task role {event.get('role')}")
        if event.get("change_id") not in state["changes"]:
            problems.append(f"events.jsonl:{lineno}: unknown change_id {event.get('change_id')}")
        if event_type == "task.claimed" and "lease_expires_at" not in event:
            problems.append(f"events.jsonl:{lineno}: task.claimed missing lease_expires_at")
    elif event_type in TERMINAL_EVENT_TYPES:
        pass
    else:
        problems.append(f"events.jsonl:{lineno}: unknown event type {event_type}")
    return problems


def cmd_prompt(args) -> int:
    coord_cmd = "python3 ~/.codex/skills/agent-coordination/scripts/coord.py"
    repo_arg = "--repo ."
    role = args.role
    actor = args.actor
    if role in ("main", "coordinator"):
        text = f"""You are Main/Coordinator Codex. Use the agent-coordination skill.

In this repository:
1. You own planning, preflight risk review, task decomposition, route decisions, coordination state, and final user communication.
2. Do not do implementation work yourself in delegated mode. Assign implementation to Developer Codex with `task create`, then monitor task/change/review/test state. Only make direct source edits for tiny emergency fixes or when the user explicitly switches to simple mode.
3. Before starting execution, perform one preflight review for missing permissions/services, destructive-risk commands, external logins/credentials, and conflicting requirements. Ask the user before starting if any exist; if none exist, state that normal local code edits, tests, commits, and pushes by Developer will not be reconfirmed phase by phase.
4. Create focused Developer tasks:
   {coord_cmd} {repo_arg} task create --actor coordinator --title "<short title>" --details "<implementation details>" --acceptance "<verification and acceptance criteria>" --risk medium
5. During execution, inspect:
   {coord_cmd} {repo_arg} task list
   {coord_cmd} {repo_arg} blockers
   {coord_cmd} {repo_arg} open
   {coord_cmd} {repo_arg} status
6. Do not wait for fresh reviewer/tester reports before assigning the next verified low/medium-risk task when no blockers exist.
7. If blockers/fail/blocked appears, create or direct a Developer fix task before unrelated work.
8. After a Developer fix handles a blocker, close handled findings and reports with finding resolve and report resolve.
9. Use show/open/timeline/task list when you need detail.
10. After the user approves the route and says to start, do not stop at roadmap phase boundaries to report progress, ask whether to continue, or wait for confirmation; publish/inspect coordination state and continue to final delivery.
11. Stop for human input only for newly discovered permissions/credentials/destructive risk missed by preflight, conflicting requirements, environment interruption, or explicit user interrupt.
12. If the user only asks for status mid-run, briefly report task list/status/open/blockers and continue; do not treat a status question as a pause. Interrupt the unattended workflow only when the user explicitly asks to pause, stop, wait for confirmation, change direction, or not commit.
13. After all Developer tasks, implementation verification, commit, and push are complete, wait for the final review/test cycle, then signal secondary agents to stop before sending the final user response:
   {coord_cmd} {repo_arg} wait-final --timeout 1800 --interval 30
   {coord_cmd} {repo_arg} doctor
   {coord_cmd} {repo_arg} blockers
   git status --short
   {coord_cmd} {repo_arg} finish --actor coordinator
"""
    elif role == "developer":
        text = f"""You are Developer Codex. Use the agent-coordination skill.

Rules:
1. You own implementation, focused local verification, change publication, and commit/push when the Coordinator task asks for it or the repo workflow expects it.
2. Do not do final user communication, route planning, or reviewer/tester work.
3. Wait for and claim Developer work:
   {coord_cmd} {repo_arg} task watch --actor {actor} --interval 60 --quiet
4. When a task appears, read task_id, title, details, acceptance, and risk from the output. Implement only that task and any directly required fix.
5. Run targeted verification listed or implied by the task acceptance criteria.
6. Publish the implementation as a change linked to the task:
   {coord_cmd} {repo_arg} change create --actor {actor} --task <task_id> --capture-diff --file <file> --summary "<summary>" --verify "<command>" --risk medium
7. Mark the Developer task complete after publishing the change:
   {coord_cmd} {repo_arg} task complete --actor {actor} --task <task_id> --change <change_id>
8. Check blockers/open/status before claiming unrelated new work. If Reviewer/Tester reports a valid blocker for your change, fix it and publish a new linked change or follow the Coordinator's fix task.
9. Do not ask the user to relay results or confirm continuation; write progress to coordination state, then watch again.
10. If task watch prints SESSION_FINISHED, stop and do not restart the watch loop.
11. While waiting, stay silent. Do not send periodic "still waiting" chat messages.
"""
    elif role == "legacy-main":
        text = f"""You are Main Codex. Use the agent-coordination skill.

In this repository:
1. You own source edits, git state, verification, commits/pushes, and final user communication.
2. Before starting execution, perform one preflight review for missing permissions/services, destructive-risk commands, external logins/credentials, and conflicting requirements. Ask the user before starting if any exist; if none exist, state that normal local code edits, tests, commits, and pushes will not be reconfirmed phase by phase.
3. Before each implementation increment, run:
   {coord_cmd} {repo_arg} blockers
   {coord_cmd} {repo_arg} open
   {coord_cmd} {repo_arg} status
4. After each small increment, run targeted verification, then publish a change:
   {coord_cmd} {repo_arg} change create --capture-diff --file <file> --summary "<summary>" --verify "<command>" --risk medium
5. Do not wait for fresh reviewer/tester reports before continuing verified low/medium-risk increments.
6. If blockers/fail/blocked appears, fix it before unrelated work.
7. After fixing a blocker, close handled findings and reports with finding resolve and report resolve.
8. Use show/open/timeline when you need detail on a change.
9. After the user approves the route and says to start, do not stop at roadmap phase boundaries to report progress, ask whether to continue, or wait for confirmation; publish progress as changes/reports and continue to final delivery.
10. Stop for human input only for newly discovered permissions/credentials/destructive risk missed by preflight, conflicting requirements, environment interruption, or explicit user interrupt.
11. If the user only asks for status mid-run, briefly report status/open/blockers and continue; do not treat a status question as a pause. Interrupt the unattended workflow only when the user explicitly asks to pause, stop, wait for confirmation, change direction, or not commit.
12. After implementation, verification, commit, and push are complete, wait for the final review/test cycle, then signal secondary agents to stop before sending the final user response:
   {coord_cmd} {repo_arg} wait-final --timeout 1800 --interval 30
   {coord_cmd} {repo_arg} doctor
   {coord_cmd} {repo_arg} blockers
   git status --short
   {coord_cmd} {repo_arg} finish --actor main
"""
    elif role == "reviewer":
        text = f"""You are Reviewer Codex. Use the agent-coordination skill.

Rules:
1. Do not edit source files, commit, push, reset, install dependencies, or run broad formatters.
2. Perform read-only code review only.
3. Wait for and claim new changes:
   {coord_cmd} {repo_arg} watch --role reviewer --actor {actor} --claim --interval 60 --quiet
4. When a change appears, read change_id, files, verification, risk, and diff_path from the output.
5. Review the diff snapshot when present, then touched files and relevant contracts.
6. Report only real defects, regressions, compatibility risks, missing tests, security issues, or concurrency issues.
7. Publish a report:
   {coord_cmd} {repo_arg} report review --actor {actor} --change <change_id> --decision pass|concerns|blocking --files-read <file> --finding "severity:file:line:message"
8. After reporting, mark processed:
   {coord_cmd} {repo_arg} mark-processed --role reviewer --actor {actor} --change <change_id>
9. Do not ask the user to relay results or confirm continuation; write the report to coord, then watch again.
10. If watch prints SESSION_FINISHED, stop and do not restart the watch loop.
11. While waiting, stay silent. Do not send periodic "still waiting" chat messages.
"""
    elif role == "tester":
        text = f"""You are Tester Codex. Use the agent-coordination skill.

Rules:
1. Do not edit source files, commit, push, reset, install dependencies, or run destructive commands.
2. Do not fake hardware, vendor SDK, or external-service coverage.
3. Wait for and claim new changes:
   {coord_cmd} {repo_arg} watch --role tester --actor {actor} --claim --interval 60 --quiet
4. When a change appears, run the listed verification command first when safe, then add focused tests based on touched files.
5. Publish a test report:
   {coord_cmd} {repo_arg} report test --actor {actor} --change <change_id> --decision pass|fail|blocked --command "<command>" --untested "<reason>"
6. After reporting, mark processed:
   {coord_cmd} {repo_arg} mark-processed --role tester --actor {actor} --change <change_id>
7. Do not ask the user to relay results or confirm continuation; write the report to coord, then watch again.
8. If watch prints SESSION_FINISHED, stop and do not restart the watch loop.
9. While waiting, stay silent. Do not send periodic "still waiting" chat messages.
"""
    elif role == "observer":
        text = f"""You are Observer Codex. Use the agent-coordination skill.

Use a lightweight model when available; this role is for user-facing status explanation, not implementation.

Rules:
1. Do not edit source files, claim tasks, publish review/test reports, mark tasks processed, commit, push, reset, install dependencies, or direct Main/Developer/Reviewer/Tester.
2. Do not run project implementation, review, or test work. Only inspect coordination state and explain it to the user.
3. For status checks, run read-only queries as needed:
   {coord_cmd} {repo_arg} task list
   {coord_cmd} {repo_arg} status
   {coord_cmd} {repo_arg} open
   {coord_cmd} {repo_arg} blockers
   {coord_cmd} {repo_arg} show <change_id>
   {coord_cmd} {repo_arg} timeline <change_id>
   {coord_cmd} {repo_arg} export-html
4. Summarize what Main/Coordinator, Developer, Reviewer, and Tester have done; call out open Developer work, blockers, and pending reviews/tests.
5. If the user wants to pause, stop, change direction, or alter commit/push behavior, tell them to send that instruction directly to Main/Coordinator Codex.
"""
    else:
        print(f"UNKNOWN_ROLE {role}")
        return 2
    print(text.rstrip())
    return 0


def html_attr(value: object) -> str:
    return html.escape(str(value), quote=True)


def cmd_export_html(args) -> int:
    coord = coord_dir(Path(args.repo))
    conn = connect(coord)
    work = [dict(row) for row in conn.execute("select * from work_items order by created_at desc").fetchall()]
    changes = [dict(row) for row in conn.execute("select * from changes order by created_at desc").fetchall()]
    tasks = [dict(row) for row in conn.execute("select * from tasks order by change_id, role").fetchall()]
    reports = [dict(row) for row in conn.execute("select * from reports order by created_at desc").fetchall()]
    findings = [dict(row) for row in conn.execute("select * from findings order by change_id, id").fetchall()]
    events = [dict(row) for row in conn.execute("select seq, type, actor, ts, payload from events order by seq desc limit 80").fetchall()]
    conn.close()

    tasks_by_change = {}
    for task in tasks:
        tasks_by_change.setdefault(task["change_id"], []).append(task)
    reports_by_change = {}
    for report in reports:
        reports_by_change.setdefault(report["change_id"], []).append(report)
    findings_by_change = {}
    for finding in findings:
        findings_by_change.setdefault(finding["change_id"], []).append(finding)

    rows = []
    for change in changes:
        change_id = change["id"]
        task_text = (
            "<br>".join(
                f"{html_attr(t['role'])}: {html_attr(t['status'])}" + (f" by {html_attr(t['claimed_by'])}" if t.get("claimed_by") else "")
                for t in tasks_by_change.get(change_id, [])
            )
            or "-"
        )
        report_text = (
            "<br>".join(
                f"{html_attr(r['role'])}: {html_attr(r['decision'])} ({html_attr(r['status'])})"
                for r in reports_by_change.get(change_id, [])
            )
            or "-"
        )
        finding_text = (
            "<br>".join(
                f"{html_attr(f['severity'])} {html_attr(f['file'])}:{html_attr(f['line'])} {html_attr(f['message'])} ({html_attr(f['status'])})"
                for f in findings_by_change.get(change_id, [])
            )
            or "-"
        )
        rows.append(
            "<tr>"
            f"<td>{html_attr(change_id)}</td>"
            f"<td>{html_attr(change['status'])}</td>"
            f"<td>{html_attr(change['summary'])}</td>"
            f"<td>{task_text}</td>"
            f"<td>{report_text}</td>"
            f"<td>{finding_text}</td>"
            "</tr>"
        )

    event_rows = []
    for event in events:
        payload = json.loads(event["payload"])
        detail = payload.get("change_id") or payload.get("report_id") or payload.get("finding_id") or ""
        event_rows.append(
            "<tr>"
            f"<td>{html_attr(event['seq'])}</td>"
            f"<td>{html_attr(event['ts'])}</td>"
            f"<td>{html_attr(event['type'])}</td>"
            f"<td>{html_attr(event['actor'])}</td>"
            f"<td>{html_attr(detail)}</td>"
            "</tr>"
        )

    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent Coordination Status</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #172033; }}
    h1, h2 {{ margin-bottom: 8px; }}
    .muted {{ color: #667085; }}
    table {{ width: 100%; border-collapse: collapse; margin: 16px 0 32px; }}
    th, td {{ border: 1px solid #d8dee8; padding: 10px; text-align: left; vertical-align: top; font-size: 14px; }}
    th {{ background: #f5f7fa; }}
    tr:nth-child(even) td {{ background: #fbfcfe; }}
  </style>
</head>
<body>
  <h1>Agent Coordination Status</h1>
  <p class="muted">Generated at {html_attr(now_iso())}</p>
  <h2>Developer Work</h2>
  <table>
    <thead><tr><th>Task</th><th>Status</th><th>Title</th><th>Owner</th><th>Change</th></tr></thead>
    <tbody>{"".join(f"<tr><td>{html_attr(item['id'])}</td><td>{html_attr(item['status'])}</td><td>{html_attr(item['title'])}</td><td>{html_attr(item['claimed_by'] or '-')}</td><td>{html_attr(item['change_id'] or '-')}</td></tr>" for item in work) or '<tr><td colspan="5">No developer work</td></tr>'}</tbody>
  </table>
  <h2>Changes</h2>
  <table>
    <thead><tr><th>Change</th><th>Status</th><th>Summary</th><th>Tasks</th><th>Reports</th><th>Findings</th></tr></thead>
    <tbody>{"".join(rows) or '<tr><td colspan="6">No changes</td></tr>'}</tbody>
  </table>
  <h2>Recent Events</h2>
  <table>
    <thead><tr><th>Seq</th><th>Time</th><th>Type</th><th>Actor</th><th>Ref</th></tr></thead>
    <tbody>{"".join(event_rows) or '<tr><td colspan="5">No events</td></tr>'}</tbody>
  </table>
</body>
</html>
"""
    output = Path(args.output).expanduser()
    if not output.is_absolute():
        output = coord / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    print(output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Structured local coordination CLI for multi-Codex workflows.")
    parser.add_argument("--repo", default=".", help="Repository root.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init")
    init.set_defaults(func=cmd_init)
    rebuild_cmd = sub.add_parser("rebuild")
    rebuild_cmd.set_defaults(func=cmd_rebuild)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--strict", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    task = sub.add_parser("task")
    task_sub = task.add_subparsers(dest="task_cmd", required=True)
    task_create = task_sub.add_parser("create")
    task_create.add_argument("--id")
    task_create.add_argument("--actor", default="coordinator")
    task_create.add_argument("--title", required=True)
    task_create.add_argument("--details", required=True)
    task_create.add_argument("--acceptance", required=True)
    task_create.add_argument("--risk", default="medium")
    task_create.set_defaults(func=cmd_task_create)
    task_list = task_sub.add_parser("list")
    task_list.add_argument("--limit", type=int, default=20)
    task_list.add_argument("--all", action="store_true")
    task_list.set_defaults(func=cmd_task_list)
    task_claim = task_sub.add_parser("claim")
    task_claim.add_argument("--actor", required=True)
    task_claim.add_argument("--ttl", type=int, default=1800)
    task_claim.add_argument("--task")
    task_claim.add_argument("--quiet", action="store_true")
    task_claim.set_defaults(func=cmd_task_claim)
    task_watch = task_sub.add_parser("watch")
    task_watch.add_argument("--actor", required=True)
    task_watch.add_argument("--interval", type=float, default=60.0)
    task_watch.add_argument("--timeout", type=float)
    task_watch.add_argument("--once", action="store_true")
    task_watch.add_argument("--ttl", type=int, default=1800)
    task_watch.add_argument("--task")
    task_watch.add_argument("--quiet", action="store_true")
    task_watch.set_defaults(func=cmd_task_watch)
    task_release = task_sub.add_parser("release")
    task_release.add_argument("--actor", required=True)
    task_release.add_argument("--task", required=True)
    task_release.add_argument("--force", action="store_true")
    task_release.set_defaults(func=cmd_task_release)
    task_complete = task_sub.add_parser("complete")
    task_complete.add_argument("--actor", required=True)
    task_complete.add_argument("--task", required=True)
    task_complete.add_argument("--change")
    task_complete.set_defaults(func=cmd_task_complete)

    change = sub.add_parser("change")
    change_sub = change.add_subparsers(dest="change_cmd", required=True)
    create = change_sub.add_parser("create")
    create.add_argument("--id")
    create.add_argument("--actor", default="main")
    create.add_argument("--file", action="append", default=[])
    create.add_argument("--summary", required=True)
    create.add_argument("--verify", action="append", default=[])
    create.add_argument("--risk", default="medium")
    create.add_argument("--capture-diff", action="store_true")
    create.add_argument("--task", help="Developer work item this change completes, e.g. job_0001")
    create.set_defaults(func=cmd_change_create)
    for name, event_type in (("verify", "change.verified"), ("commit", "change.committed"), ("push", "change.pushed")):
        sp = change_sub.add_parser(name)
        sp.add_argument("--actor", default="main")
        sp.add_argument("change")
        sp.set_defaults(func=lambda args, et=event_type: cmd_change_event(args, et))

    report = sub.add_parser("report")
    report_sub = report.add_subparsers(dest="report_cmd", required=True)
    review = report_sub.add_parser("review")
    review.add_argument("--id")
    review.add_argument("--actor", default="reviewer-a")
    review.add_argument("--change", required=True)
    review.add_argument("--decision", choices=("pass", "concerns", "blocking"), required=True)
    review.add_argument("--finding", action="append", default=[], help="severity:file:line:message, or plain message")
    review.add_argument("--files-read", action="append", default=[], help="file inspected during review")
    review.set_defaults(func=lambda args: cmd_report(args, "reviewer"))
    report_resolve = report_sub.add_parser("resolve")
    report_resolve.add_argument("--actor", default="main")
    report_resolve.add_argument("--reason", default="")
    report_resolve.add_argument("report")
    report_resolve.set_defaults(func=cmd_report_resolve)
    test = report_sub.add_parser("test")
    test.add_argument("--id")
    test.add_argument("--actor", default="tester-a")
    test.add_argument("--change", required=True)
    test.add_argument("--decision", choices=("pass", "fail", "blocked"), required=True)
    test.add_argument("--command", action="append", default=[])
    test.add_argument("--result")
    test.add_argument("--untested", action="append", default=[])
    test.add_argument("--finding", action="append", default=[], help="severity:file:line:message, or plain message")
    test.set_defaults(func=lambda args: cmd_report(args, "tester"))

    status = sub.add_parser("status")
    status.add_argument("--limit", type=int, default=10)
    status.set_defaults(func=cmd_status)
    blockers = sub.add_parser("blockers")
    blockers.set_defaults(func=cmd_blockers)
    show = sub.add_parser("show")
    show.add_argument("change")
    show.set_defaults(func=cmd_show)
    open_cmd = sub.add_parser("open")
    open_cmd.add_argument("--limit", type=int, default=20)
    open_cmd.set_defaults(func=cmd_open)
    timeline = sub.add_parser("timeline")
    timeline.add_argument("change")
    timeline.set_defaults(func=cmd_timeline)
    prompt = sub.add_parser("prompt")
    prompt.add_argument("role", choices=("main", "coordinator", "developer", "reviewer", "tester", "observer", "legacy-main"))
    prompt.add_argument("--actor", default="reviewer-a")
    prompt.set_defaults(func=cmd_prompt)
    export_html = sub.add_parser("export-html")
    export_html.add_argument("--output", default="reports/status.html")
    export_html.set_defaults(func=cmd_export_html)
    wait_final = sub.add_parser("wait-final")
    wait_final.add_argument("--interval", type=float, default=30.0)
    wait_final.add_argument("--timeout", type=float, default=1800.0)
    wait_final.add_argument("--once", action="store_true")
    wait_final.set_defaults(func=cmd_wait_final)
    finish = sub.add_parser("finish")
    finish.add_argument("--actor", default="main")
    finish.add_argument("--reason", default="final handoff complete")
    finish.add_argument("--force", action="store_true")
    finish.set_defaults(func=cmd_finish)
    finding = sub.add_parser("finding")
    finding_sub = finding.add_subparsers(dest="finding_cmd", required=True)
    resolve = finding_sub.add_parser("resolve")
    resolve.add_argument("--actor", default="main")
    resolve.add_argument("--reason", default="")
    resolve.add_argument("finding")
    resolve.set_defaults(func=cmd_finding_resolve)
    next_cmd = sub.add_parser("next")
    next_cmd.add_argument("--role", required=True)
    next_cmd.add_argument("--actor", required=True)
    next_cmd.set_defaults(func=cmd_next)
    claim = sub.add_parser("claim")
    claim.add_argument("--role", required=True)
    claim.add_argument("--actor", required=True)
    claim.add_argument("--ttl", type=int, default=900)
    claim.add_argument("--change")
    claim.set_defaults(func=cmd_claim)
    release = sub.add_parser("release")
    release.add_argument("--role", required=True)
    release.add_argument("--actor", required=True)
    release.add_argument("--change", required=True)
    release.add_argument("--force", action="store_true")
    release.set_defaults(func=cmd_release)
    mark = sub.add_parser("mark-processed")
    mark.add_argument("--role", required=True)
    mark.add_argument("--actor", required=True)
    mark.add_argument("--change", required=True)
    mark.set_defaults(func=cmd_mark_processed)
    watch = sub.add_parser("watch")
    watch.add_argument("--role", required=True)
    watch.add_argument("--actor", required=True)
    watch.add_argument("--interval", type=float, default=60.0)
    watch.add_argument("--timeout", type=float)
    watch.add_argument("--once", action="store_true")
    watch.add_argument("--claim", action="store_true")
    watch.add_argument("--quiet", action="store_true", help="Suppress idle polling output while waiting.")
    watch.add_argument("--ttl", type=int, default=900)
    watch.add_argument("--change")
    watch.set_defaults(func=cmd_watch)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
