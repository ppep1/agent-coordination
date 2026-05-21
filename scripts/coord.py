#!/usr/bin/env python3
import argparse
import fcntl
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = 1
BLOCKING_DECISIONS = ("blocking", "fail", "blocked")
BLOCKING_SEVERITIES = ("high", "critical", "blocking")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        """
    )
    report_columns = {row["name"] for row in conn.execute("pragma table_info(reports)").fetchall()}
    if "status" not in report_columns:
        conn.execute("alter table reports add column status text not null default 'open'")
    conn.execute("insert or replace into meta(key, value) values('schema', ?)", (str(SCHEMA),))
    conn.commit()
    return conn


def event_path(coord: Path) -> Path:
    return coord / "events.jsonl"


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

    if event["type"] == "change.created":
        conn.execute(
            """
            insert or replace into changes(id, status, actor, summary, risk, created_at)
            values(?, ?, ?, ?, ?, ?)
            """,
            (
                event["change_id"],
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


def change_exists(coord: Path, change_id: str) -> bool:
    conn = connect(coord)
    row = conn.execute("select 1 from changes where id = ?", (change_id,)).fetchone()
    conn.close()
    return row is not None


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
        entry.extend([f"- {f.get('severity', 'medium')}: {f.get('file', '')}:{f.get('line', '')} - {f.get('message', '')}" for f in findings] or ["- none"])
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


def with_finding_ids(findings: list[dict]) -> list[dict]:
    return [{**finding, "id": finding.get("id") or f"fnd_{uuid.uuid4().hex[:12]}"} for finding in findings]


def cmd_init(args) -> int:
    coord = coord_dir(Path(args.repo))
    with locked(coord):
        for subdir in ("artifacts", "logs", "reports", "status", "templates"):
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


def cmd_change_create(args) -> int:
    coord = coord_dir(Path(args.repo))
    with locked(coord):
        change_id = args.id or next_change_id(coord)
        event = append_event(
            coord,
            "change.created",
            args.actor,
            {
                "change_id": change_id,
                "status": "ready-for-review",
                "files": args.file,
                "summary": args.summary,
                "verification": args.verify,
                "risk": args.risk,
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
    payload = {
        "report_id": args.id or f"rpt_{uuid.uuid4().hex[:12]}",
        "change_id": args.change,
        "decision": args.decision,
        "findings": with_finding_ids([parse_finding(item) for item in getattr(args, "finding", [])]),
    }
    if role == "tester":
        payload.update({"commands": args.command, "result": args.result or args.decision, "untested": args.untested})
    else:
        payload.update({"files_read": args.files_read})
    with locked(coord):
        if not change_exists(coord, args.change):
            print(f"UNKNOWN_CHANGE {args.change}")
            return 2
        event = append_event(coord, event_type, args.actor, payload)
        append_markdown_report(coord, event, role)
    print(payload["report_id"])
    return 0


def cmd_status(args) -> int:
    coord = coord_dir(Path(args.repo))
    conn = connect(coord)
    rows = conn.execute(
        """
        select c.id, c.status, c.summary,
               coalesce(group_concat(distinct r.role || ':' || r.decision), '') as reports,
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
        group by c.id
        order by c.created_at desc
        limit ?
        """,
        (args.limit,),
    ).fetchall()
    if not rows:
        print("No changes.")
        conn.close()
        return 0
    for row in rows:
        print(f"{row['id']} {row['status']} blockers={row['blockers']} reports={row['reports'] or '-'}")
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
    row = conn.execute(
        """
        select e.seq, e.payload
        from events e
        where e.seq > ? and e.type = 'change.created'
          and not exists (
            select 1 from reports r
            where r.change_id = json_extract(e.payload, '$.change_id') and r.actor = ?
          )
        order by e.seq
        limit 1
        """,
        (last_seq, args.actor),
    ).fetchone()
    if not row:
        print("NO_CHANGE")
        conn.close()
        return 2
    event = json.loads(row["payload"])
    print(json.dumps({
        "seq": row["seq"],
        "change_id": event["change_id"],
        "summary": event.get("summary", ""),
        "files": event.get("files", []),
        "verification": event.get("verification", []),
        "risk": event.get("risk", ""),
    }, indent=2))
    conn.close()
    return 0


def cmd_mark_processed(args) -> int:
    coord = coord_dir(Path(args.repo))
    conn = connect(coord)
    row = conn.execute(
        "select max(seq) as seq from events where type = 'change.created' and json_extract(payload, '$.change_id') = ?",
        (args.change,),
    ).fetchone()
    if not row or row["seq"] is None:
        print("UNKNOWN_CHANGE")
        conn.close()
        return 2
    conn.execute(
        "insert or replace into agent_offsets(actor, role, last_seq, updated_at) values(?, ?, ?, ?)",
        (args.actor, args.role, int(row["seq"]), now_iso()),
    )
    conn.commit()
    conn.close()
    print(f"MARKED_PROCESSED {args.actor} {args.change}")
    return 0


def cmd_watch(args) -> int:
    deadline = time.time() + args.timeout if args.timeout else None
    while True:
        rc = cmd_next(args)
        if rc == 0 or args.once:
            return rc
        if deadline and time.time() >= deadline:
            print("WATCH_TIMEOUT")
            return 2
        time.sleep(args.interval)


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Structured local coordination CLI for multi-Codex workflows.")
    parser.add_argument("--repo", default=".", help="Repository root.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init")
    init.set_defaults(func=cmd_init)
    rebuild_cmd = sub.add_parser("rebuild")
    rebuild_cmd.set_defaults(func=cmd_rebuild)
    doctor = sub.add_parser("doctor")
    doctor.set_defaults(func=cmd_doctor)

    change = sub.add_parser("change")
    change_sub = change.add_subparsers(dest="change_cmd", required=True)
    create = change_sub.add_parser("create")
    create.add_argument("--id")
    create.add_argument("--actor", default="main")
    create.add_argument("--file", action="append", default=[])
    create.add_argument("--summary", required=True)
    create.add_argument("--verify", action="append", default=[])
    create.add_argument("--risk", default="medium")
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
    watch.set_defaults(func=cmd_watch)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
