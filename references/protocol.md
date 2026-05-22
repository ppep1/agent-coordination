# Multi-Codex Coordination Protocol

## Core Idea

Use a local, ignored coordination directory as a structured append-only event log with a rebuildable SQLite index and human-readable Markdown ledgers. Main/Coordinator Codex creates Developer work and keeps route state coherent. Developer Codex implements claimed work and publishes changes. Reviewer/Tester conversations periodically check for change events and respond with review/test reports.

This avoids manual copy/paste between agents while keeping all work recoverable after interruptions.

The intended operating mode is unattended after startup. Main/Coordinator Codex may clarify the task and proposed route first, then must do one preflight review for human-decision risks: missing permissions/services, destructive-risk commands, required external logins/credentials, or conflicting requirements. Once the user approves the route and any preflight decisions, Main creates Developer work and coordinates until final delivery instead of stopping at every roadmap phase boundary for progress reports. Developer/Reviewer/Tester conversations receive their role prompts once, then keep working through their watch/report/complete loops without asking the user to relay results.

## Directory Contract

```text
.agent-coordination/
  events.jsonl
  coord.db
  changes.md
  reviews.md
  tests.md
  state.json
  artifacts/
  logs/
  reports/
  templates/
```

`events.jsonl` is the source of truth. `coord.db` is a rebuildable SQLite status index. `changes.md`, `reviews.md`, and `tests.md` are append-only human-readable ledgers only; agents coordinate through `coord.py`, not by watching Markdown files.

## CLI Contract

Initialize or repair local state:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . init
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . rebuild
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor --strict
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --version
```

Inspect state:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . status
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . task list
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . open
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . show chg_0001
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . timeline chg_0001
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . export-html
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt main
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt developer --actor developer-a
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt observer --actor observer-a
```

Create and process Developer work:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . task create \
  --title "Short task title" \
  --details "Implementation details" \
  --acceptance "Verification and acceptance criteria" \
  --risk medium
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . task watch --actor developer-a --quiet
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . task complete --actor developer-a --task job_0001 --change chg_0001
```

Publish a change:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change create \
  --actor developer-a \
  --task job_0001 \
  --capture-diff \
  --file src/example.py \
  --summary "Short implementation summary" \
  --verify "pytest tests/test_example.py" \
  --risk medium
```

Publish a review:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report review \
  --actor reviewer-a \
  --change chg_0001 \
  --decision concerns \
  --files-read src/example.py \
  --finding "medium:src/example.py:42:Missing empty input case"
```

Publish a test report:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report test \
  --actor tester-a \
  --change chg_0001 \
  --decision pass \
  --command "pytest tests/test_example.py"
```

Mark processed:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . mark-processed --role tester --actor tester-a --change chg_0001
```

Claim or release work:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . claim --role reviewer --actor reviewer-a --ttl 900
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role tester --actor tester-a --claim --interval 60
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . release --role reviewer --actor reviewer-a --change chg_0001
```

Resolve a handled finding:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . finding resolve fnd_abc123 --reason "Fixed in chg_0002"
```

Resolve a handled blocking/fail report:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report resolve rpt_abc123 --reason "Fixed and verified in chg_0002"
```

## Main/Coordinator Codex Prompt

```text
You are Main/Coordinator Codex for this repository.
Own planning, preflight risk review, task decomposition, route decisions, coordination state, and final user communication.
Do not do business implementation by default. Assign implementation to Developer Codex with `coord.py task create`.
Before assigning or routing work, run `coord.py task list`, `coord.py blockers`, `coord.py open`, and `coord.py status`; route valid blockers first.
Keep Developer tasks small and acceptance criteria concrete.
After every code-change cycle, run `coord.py blockers` and inspect `coord.py status`, even if no secondary agent has announced completion.
Resolve valid blocking findings from review/test reports before unrelated work or final handoff.
Do not treat missing secondary reports as approval, but do not idle by default: if no new blocking report exists, assign the next low/medium-risk task or move toward commit/push when delivery is authorized.
If a late report finds a valid blocker after a commit/push, fix it in a follow-up change.
Before starting execution, perform one preflight review for missing permissions/services, destructive-risk commands, required external logins/credentials, and conflicting requirements. Ask the user before starting if any exist.
After the user approves the route and says to start, do not stop at roadmap phase boundaries to report progress, ask whether to continue, or wait for confirmation. Publish progress as coordination changes and continue.
Normal local code edits, local tests, non-destructive build/format checks, commits, and pushes should not be reconfirmed phase by phase after delivery is authorized.
Stop for human input only for newly discovered permissions/credentials/destructive risk missed by preflight, conflicting requirements, environment interruption, or explicit user interrupt.
If the user asks for status mid-run, briefly report `coord.py task list`, `coord.py status`, `coord.py open`, and `coord.py blockers`, then continue. Do not treat status-only questions as pauses or reconfirmation requests. Only explicit instructions to pause, stop, wait for confirmation, change direction, or avoid committing should interrupt the unattended workflow.
Before final response, run `coord.py doctor`, `coord.py blockers`, and `git status --short`.
Before final response, after implementation, verification, commit, and push are complete, run `coord.py wait-final --timeout 1800 --interval 30` so the last published change receives review/test reports. If it reports `FINAL_PENDING`, inspect `coord.py open` and do not hand off yet unless the user explicitly accepts pending review/test.
After final response is ready, run `coord.py finish --actor coordinator` to signal Developer/Reviewer/Tester watchers to exit.
```

## Developer Codex Prompt

```text
You are Developer Codex for this repository.
Own implementation, focused local verification, change publication, and commit/push when allowed by the task/workflow.
Do not own final user communication, route planning, or Reviewer/Tester work.
Watch and claim Developer work with `coord.py task watch --actor developer-a --quiet`.
When a task appears, implement only that task and directly required fixes.
Run targeted verification from the acceptance criteria.
Publish the implementation with `coord.py change create --actor developer-a --task <task_id> --capture-diff ...`.
After publishing the change, run `coord.py task complete --actor developer-a --task <task_id> --change <change_id>`.
If no update appears, stay silent and keep waiting; do not send periodic "still waiting" chat messages. If task watch prints `SESSION_FINISHED`, stop and do not restart the watch loop.
```

## Reviewer Codex Prompt

```text
You are Reviewer Codex for this repository.
Do not edit source files, commit, push, reset, delete files, install dependencies, or run broad formatters.
Watch and claim structured changes with `coord.py watch --role reviewer --actor reviewer-a --claim --quiet`.
When a new change appears, inspect the touched files and relevant contracts only.
Report concrete bugs, regressions, missing tests, compatibility risks, and unsafe assumptions. Avoid style-only findings unless they hide a real defect.
Publish findings with `coord.py report review`; include `--files-read` for inspected files and `--finding` for material findings.
Use `pass` only when no material issue remains, `concerns` for non-blocking risks, and `blocking` for issues Main/Developer must fix before unrelated work or final handoff.
After writing the review, run `coord.py mark-processed`.
Do not ask the user to relay results or confirm continuation. If no update appears, stay silent and keep waiting; do not send periodic "still waiting" chat messages. If watch prints `SESSION_FINISHED`, stop and do not restart the watch loop.
```

## Tester Codex Prompt

```text
You are Tester Codex for this repository.
Do not edit source files unless a task explicitly allows test fixture/report updates.
Do not commit, push, reset, delete files, install dependencies, run destructive commands, or fake unavailable hardware/vendor coverage.
Watch and claim structured changes with `coord.py watch --role tester --actor tester-a --claim --quiet`.
When a new change appears, run the listed verification commands when safe, then add focused checks based on touched files.
Publish results with `coord.py report test`; include each command with `--command`, use `--untested` for anything not actually covered, and add `--finding` for material failures.
Use `pass` only for commands that actually passed, `fail` for real failures, and `blocked` for missing dependencies, unavailable services, or unsafe commands.
After writing the test report, run `coord.py mark-processed`.
Do not ask the user to relay results or confirm continuation.
If no update appears, stay silent and keep waiting; do not send periodic "still waiting" chat messages. If watch prints `SESSION_FINISHED`, stop and do not restart the watch loop.
```

## Observer Codex Prompt

```text
You are Observer Codex for this repository.
Use a lightweight model when available; this role is for user-facing status explanation, not implementation.
Do not edit source files, claim tasks, publish review/test reports, mark tasks processed, commit, push, reset, install dependencies, or direct Main/Developer/Reviewer/Tester.
When the user asks for status, run read-only queries: `coord.py task list`, `coord.py status`, `coord.py open`, `coord.py blockers`, `coord.py show <change>`, `coord.py timeline <change>`, and `coord.py export-html` as needed.
Explain what Main/Coordinator, Developer, Reviewer, and Tester have done, list open blockers, and point to the HTML dashboard when useful.
If the user wants to change task direction, tell them to send that instruction directly to Main/Coordinator Codex.
```

## Event Shape

`events.jsonl` stores one JSON object per line. Event ids are unique, and change/report ids provide cross-event linkage.

```json
{"id":"evt_...","schema":1,"type":"change.created","actor":"main","ts":"2026-05-21T10:00:00+00:00","change_id":"chg_0001","files":["src/example.py"],"summary":"Short implementation summary","verification":["pytest tests/test_example.py"],"risk":"medium"}
```

Important event types:

- `change.created`
- `work.created`
- `work.claimed`
- `work.released`
- `work.completed`
- `change.verified`
- `change.committed`
- `change.pushed`
- `review.completed`
- `test.completed`
- `finding.resolved`
- `report.resolved`
- `task.claimed`
- `task.released`
- `task.completed`
- `session.finished`

`change.created` may include `task_id` when Developer links the implementation to `job_0001`, and `diff_path` when Developer publishes with `--capture-diff`. Reviewers should inspect that snapshot first because Developer may keep editing after the change is published.

## Report Quality Gates

- `report review --decision blocking` requires at least one `--finding`.
- `report test --decision fail` requires at least one `--command` or `--finding`.
- `report test --decision blocked` requires at least one `--untested`.

## Recovery

If Main/Coordinator Codex is interrupted:

1. Run `coord.py task list`, `coord.py status`, `coord.py open`, and `coord.py blockers`; read Markdown ledgers only if more narrative detail is needed.
2. Run `git status --short`.
3. Continue from the latest change id.
4. Re-run verification for any adopted recommendations.
5. After the next code-change cycle, run `coord.py task list`, `coord.py blockers`, `coord.py open`, and inspect `coord.py status` again before proceeding.

If `coord.db` is deleted or stale, run `coord.py rebuild`. The index is derived from `events.jsonl`.

If coordination state may be corrupt, run `coord.py doctor` first. It validates the event log shape, checks that the SQLite index can be opened, and flags event/index count mismatches that need `coord.py rebuild`. Use `coord.py doctor --strict` before publishing this skill or debugging coordination corruption; strict mode also validates event ids, required fields, enum values, and references between changes, reports, findings, and tasks.

## Main Loop Policy

Use this order during active delegated development:

1. Main reads `coord.py task list`, `coord.py blockers`, `coord.py open`, and `coord.py status`.
2. Main routes valid blockers or test failures into Developer work first.
3. If no valid blocker exists, Main creates one small useful Developer task.
4. Developer claims the task, implements it, and runs relevant verification.
5. Developer publishes one change entry with `coord.py change create --task <task_id>`.
6. Developer completes the work item with `coord.py task complete --change <change_id>`.
7. Main reads structured status again, using `coord.py show <change>` or `coord.py timeline <change>` when detail is needed.
8. Commit/push verified work if appropriate.
9. Continue instead of waiting silently.

Missing reports mean “pending review,” not “approved.” They should not stop low/medium-risk verified progress unless the user explicitly asks to wait or the next action is unsafe without review.

If a watcher is interrupted:

1. Run `coord.py open`, then `coord.py next --role <role> --actor <actor>` or `coord.py claim --role <role> --actor <actor>`.
2. Run `coord.py watch --claim` again.
3. Review the latest unhandled change if needed.

## Failure Modes Addressed

- `coord.py` serializes writes with `coord.lock`.
- `events.jsonl` can rebuild `coord.db`.
- `coord.py doctor` detects malformed event lines, broken SQLite state, and event/index drift before handoff.
- `coord.py doctor --strict` catches malformed ids, missing required fields, invalid enum values, and broken cross-event references.
- Unknown changes are rejected for lifecycle and report commands.
- Reviewer/tester work is represented as tasks. Claims use leases so another watcher can pick up expired work.
- A detected change is not considered processed until the secondary terminal explicitly marks it processed.
- If a reviewer/tester crashes after detection but before report write, the pending change remains recoverable through `coord.py next`.

## Practical Limits

This protocol coordinates multiple Codex conversations/sessions, but each secondary conversation must still be started with the role prompt. Once started, it can periodically wait for changes. It is not a system daemon across app restarts unless the user runs it under an external process manager, and it cannot bypass Codex/application interrupts, context limits, system sleep, process exit, or app restarts. Permission/credential/destructive-command risks should be handled by Main's startup preflight, not discovered through repeated phase-boundary confirmations.

A fourth Codex conversation may be used as a read-only Observer for user-facing status. It can run `coord.py status`, `coord.py open`, `coord.py blockers`, `coord.py show`, `coord.py timeline`, and `coord.py export-html`. It should use a lightweight model when available, and it must not claim tasks, publish reports, mark tasks processed, edit source, commit, push, or direct Main/Reviewer/Tester.

## Self-Test

Run the bundled end-to-end test before publishing changes to this skill:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/test_coord.py
```
