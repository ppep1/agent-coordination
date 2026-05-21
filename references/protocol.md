# Multi-Codex Coordination Protocol

## Core Idea

Use a local, ignored coordination directory as a structured append-only event log with Markdown compatibility ledgers. Main Codex publishes changes and keeps moving on verified small increments. Other Codex terminals periodically check for change events and respond with review/test reports.

This avoids manual copy/paste between agents while keeping all work recoverable after interruptions.

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
  status/
  templates/
```

`events.jsonl` is the source of truth. `coord.db` is a rebuildable SQLite status index. `changes.md`, `reviews.md`, and `tests.md` are append-only Markdown compatibility ledgers. `status/*.json` is retained for older `watch_changes.py` workflows.

Order is still part of the Markdown compatibility contract: the newest change entry must be the last `## Change ...` heading in `changes.md`.

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
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . open
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . show chg_0001
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . timeline chg_0001
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . export-html
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt main
```

Publish a change:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change create \
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

## Main Codex Prompt

```text
You are Main Codex for this repository.
Own implementation, git state, verification, commits/pushes, and final user communication.
Before a new increment, run `coord.py blockers`, `coord.py open`, and `coord.py status`; fix valid blockers first.
Keep increments small. After each meaningful change, run targeted verification and publish it with `coord.py change create`.
After every code-change cycle, run `coord.py blockers` and inspect `coord.py status`, even if no secondary agent has announced completion.
Resolve valid blocking findings from review/test reports before unrelated work or final handoff.
Do not treat missing secondary reports as approval, but do not idle by default: if no new blocking report exists, continue the next verified small increment or commit/push when the user has asked for continuous delivery.
If a late report finds a valid blocker after a commit/push, fix it in a follow-up change.
Before final response, run `coord.py doctor`, `coord.py blockers`, and `git status --short`.
```

## Reviewer Codex Prompt

```text
You are Reviewer Codex for this repository.
Do not edit source files, commit, push, reset, delete files, install dependencies, or run broad formatters.
Watch and claim structured changes with `coord.py watch --role reviewer --actor reviewer-a --claim`.
When a new change appears, inspect the touched files and relevant contracts only.
Report concrete bugs, regressions, missing tests, compatibility risks, and unsafe assumptions. Avoid style-only findings unless they hide a real defect.
Publish findings with `coord.py report review`; include `--files-read` for inspected files and `--finding` for material findings.
Use `pass` only when no material issue remains, `concerns` for non-blocking risks, and `blocking` for issues Main must fix before unrelated work or final handoff.
After writing the review, run `coord.py mark-processed`.
If no update appears, stay silent and keep waiting.
```

## Tester Codex Prompt

```text
You are Tester Codex for this repository.
Do not edit source files unless a task explicitly allows test fixture/report updates.
Do not commit, push, reset, delete files, install dependencies, run destructive commands, or fake unavailable hardware/vendor coverage.
Watch and claim structured changes with `coord.py watch --role tester --actor tester-a --claim`.
When a new change appears, run the listed verification commands when safe, then add focused checks based on touched files.
Publish results with `coord.py report test`; include each command with `--command`, use `--untested` for anything not actually covered, and add `--finding` for material failures.
Use `pass` only for commands that actually passed, `fail` for real failures, and `blocked` for missing dependencies, unavailable services, or unsafe commands.
After writing the test report, run `coord.py mark-processed`.
If no update appears, stay silent and keep waiting.
```

## Event Shape

`events.jsonl` stores one JSON object per line. Event ids are unique, and change/report ids provide cross-event linkage.

```json
{"id":"evt_...","schema":1,"type":"change.created","actor":"main","ts":"2026-05-21T10:00:00+00:00","change_id":"chg_0001","files":["src/example.py"],"summary":"Short implementation summary","verification":["pytest tests/test_example.py"],"risk":"medium"}
```

Important event types:

- `change.created`
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

`change.created` may include `diff_path` when Main publishes with `--capture-diff`. Reviewers should inspect that snapshot first because Main may keep editing after the change is published.

## Report Quality Gates

- `report review --decision blocking` requires at least one `--finding`.
- `report test --decision fail` requires at least one `--command` or `--finding`.
- `report test --decision blocked` requires at least one `--untested`.

## Recovery

If Main Codex is interrupted:

1. Run `coord.py status`, `coord.py open`, and `coord.py blockers`; read Markdown ledgers only if more narrative detail is needed.
2. Run `git status --short`.
3. Continue from the latest change id.
4. Re-run verification for any adopted recommendations.
5. After the next code-change cycle, run `coord.py blockers`, `coord.py open`, and inspect `coord.py status` again before proceeding.

If `coord.db` is deleted or stale, run `coord.py rebuild`. The index is derived from `events.jsonl`.

If coordination state may be corrupt, run `coord.py doctor` first. It validates the event log shape, checks that the SQLite index can be opened, and flags event/index count mismatches that need `coord.py rebuild`. Use `coord.py doctor --strict` before publishing this skill or debugging coordination corruption; strict mode also validates event ids, required fields, enum values, and references between changes, reports, findings, and tasks.

## Main Loop Policy

Use this order during active development:

1. Read `coord.py blockers`, `coord.py open`, and `coord.py status`.
2. Fix valid blockers or test failures.
3. If no valid blocker exists, implement one small useful increment.
4. Run relevant verification.
5. Publish one change entry with `coord.py change create`.
6. Read structured status again, using `coord.py show <change>` or `coord.py timeline <change>` when detail is needed.
7. Commit/push verified work if appropriate.
8. Continue instead of waiting silently.

Missing reports mean “pending review,” not “approved.” They should not stop low/medium-risk verified progress unless the user explicitly asks to wait or the next action is unsafe without review.

When appending Markdown manually, write at EOF only. Do not patch against repeated text such as `Open Questions: - none`, because that can insert a newer change into the middle of the ledger and leave older watchers stuck on an older final heading.

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
- Empty `changes.md` baseline does not trigger a fake review cycle in legacy mode.
- If a reviewer/tester crashes after detection but before report write, the pending change remains recoverable through `coord.py next`.

## Practical Limits

This protocol coordinates multiple Codex terminals, but each secondary terminal must still be started with the role prompt. Once started, it can periodically wait for changes. It is not a system daemon across app restarts unless the user runs it under an external process manager.

## Self-Test

Run the bundled end-to-end test before publishing changes to this skill:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/test_coord.py
```
