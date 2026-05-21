# Agent Coordination

<p>
  <a href="README.md"><img alt="中文" src="https://img.shields.io/badge/Language-%E4%B8%AD%E6%96%87-lightgrey"></a>
  <a href="README.en.md"><img alt="English" src="https://img.shields.io/badge/Language-English-blue"></a>
</p>

A Codex skill for coordinating Main Codex, Reviewer Codex, and Tester Codex through a local structured event log. It is designed for workflows where you run multiple Codex conversations/sessions in the same project but do not want to coordinate them through copy/paste, chat state, or manual waiting.

## How This Differs From Built-In Codex Subagents

Codex's built-in subagent system is already a good fit for many parallel tasks: Main Codex can delegate a specific, bounded task to an explorer or worker subagent, wait for the result, and integrate it. This skill does not replace built-in subagents. It covers a different workflow: multiple independent Codex terminals collaborating around the same repository over time, with recoverable, auditable, queryable state.

| Capability | Built-in Codex subagents | Agent Coordination skill |
| --- | --- | --- |
| Typical use | One-shot or short-lived delegation: inspect code, implement a local change, parallelize well-scoped subtasks | Long-running collaboration: Main keeps developing while Reviewer/Tester independently watch, claim, and report |
| Lifecycle | Created, waited on, and integrated by Main Codex inside the current session | Runs across separate Codex conversations/sessions, with state stored in the target repo's `.agent-coordination/` directory |
| State record | Mostly lives in conversation context and subagent final messages | `events.jsonl` is the source of truth, `coord.db` is queryable, Markdown ledgers are readable |
| Recovery | Works well inside the current task; after session/context changes it depends on Main's memory or summaries | Recover with `status/open/blockers/timeline/doctor --strict` |
| Review/test ownership | Can be delegated, but results are usually received once by Main Codex | Reviewer/Tester have stable roles, claim/lease, report quality gates, and processed state |
| Duplicate work control | Main Codex coordinates to avoid duplicate delegation | `claim --ttl` and leases prevent multiple Reviewer/Tester terminals from processing the same change |
| Audit and handoff | Depends on chat history | Changes, reviews, tests, and resolves are structured events and can be exported to HTML |

In short: built-in subagents are closer to parallel worker threads owned by Main Codex; this skill is a local, file-backed collaboration protocol. If you only need one subagent to inspect code or patch a small module, use built-in subagents. If you want Main/Reviewer/Tester Codex conversations to keep working over time without losing state, this skill is the better fit.

## What Problem It Solves

The old manual workflow often looks like this:

```text
Open three Codex conversations/sessions
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

Initialize the coordination directory in the target repository, then start the multi-Codex workflow. Concrete commands are grouped later in the “Command Cheat Sheet”.

First-time setup has three steps:

1. Enter the target repository.
2. Initialize `.agent-coordination/` and run `doctor --strict`.
3. Generate role prompts for Main, Reviewer, and Tester. Generate an Observer prompt too if needed.

Initially there should usually be no changes and no blockers.

## How To Run The Four Codex Conversations

The core execution roles are three Codex conversations: Main, Reviewer, and Tester. The fourth Observer Codex is optional and only talks with the user about status.

All conversations are normal Codex chat interfaces. The difference is their role prompt. They exchange state through the same `.agent-coordination/` directory instead of relying on you to copy/paste results between chats.

The intended workflow is unattended after startup: Main Codex first clarifies the task and route, then performs one preflight review before execution. It should identify anything that needs a human decision: missing permissions or external services, destructive-risk commands, purchases/logins/credentials, or conflicting requirements. After you approve the route and how to handle those risks, Main keeps going until final delivery and should not stop at every roadmap phase boundary to report progress or ask whether to continue.

Normal code edits, reading project files, running local tests, running non-destructive build/format checks, committing, and pushing should not trigger mid-run confirmation after the user has authorized delivery. The limits this skill cannot solve are runtime limits: Codex/app interrupts, context limits, system sleep, process exit, and app restarts.

### Conversation 1: Main Codex

Main Codex owns implementation, verification, change publication, blocker handling, commits/pushes, and final user delivery.

How to use it:

1. Open the first Codex conversation in the target repository.
2. Generate the Main prompt, copy the output, and paste it into that conversation.
3. Send your task to Main and let it perform the preflight review. After you approve the start, Main keeps going.

Main's key rules:

- Perform one preflight review before starting; ask the user first if there are permission, credential, destructive-command, or requirement-conflict risks.
- After the user approves the start, do not stop at phase boundaries to ask whether to continue; publish progress to coordination state and keep going.
- Verify and publish a change after each meaningful small increment.
- Do not wait for fresh reviewer/tester reports before continuing verified low/medium-risk increments.
- Once a valid `blocking`, `fail`, or `blocked` appears, handle it before unrelated work.
- If the user only asks for status, briefly report `status/open/blockers` and continue. Interrupt the unattended workflow only when the user explicitly asks to pause, stop, wait for confirmation, change direction, or not commit.

### Conversation 2: Reviewer Codex

Reviewer Codex is a read-only review role. After receiving its role prompt, it enters a watch loop: claim a change, review it, write a review report, mark processed, and keep watching.

How to use it:

1. Open the second Codex conversation in the same target repository.
2. Generate the Reviewer prompt, copy the output, and paste it into that conversation.
3. Do not give Reviewer a separate task. It will enter the watch loop from the prompt, then automatically claim, review, report, and mark processed after Main publishes a change.

Reviewer responsibility boundaries:

- Do not edit source, commit/push/reset, install dependencies, or run broad formatters.
- Report only real defects, regressions, compatibility risks, missing tests, security issues, or concurrency issues.
- Do not ask the user to relay results or confirm continuation; write reports to coordination state.

### Conversation 3: Tester Codex

Tester Codex performs real validation. After receiving its role prompt, it enters a watch loop: claim a change, run verification, write a test report, mark processed, and keep watching.

How to use it:

1. Open the third Codex conversation in the same target repository.
2. Generate the Tester prompt, copy the output, and paste it into that conversation.
3. Do not give Tester a separate task. It will enter the watch loop from the prompt, then automatically claim, verify, report, and mark processed after Main publishes a change.

Tester responsibility boundaries:

- Do not edit source, commit/push/reset, install dependencies, or run destructive commands.
- Run the verification commands published by Main first, then add focused tests based on touched files.
- Do not fake hardware, vendor SDK, or external-service coverage; record untested or blocked coverage honestly.
- Do not ask the user to relay results or confirm continuation; write reports to coordination state.

### Optional Conversation 4: Observer Codex

Observer Codex is an optional user-facing status role. Use a lightweight model when available, such as GPT-5.4-Mini. It does not automatically run project tasks; it only reads coordination state and explains it to you.

How to use it:

1. Open the fourth Codex conversation in the same target repository. Use a lightweight model if available.
2. Generate the Observer prompt, copy the output, and paste it into that conversation.
3. Ask Observer status questions such as “What has happened?”, “Are there blockers?”, or “What did Reviewer/Tester do?”.

Observer responsibility boundaries:

- It can answer what has happened, whether blockers exist, what Reviewer/Tester did, what the next change is, and where the HTML dashboard is.
- It may only run read-only queries: `status`, `open`, `blockers`, `show`, `timeline`, and `export-html`.
- It must not claim tasks, write review/test reports, mark processed, edit source, commit, or push.
- It must not direct Main to change course. If you want to change task direction, send that instruction directly to Main Codex.

You can also skip Observer and use a regular shell terminal for read-only queries. The difference is that Observer explains the status in natural language.

## How Main Handles Feedback

Main does not need to wait for fresh review/test reports after every change, but it should check `blockers/open/status` after each implementation cycle.

If Reviewer or Tester writes a valid blocker, Main should fix it first, publish a new change, and resolve handled findings/reports. If a late blocker arrives after commit/push, fix it in a follow-up change.

Missing reports mean pending review/test, not approval. For low/medium-risk increments that have passed local verification, Main can keep moving. For high-risk work, Main should be more conservative and actively inspect state.

## How To Observe Current State

Use Observer Codex or a regular shell terminal to observe state without interrupting the Main/Reviewer/Tester execution loops.

Common observation entry points:

- `status`: overall state.
- `open`: changes still pending review/test.
- `blockers`: current blocking issues.
- `show <change>`: details for one change.
- `timeline <change>`: event timeline for one change.
- `export-html`: export the HTML status dashboard.

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
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --version
python3 ~/.codex/skills/agent-coordination/scripts/setup_agent_coordination.py --repo .
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . init
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . rebuild
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor --strict

# Query
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . status
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . open
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . next --role tester --actor tester-a
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . show chg_0001
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . timeline chg_0001
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . export-html

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

# Prompts
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt main
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt reviewer --actor reviewer-a
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt tester --actor tester-a
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt observer --actor observer-a

# Copy each prompt into its Main / Reviewer / Tester / Observer Codex conversation

# Resolve handled issues
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . finding resolve fnd_abc123 --reason "Fixed in chg_0002"
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report resolve rpt_abc123 --reason "Fixed and verified in chg_0002"
```

## Run Self-Test

The skill includes an end-to-end test script. It uses a temporary directory and validates init, change creation, diff snapshots, claim/lease, report quality gates, reports, mark-processed, show/open/timeline, prompt, export-html, resolve, and rebuild:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/test_coord.py
```

GitHub Actions runs the same baseline checks: `py_compile` and `scripts/test_coord.py`.

## Compatibility

`scripts/watch_changes.py` remains available for older Markdown-only workflows. New workflows should prefer `scripts/coord.py` because it provides structured events, file locking, a SQLite status index, and clearer recovery behavior.

## Limits

- This is not a daemon or cloud orchestration platform; it cannot bypass Codex/app interrupts, context limits, system sleep, process exits, or app restarts.
- Permissions, destructive-risk commands, external logins/credentials, and conflicting requirements should be found in Main's startup preflight and asked about once; normal code edits, local tests, commits, and pushes should not interrupt every phase.
- Reviewer and Tester Codex conversations/sessions still need to be started with role-specific prompts; Observer is an optional fourth Codex conversation.
- Task leases are local coordination-state locks, not distributed locks across machines.
- File locking currently uses Unix `fcntl`, so macOS/Linux are the intended environments.
