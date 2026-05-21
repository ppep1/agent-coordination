# Agent Coordination

<p>
  <a href="README.md"><img alt="中文" src="https://img.shields.io/badge/Language-%E4%B8%AD%E6%96%87-lightgrey"></a>
  <a href="README.en.md"><img alt="English" src="https://img.shields.io/badge/Language-English-blue"></a>
</p>

A Codex skill for coordinating Main Codex, Reviewer Codex, and Tester Codex through a local structured event log. It is designed for workflows where you run multiple Codex terminals but do not want to coordinate them through copy/paste, chat state, or manual waiting.

## What Problem It Solves

The old manual workflow often looks like this:

```text
Open three Codex terminals
-> ask all of them to read the skill
-> Main writes code
-> Reviewer reviews manually
-> Tester runs tests manually
-> you coordinate the state yourself
```

That works, but it has real failure modes:

- Main Codex can stall while waiting for Reviewer/Tester feedback.
- Review and test findings can get lost in chat and lose their link to a specific change.
- Multiple agents editing source files creates conflicts and unclear ownership.
- Late test failures or blocking review findings can be missed.
- Recovery after interruption is hard because it is unclear which change was reviewed or tested.

This skill changes the workflow to:

```text
Main Codex: implement + verify + publish change
Reviewer Codex: watch -> read-only review -> report -> mark-processed -> watch
Tester Codex: watch -> run tests -> report -> mark-processed -> watch
Main Codex: status/open/blockers -> fix blockers -> continue
```

## Runtime State Model

After initialization, the target repository gets:

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

- `events.jsonl`: append-only structured event log and source of truth.
- `coord.db`: rebuildable SQLite query index.
- `changes.md`: human-readable change ledger.
- `reviews.md`: human-readable review report ledger.
- `tests.md`: human-readable test report ledger.
- `coord.py`: the preferred structured CLI for locking, event writes, queries, reports, and recovery.

`.agent-coordination/` is runtime state, not the skill itself. Do not commit it to the target repository. The setup script adds it to `.gitignore` by default.

## Install

Clone this skill into your Codex skills directory:

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/ppep1/agent-coordination.git ~/.codex/skills/agent-coordination
```

If already installed:

```bash
cd ~/.codex/skills/agent-coordination
git pull
```

Open a new Codex session after installation.

## Enable It In A Project

Enter the target repository:

```bash
cd /path/to/your/repo
```

Initialize coordination:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/setup_agent_coordination.py --repo .
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . init
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor
```

Check current state:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . status
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . open
```

Initially there should usually be no changes and no blockers.

## How To Run The Three Terminals

You still open three Codex terminals. The difference is that each terminal now has a clear role and uses `coord.py` to exchange state.

### Terminal 1: Main Codex

Main Codex owns source edits, targeted verification, change publication, blocker handling, commits, and pushes.

Use this prompt for Main Codex:

```text
You are Main Codex. Use the agent-coordination skill.

In this repository:
1. You own source edits, git state, verification, commits/pushes, and final user communication.
2. Before each implementation increment, run:
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . open
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . status
3. After each small increment, run targeted verification, then publish a change:
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change create --capture-diff --file <file> --summary "<summary>" --verify "<command>" --risk medium
4. Do not wait for fresh reviewer/tester reports before continuing verified low/medium-risk increments.
5. If blockers/fail/blocked appears, fix it before unrelated work.
6. After fixing a blocker, close handled findings and reports with finding resolve and report resolve.
7. Before final handoff, run:
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
   git status --short
```

You can also generate the latest prompt:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt main
```

After Main finishes a small change, publish it:

```bash
pytest tests/test_parser.py

python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change create \
  --capture-diff \
  --file src/parser.py \
  --summary "Add empty input validation" \
  --verify "pytest tests/test_parser.py" \
  --risk medium
```

The command prints a change id:

```text
chg_0001
```

Reviewer and Tester reports should reference that change id.

### Terminal 2: Reviewer Codex

Reviewer Codex reads source files but does not edit them by default.

Use this prompt for Reviewer Codex:

```text
You are Reviewer Codex. Use the agent-coordination skill.

Rules:
1. Do not edit source files, commit, push, reset, install dependencies, or run broad formatters.
2. Perform read-only code review only.
3. Wait for and claim new changes:
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role reviewer --actor reviewer-a --claim --interval 60
4. When a change appears, read change_id, files, verification, risk, and diff_path from the output.
5. Review the diff snapshot first when present, then touched files and relevant contracts. Report only real defects, regressions, compatibility risks, missing tests, security issues, or concurrency issues.
6. Publish a report:
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report review --actor reviewer-a --change <change_id> --decision pass|concerns|blocking --files-read <file> --finding "severity:file:line:message"
7. After reporting, mark processed; this completes the claimed task:
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . mark-processed --role reviewer --actor reviewer-a --change <change_id>
8. Then watch again.
```

You can also generate the latest prompt:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt reviewer --actor reviewer-a
```

Start watching:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role reviewer --actor reviewer-a --claim --interval 60
```

When Main publishes a change, Reviewer sees:

```json
{
  "seq": 1,
  "change_id": "chg_0001",
  "summary": "Add empty input validation",
  "files": [
    "src/parser.py"
  ],
  "verification": [
    "pytest tests/test_parser.py"
  ],
  "risk": "medium"
}
```

If Reviewer finds a non-blocking concern:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report review \
  --actor reviewer-a \
  --change chg_0001 \
  --decision concerns \
  --files-read src/parser.py \
  --finding "medium:src/parser.py:42:Missing whitespace-only input case"

python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . mark-processed \
  --role reviewer \
  --actor reviewer-a \
  --change chg_0001
```

If Reviewer finds no material issue:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report review \
  --actor reviewer-a \
  --change chg_0001 \
  --decision pass \
  --files-read src/parser.py

python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . mark-processed \
  --role reviewer \
  --actor reviewer-a \
  --change chg_0001
```

If Reviewer finds an issue Main must fix first:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report review \
  --actor reviewer-a \
  --change chg_0001 \
  --decision blocking \
  --files-read src/parser.py \
  --finding "high:src/parser.py:42:Empty input crashes the parser"
```

### Terminal 3: Tester Codex

Tester Codex runs real validation and records what was not covered.

Use this prompt for Tester Codex:

```text
You are Tester Codex. Use the agent-coordination skill.

Rules:
1. Do not edit source files, commit, push, reset, install dependencies, or run destructive commands.
2. Do not fake hardware, vendor SDK, or external-service coverage.
3. Wait for and claim new changes:
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role tester --actor tester-a --claim --interval 60
4. When a change appears, run the listed verification command first when safe, then add focused tests based on touched files.
5. Publish a test report:
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report test --actor tester-a --change <change_id> --decision pass|fail|blocked --command "<command>" --untested "<reason>"
6. After reporting, mark processed; this completes the claimed task:
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . mark-processed --role tester --actor tester-a --change <change_id>
7. Then watch again.
```

Start watching:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role tester --actor tester-a --claim --interval 60
```

If tests pass:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report test \
  --actor tester-a \
  --change chg_0001 \
  --decision pass \
  --command "pytest tests/test_parser.py"

python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . mark-processed \
  --role tester \
  --actor tester-a \
  --change chg_0001
```

If tests fail:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report test \
  --actor tester-a \
  --change chg_0001 \
  --decision fail \
  --command "pytest tests/test_parser.py" \
  --finding "high:tests/test_parser.py:18:Parser regression test fails"
```

If dependencies or external services are unavailable:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report test \
  --actor tester-a \
  --change chg_0001 \
  --decision blocked \
  --command "pytest tests/test_parser.py" \
  --untested "pytest is not installed in this environment"
```

## How Main Handles Feedback

Main can check state at any time:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . status
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . open
```

If blockers show:

```text
rpt_abc123 chg_0001 reviewer:blocking actor=reviewer-a
fnd_def456 chg_0001 high src/parser.py:42 Empty input crashes the parser
```

Main fixes the issue and publishes a follow-up change:

```bash
pytest tests/test_parser.py

python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change create \
  --capture-diff \
  --file src/parser.py \
  --file tests/test_parser.py \
  --summary "Handle empty and whitespace-only parser input" \
  --verify "pytest tests/test_parser.py" \
  --risk low
```

Then Main closes the old finding and report:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . finding resolve fnd_def456 --reason "Fixed in chg_0002"
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report resolve rpt_abc123 --reason "Fixed and verified in chg_0002"
```

Confirm blockers are gone:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
```

Expected output:

```text
No open blockers.
```

To share the current coordination state, export the HTML dashboard:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . export-html
```

The default output is `.agent-coordination/reports/status.html`.

## Recommended Daily Loop

```text
Main:
  blockers/open/status -> implement one small increment -> targeted verification -> change create -> blockers/open/status -> continue

Reviewer:
  watch -> review -> report review -> mark-processed -> watch

Tester:
  watch -> run tests -> report test -> mark-processed -> watch
```

Main does not need to wait for fresh review/test reports after every change. However, once `blockers` reports a valid issue, Main should handle it before unrelated work.

## Command Cheat Sheet

```bash
# Initialize / repair
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . init
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . rebuild
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor

# Query
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . status
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . open
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . next --role tester --actor tester-a
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . show chg_0001
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . timeline chg_0001

# Task claim / lease
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . claim --role reviewer --actor reviewer-a --ttl 900
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . release --role reviewer --actor reviewer-a --change chg_0001

# Change lifecycle
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change create --capture-diff --file src/a.py --summary "..." --verify "pytest ..."
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change verify chg_0001
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change commit chg_0001
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change push chg_0001

# Reports
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report review --change chg_0001 --decision pass
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report test --change chg_0001 --decision pass --command "pytest"

# Prompts and dashboard
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt main
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt reviewer --actor reviewer-a
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . export-html

# Resolve handled issues
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . finding resolve fnd_abc123 --reason "Fixed in chg_0002"
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report resolve rpt_abc123 --reason "Fixed and verified in chg_0002"
```

## Run Self-Test

The skill includes an end-to-end test script. It uses a temporary directory and validates init, change creation, diff snapshots, claim/lease, report quality gates, reports, mark-processed, show/open/timeline, prompt, export-html, resolve, and rebuild:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/test_coord.py
```

## Compatibility

`scripts/watch_changes.py` remains available for older Markdown-only workflows. New workflows should prefer `scripts/coord.py` because it provides structured events, file locking, a SQLite status index, and clearer recovery behavior.

## Limits

- This is not a daemon or cloud orchestration platform.
- Reviewer and Tester terminals still need to be started with role-specific prompts.
- Task leases are local coordination-state locks, not distributed locks across machines.
- File locking currently uses Unix `fcntl`, so macOS/Linux are the intended environments.
