---
name: agent-coordination
description: Coordinate a primary implementation Codex with reviewer/tester Codex watchers using a local structured event log, SQLite status index, task leases, diff snapshots, and Markdown compatibility ledgers. Use when the user wants Main Codex to keep implementing, testing, committing, and pushing without idle waiting while secondary terminals independently review/test changes and report blockers through local files.
---

# Agent Coordination

Use this skill to run a multi-Codex workflow in one repository. Main Codex keeps implementation moving; Reviewer/Tester Codex terminals provide asynchronous safety checks through local structured events.

Read `references/protocol.md` when setting up a repo, recovering state, debugging watcher behavior, or writing role prompts. The README files are for human installation and usage.

## Contract

- **Main Codex** owns source edits, git state, verification, commits/pushes, and user communication.
- **Reviewer Codex** performs read-only review for defects, regressions, contract risks, missing tests, security, and concurrency issues.
- **Tester Codex** runs real validation, records untested areas honestly, and never fakes unavailable hardware/vendor/service coverage.
- Secondary agents do not edit source files, commit, push, reset, delete files, install dependencies, or run broad formatters unless explicitly granted a narrow write scope.
- Coordination state is local under `.agent-coordination/` and should stay ignored by the target repo.

## Setup

```bash
python3 ~/.codex/skills/agent-coordination/scripts/setup_agent_coordination.py --repo .
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . init
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor --strict
```

Use `coord.py prompt` to generate role prompts for extra terminals:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt main
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt reviewer --actor reviewer-a
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt tester --actor tester-a
```

## Main Loop

```text
coord blockers/open/status
-> fix valid blockers first
-> implement one small increment
-> run targeted verification
-> coord change create --capture-diff
-> coord blockers/open/status
-> commit/push when appropriate
-> continue without waiting silently for fresh reports
```

Publish changes with:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change create \
  --capture-diff \
  --file src/example.py \
  --summary "Short implementation summary" \
  --verify "pytest tests/test_example.py" \
  --risk medium
```

Missing reports mean “pending review,” not approval. They do not stop verified low/medium-risk progress. Valid `blocking`, `fail`, or `blocked` reports must be handled before unrelated work or final handoff.

## Reviewer/Tester Loop

Secondary terminals should claim work before acting:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role reviewer --actor reviewer-a --claim --interval 60
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role tester --actor tester-a --claim --interval 60
```

After review/test, publish one report, then mark the task processed:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . mark-processed --role reviewer --actor reviewer-a --change chg_0001
```

Report quality gates:

- `report review --decision blocking` requires at least one `--finding`.
- `report test --decision fail` requires at least one `--command` or `--finding`.
- `report test --decision blocked` requires at least one `--untested`.

## Recovery And Inspection

Use these before final handoff or after interruption:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor --strict
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

Resolve handled blockers:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . finding resolve fnd_abc123 --reason "Fixed in chg_0002"
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report resolve rpt_abc123 --reason "Fixed and verified in chg_0002"
```

Run the bundled smoke test when modifying this skill:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/test_coord.py
```
