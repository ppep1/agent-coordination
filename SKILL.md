---
name: agent-coordination
description: Coordinate a primary implementation Codex with reviewer/tester Codex watchers using a local structured event log, SQLite status index, and Markdown compatibility ledgers. Use when the user wants Main Codex to keep implementing, testing, committing, and pushing without idle waiting while secondary terminals independently review/test changes and report blockers through local files.
---

# Agent Coordination

Use this skill to run a multi-Codex workflow in one repository. Main Codex keeps implementation moving; reviewer/tester watchers provide asynchronous safety checks through local structured events. Markdown ledgers are still written for readability and compatibility with the older watcher.

Load `references/protocol.md` when setting up a new repo, recovering a broken watcher, debugging coordination state, or writing role prompts for secondary terminals.

## Roles

- **Main Codex** owns implementation, git state, final decisions, and user communication.
- **Reviewer Codex** watches change events, performs read-only review for bugs/regressions/contract risks, and publishes review reports.
- **Tester Codex** watches the same events, runs scoped verification, records untested areas honestly, and publishes test reports.
- Extra Codex terminals may be specialized as docs, risk, compatibility, or old-version comparison agents.

Only Main Codex edits source files by default. Other Codex terminals may edit only their assigned report/status files unless a task explicitly grants a narrow write scope.

## Setup

Initialize the local coordination directory:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/setup_agent_coordination.py --repo .
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . init
```

This creates:

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
    main-codex-prompt.md
    reviewer-codex-prompt.md
    tester-codex-prompt.md
```

The setup script adds `.agent-coordination/` to `.gitignore` by default. Coordination state is local and should not be uploaded unless the user explicitly asks for it.

`events.jsonl` is the append-only source of truth. `coord.db` is a rebuildable SQLite query index. `changes.md`, `reviews.md`, and `tests.md` are compatibility ledgers for humans and older watchers.

## Main Codex Loop

After each meaningful implementation step:

1. Run `coord.py blockers`, `coord.py open`, and `coord.py status`; fix valid blockers first.
2. Implement one small, coherent increment.
3. Run targeted verification.
4. Publish a structured change event with `coord.py change create`; this also appends `.agent-coordination/changes.md`.
5. Include files touched, summary, verification, risk, and any open questions in the summary if needed.
6. Immediately run `coord.py blockers` and inspect `coord.py status`.
7. If no new review/test entry exists for the latest change, do **not** idle by default. Continue the next small useful increment, or commit/push the verified change when the user has asked for continuous delivery.
8. If a later report arrives with a valid blocker or test failure, pause the current line of work, fix it, verify, publish a new change entry, then continue.

This is the preferred steady-state policy:

- **No report yet**: treat as pending review, not approval, but keep moving on low/medium-risk verified increments.
- **Pass report**: continue.
- **Concerns report**: fix if valid and material; otherwise record why it is non-blocking in the next change entry or user update.
- **Blocking/fail report**: fix before starting unrelated work or before final handoff.
- **Do not silently wait** unless the user explicitly asks to wait or the next action is unsafe without review.

Use this command shape:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change create \
  --file TargetDetection_stack.py \
  --summary "Short description of the implementation increment" \
  --verify "pytest tests/test_target_detection.py" \
  --risk medium
```

The command prints a structured change id such as `chg_0003`. Record lifecycle events when useful:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change verify chg_0003
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change commit chg_0003
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change push chg_0003
```

If `coord.db` looks stale or is deleted, rebuild it from the event log:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . rebuild
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor
```

Inspect active work and history:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . open
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . show chg_0003
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . timeline chg_0003
```

Compatibility Markdown entries must still be appended at EOF. Do not insert entries by patching against repeated text such as `Open Questions: - none`; older watchers determine the newest change from the last `## Change ...` heading in the file.

## Reviewer/Tester Loop

Secondary Codex terminals should run a wait command, then act only when a structured change appears:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role reviewer --actor reviewer-a --claim --interval 60
```

When the watcher reports a new change:

1. Read the reported change id, files, verification, and risk from `coord.py watch`; read `.agent-coordination/changes.md` only if useful.
2. Perform the assigned review/test.
3. Publish results with `coord.py report review` or `coord.py report test`; this also appends Markdown reports.
4. Mark that change as processed; this completes the claimed task:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . mark-processed --role reviewer --actor reviewer-a --change chg_0003
```

5. Start watching again.

If no change is detected, stay silent.

### Tester Loop Details

Tester Codex is responsible for useful validation, not just echoing the change entry.

1. Watch and claim structured `change.created` events with `coord.py watch --claim`.
2. When a new change appears, read that exact change entry before testing.
3. Run the verification commands listed in the change when they are safe.
4. Add scoped tests based on the files touched. For code, tests, build, or runtime entrypoint changes, prefer focused tests plus full-suite validation when feasible.
5. Publish one test report with `coord.py report test`.
6. Mark processed only after the report command succeeds.
7. Resume watching from the processed change.

Use role `tester` for watcher commands:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role tester --actor tester-a --claim --interval 60
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . mark-processed --role tester --actor tester-a --change chg_0003
```

Use explicit claim/release when a watcher needs manual control:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . claim --role reviewer --actor reviewer-a --ttl 900
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . release --role reviewer --actor reviewer-a --change chg_0003
```

The legacy `watch_changes.py` commands remain available for Markdown-only workflows. Prefer `coord.py` for new workflows because it uses structured events, file locking, and a queryable status index.

Tester validation guidelines:

- **Docs-only changes**: run `git diff --check` unless the ledger requests more.
- **Python code or tests**: run focused pytest for touched tests/modules, then full pytest when practical.
- **Build or CLI entrypoint changes**: run the changed command path, a no-hardware smoke test, and full pytest when practical.
- **Config/package changes**: run syntax/parse validation, smoke checks, and full pytest when practical.
- **Unavailable dependencies**: do not install them by default; record the blocked command and reason under `Untested`.
- **Hardware/vendor SDK flows**: do not fake coverage. Record them under `Untested` unless the environment actually supports them.

Reviewer validation guidelines:

- Focus on defects, behavioral regressions, API/contract mismatches, missing tests for risky paths, data loss, security, concurrency, and compatibility.
- Do not report style-only nits unless they hide a concrete maintainability or correctness risk.
- Use `pass` only when no material issue remains, `concerns` for non-blocking risks, and `blocking` for issues Main must fix before unrelated work or final handoff.
- Include `--files-read` for inspected files and `--finding "severity:file:line:message"` for material findings.

## Continuous Delivery Pattern

When the user wants active progress instead of manual checkpoints, Main Codex should use this loop:

```text
coord blockers/open/status -> fix valid blockers -> implement one small increment
-> run targeted verification -> coord change create -> coord blockers/open/status
-> commit locally if verified and no known blocker -> push when network/policy allows
-> continue next increment without waiting for fresh reports
```

Rules learned from long-running delivery work:

- **Never idle just because reviewer/tester reports are not fresh.** Missing reports mean pending review, not a stop signal. If there is no new blocker, continue the next useful increment or commit/push the verified work.
- **Main still runs targeted verification.** Tester may own broad/full-suite validation, but Main must run the narrow checks that prove the current increment is not obviously broken, such as compile, smoke, diff checks, or focused tests.
- **Commit after each verified small increment.** Keep commits narrow and recoverable. If the user has enabled continuous delivery and the network is available, push after the commit; otherwise keep local commits moving.
- **Late reports are handled retroactively.** If reviewer/tester later reports a valid blocker for an already committed or pushed change, fix it in a new small increment, append a new ledger entry, verify, commit, and continue.
- **Do not batch unrelated work while reports are pending.** Progress should be steady, but increments must stay small enough that a late finding can be applied cleanly.
- **When stopping a task, clean up watchers.** If the user asks to stop, remove this repo's cron/launch/watch processes where possible, confirm `git status`, and leave a short handoff or memory if requested.

## Report Rules

Reviewer reports should lead with findings. Prefer the structured CLI:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report review \
  --actor reviewer-a \
  --change chg_0003 \
  --decision concerns \
  --files-read src/file.py \
  --finding "medium:src/file.py:42:Missing empty input case"
```

Tester reports should state what ran and what remains untested. Prefer the structured CLI:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report test \
  --actor tester-a \
  --change chg_0003 \
  --decision pass \
  --command "pytest tests/test_file.py"
```

Markdown compatibility reports are still appended to `reviews.md` and `tests.md` by the CLI.

Main Codex can resolve a handled finding:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . finding resolve fnd_abc123 --reason "Fixed in chg_0004"
```

If the original blocking/fail report has been handled by a later change, resolve that report too:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report resolve rpt_abc123 --reason "Fixed and verified in chg_0004"
```

## Safety

- Do not let secondary Codex terminals edit source files by default.
- Do not let secondary terminals run broad formatters, commits, pushes, resets, or deletes.
- Use one source owner per file set.
- Keep coordination files append-only where possible.
- Write coordination state through `coord.py` for new workflows; it serializes writes with `.agent-coordination/coord.lock`.
- Treat `events.jsonl` as the source of truth and `coord.db` as rebuildable. If the index looks stale, run `coord.py rebuild`.
- Use `coord.py doctor` after setup, recovery, or before final handoff when coordination state mattered; it flags malformed events, broken SQLite state, and event/index drift.
- Changes ledger order matters for legacy watchers: the newest change must be physically last in `.agent-coordination/changes.md`.
- Treat missing review/test reports as “not reviewed yet,” not as approval; however, Main Codex may continue verified low/medium-risk work instead of waiting.
- Main Codex must proactively inspect `coord.py blockers` and `coord.py status` after every code-change cycle, even if no secondary agent has announced completion.
- Main Codex must revisit and handle valid late reports, even if the related change was already committed or pushed.
- Secondary Codex terminals must mark a detected change as processed only after they append their report.
- Main Codex must still inspect `git status --short` before final response.

See `references/protocol.md` for detailed templates and recovery rules.

For a local end-to-end smoke test of the coordination CLI, run:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/test_coord.py
```
