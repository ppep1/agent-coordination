---
name: agent-coordination
description: Coordinate Main/Coordinator, Developer, Reviewer, Tester, and optional Observer Codex conversations using a local structured event log, SQLite status index, work leases, diff snapshots, and human-readable Markdown ledgers. Use when the user wants Main to plan and route work while Developer implements and Reviewer/Tester independently review/test changes without idle waiting.
---

# Agent Coordination

Use this skill to run a multi-Codex workflow in one repository. Main/Coordinator Codex plans and assigns work; Developer Codex implements; Reviewer/Tester Codex conversations provide asynchronous safety checks through local structured events.

Read `references/protocol.md` when setting up a repo, recovering state, debugging watcher behavior, or writing role prompts. The README files are for human installation and usage.

## Contract

- **Main/Coordinator Codex** owns planning, preflight review, task decomposition, route decisions, coordination state, and final user communication.
- **Developer Codex** owns source edits, focused verification, change publication, and commit/push when allowed by the task/workflow.
- **Reviewer Codex** performs read-only review for defects, regressions, contract risks, missing tests, security, and concurrency issues.
- **Tester Codex** runs real validation, records untested areas honestly, and never fakes unavailable hardware/vendor/service coverage.
- Reviewer/Tester/Observer do not edit source files, commit, push, reset, delete files, install dependencies, or run broad formatters. Developer may edit only within claimed work.
- Coordination state is local under `.agent-coordination/` and should stay ignored by the target repo.

## Setup

```bash
python3 ~/.codex/skills/agent-coordination/scripts/setup_agent_coordination.py --repo .
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . init
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor --strict
```

Use `coord.py prompt` to generate role prompts for extra Codex conversations:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt main
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt developer --actor developer-a
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt reviewer --actor reviewer-a
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt tester --actor tester-a
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt observer --actor observer-a
```

## Delegated Loop

```text
Main/Coordinator: task create -> task list/status/open/blockers -> route blockers -> wait-final -> finish
Developer: task watch -> implement claimed task -> targeted verification -> change create --task --capture-diff -> task complete -> watch
Reviewer: watch claimed changes -> review report -> mark-processed -> watch
Tester: watch claimed changes -> test report -> mark-processed -> watch
```

Coordinator creates Developer work with:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . task create \
  --title "Short task title" \
  --details "Implementation instructions" \
  --acceptance "Verification and acceptance criteria" \
  --risk medium
```

Developer publishes changes with:

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

Missing reports mean “pending review,” not approval. They do not stop verified low/medium-risk progress. Valid `blocking`, `fail`, or `blocked` reports must be handled before unrelated work or final handoff.

Legacy single-Main implementation mode is still available with `coord.py prompt legacy-main`, but delegated mode is the default for unattended work.

## Reviewer/Tester Loop

Secondary Codex conversations should claim work before acting:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role reviewer --actor reviewer-a --claim --interval 60 --quiet
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role tester --actor tester-a --claim --interval 60 --quiet
```

After review/test, publish one report, then mark the task processed:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . mark-processed --role reviewer --actor reviewer-a --change chg_0001
```

Report quality gates:

- `report review --decision blocking` requires at least one `--finding`.
- `report test --decision fail` requires at least one `--command` or `--finding`.
- `report test --decision blocked` requires at least one `--untested`.

Before Main gives the final user response, it must run `coord.py wait-final` so the last published change gets review/test results. Once final checks and any commit/push are complete, Main runs `coord.py finish` immediately before its final response; Reviewer/Tester stop when `watch` prints `SESSION_FINISHED`.

## Recovery And Inspection

Use these before final handoff or after interruption:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor --strict
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . task list
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . open
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . status
```

Useful detail commands:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . show chg_0001
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . timeline chg_0001
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . export-html
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . rebuild
```

Optional Observer Codex conversations are read-only status explainers for the user. They may run `task list`, `status`, `open`, `blockers`, `show`, `timeline`, and `export-html`; they must not edit source, claim tasks, publish reports, mark tasks processed, commit, push, or direct Main/Developer/Reviewer/Tester.

Resolve handled blockers:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . finding resolve fnd_abc123 --reason "Fixed in chg_0002"
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report resolve rpt_abc123 --reason "Fixed and verified in chg_0002"
```

Run the bundled smoke test when modifying this skill:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/test_coord.py
```
