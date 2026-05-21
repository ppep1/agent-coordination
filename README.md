# Agent Coordination

中文 | [English](#english)

一个 Codex skill，用本地结构化事件日志协调 Main Codex、Reviewer Codex 和 Tester Codex。目标是让主 Codex 持续实现、验证、提交和推送，同时让旁路 Codex 终端异步审查和测试，并把 blocker 通过本地文件回传给主线。

## 为什么需要它

多 Codex 协作的难点通常不是“能不能多开几个终端”，而是：

- 主 Codex 容易因为等 review/test 结果而空转。
- 审查和测试反馈容易散落在聊天里，难以关联到具体 change。
- 多个 agent 同时改源码会带来冲突和责任边界问题。
- 迟到的 test failure 或 blocking review 容易被忽略。

这个 skill 的策略是：只有 Main Codex 默认改源码；Reviewer/Tester 只读代码、运行验证、写报告。主线不把“没有报告”当作通过，但也不因为缺少新报告而默认停下。

## 当前实现

初始化后，每个目标仓库会有一个本地目录：

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

- `events.jsonl`：append-only 结构化事件日志，是真相来源。
- `coord.db`：可从 `events.jsonl` 重建的 SQLite 查询索引。
- `changes.md` / `reviews.md` / `tests.md`：给人读和旧 watcher 兼容的 Markdown ledger。
- `coord.py`：推荐的结构化 CLI，负责写锁、事件写入、状态查询、报告和恢复。

`.agent-coordination/` 是运行时状态，默认应加入 `.gitignore`，不要提交到业务仓库。

## 安装

把本仓库放到 Codex skills 目录：

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/ppep1/agent-coordination.git ~/.codex/skills/agent-coordination
```

重新打开 Codex 会话后即可使用。

## 快速开始

在你要协作的项目仓库里初始化：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/setup_agent_coordination.py --repo .
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . init
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor
```

Main Codex 发布一个变更：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change create \
  --file src/example.py \
  --summary "Add empty input validation" \
  --verify "pytest tests/test_example.py" \
  --risk medium
```

Reviewer Codex 等待新 change：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role reviewer --actor reviewer-a --interval 60
```

Reviewer 发布审查报告：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report review \
  --actor reviewer-a \
  --change chg_0001 \
  --decision concerns \
  --files-read src/example.py \
  --finding "medium:src/example.py:42:Missing empty input case"
```

Tester 发布测试报告：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report test \
  --actor tester-a \
  --change chg_0001 \
  --decision pass \
  --command "pytest tests/test_example.py"
```

处理完成后标记 processed：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . mark-processed --role reviewer --actor reviewer-a --change chg_0001
```

Main Codex 查询当前状态：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . status
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
```

如果 blocker 已在后续 change 里修复：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . finding resolve fnd_abc123 --reason "Fixed in chg_0002"
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report resolve rpt_abc123 --reason "Fixed and verified in chg_0002"
```

## 角色职责

### Main Codex

- 拥有实现、源码写入、git 状态、提交/推送和用户沟通。
- 每个小增量前先看 `coord.py blockers` 和 `coord.py status`。
- 每个小增量后运行 targeted verification，再 `change create`。
- valid `blocking` / `fail` / `blocked` 必须先处理，再做无关工作或最终交付。

### Reviewer Codex

- 默认只读源码，不改代码。
- 关注真实缺陷：逻辑错误、回归、API/契约不匹配、兼容性风险、缺失测试、安全和并发问题。
- 用 `pass`、`concerns`、`blocking` 表达结论。
- 不提交、不推送、不 reset、不安装依赖、不跑 broad formatter。

### Tester Codex

- 运行主线列出的验证命令，并根据 touched files 补充 focused tests。
- 用 `pass`、`fail`、`blocked` 表达结论。
- 未实际覆盖的内容必须写 `--untested`，不要假装硬件、vendor SDK 或外部服务覆盖。
- 不改源码、不提交、不推送、不运行破坏性命令。

## 常用命令

```bash
# 初始化 / 修复
coord.py --repo . init
coord.py --repo . rebuild
coord.py --repo . doctor

# 查询
coord.py --repo . status
coord.py --repo . blockers
coord.py --repo . next --role tester --actor tester-a

# change 生命周期
coord.py --repo . change create --file src/a.py --summary "..." --verify "pytest ..."
coord.py --repo . change verify chg_0001
coord.py --repo . change commit chg_0001
coord.py --repo . change push chg_0001

# 报告
coord.py --repo . report review --change chg_0001 --decision pass
coord.py --repo . report test --change chg_0001 --decision pass --command "pytest"

# 关闭已处理问题
coord.py --repo . finding resolve fnd_abc123 --reason "Fixed in chg_0002"
coord.py --repo . report resolve rpt_abc123 --reason "Fixed and verified in chg_0002"
```

上面命令里的 `coord.py` 可以替换成完整路径：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py
```

## 兼容说明

`scripts/watch_changes.py` 仍然保留，用于旧的 Markdown-only 工作流。新项目应优先使用 `scripts/coord.py`，因为它提供结构化事件、文件锁、SQLite 状态索引和更明确的恢复能力。

## 限制

- 这不是常驻 daemon，也不是云端 agent 编排平台。
- 需要你为 Reviewer/Tester 分别启动 Codex 终端并给出对应角色提示词。
- 目前没有任务租约，所以多个 watcher 仍可能重复处理同一个 change。
- 当前文件锁使用 Unix `fcntl`，适合 macOS/Linux。

## English

[中文](#agent-coordination) | English

A Codex skill for coordinating Main Codex, Reviewer Codex, and Tester Codex through a local structured event log. Main Codex keeps implementing, verifying, committing, and pushing; secondary Codex terminals asynchronously review and test changes, then report blockers through local files.

## Why This Exists

The hard part of multi-Codex work is not opening multiple terminals. The hard part is keeping coordination reliable:

- Main Codex can stall while waiting for review or test feedback.
- Review/test findings can get lost in chat and lose their link to a specific change.
- Multiple agents editing source files at the same time creates conflicts and unclear ownership.
- Late test failures or blocking review findings can be missed after Main has moved on.

This skill uses a conservative ownership model: Main Codex edits source files by default; Reviewer and Tester inspect, validate, and report. Missing reports are treated as pending review, not approval, but Main does not idle by default on verified low/medium-risk increments.

## Implementation

Each coordinated repository gets a local runtime directory:

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
- `changes.md`, `reviews.md`, `tests.md`: human-readable and legacy-compatible Markdown ledgers.
- `coord.py`: the preferred structured CLI for locking, event writes, status queries, reports, and recovery.

`.agent-coordination/` is runtime state. It should normally be ignored by the target repository and not committed.

## Install

Clone this repository into your Codex skills directory:

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/ppep1/agent-coordination.git ~/.codex/skills/agent-coordination
```

Open a new Codex session after installation.

## Quick Start

Initialize coordination inside the target repository:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/setup_agent_coordination.py --repo .
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . init
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor
```

Main Codex publishes a change:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change create \
  --file src/example.py \
  --summary "Add empty input validation" \
  --verify "pytest tests/test_example.py" \
  --risk medium
```

Reviewer Codex waits for work:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role reviewer --actor reviewer-a --interval 60
```

Reviewer publishes a report:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report review \
  --actor reviewer-a \
  --change chg_0001 \
  --decision concerns \
  --files-read src/example.py \
  --finding "medium:src/example.py:42:Missing empty input case"
```

Tester publishes a test report:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report test \
  --actor tester-a \
  --change chg_0001 \
  --decision pass \
  --command "pytest tests/test_example.py"
```

After reporting, mark the change as processed:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . mark-processed --role reviewer --actor reviewer-a --change chg_0001
```

Main Codex checks current state:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . status
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
```

When a blocker has been handled by a later change:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . finding resolve fnd_abc123 --reason "Fixed in chg_0002"
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report resolve rpt_abc123 --reason "Fixed and verified in chg_0002"
```

## Roles

### Main Codex

- Owns implementation, source edits, git state, commits/pushes, and user communication.
- Checks `coord.py blockers` and `coord.py status` before each increment.
- Runs targeted verification and publishes a change after each small increment.
- Handles valid `blocking`, `fail`, or `blocked` reports before unrelated work or final handoff.

### Reviewer Codex

- Reads source files but does not edit them by default.
- Focuses on real defects: logic bugs, regressions, API/contract mismatches, compatibility risks, missing tests, security, and concurrency issues.
- Uses `pass`, `concerns`, or `blocking`.
- Does not commit, push, reset, install dependencies, or run broad formatters.

### Tester Codex

- Runs listed verification commands and adds focused tests based on touched files.
- Uses `pass`, `fail`, or `blocked`.
- Records anything not actually covered with `--untested`.
- Does not fake hardware, vendor SDK, or external-service coverage.
- Does not edit source files, commit, push, or run destructive commands.

## Common Commands

```bash
# Initialize / repair
coord.py --repo . init
coord.py --repo . rebuild
coord.py --repo . doctor

# Query
coord.py --repo . status
coord.py --repo . blockers
coord.py --repo . next --role tester --actor tester-a

# Change lifecycle
coord.py --repo . change create --file src/a.py --summary "..." --verify "pytest ..."
coord.py --repo . change verify chg_0001
coord.py --repo . change commit chg_0001
coord.py --repo . change push chg_0001

# Reports
coord.py --repo . report review --change chg_0001 --decision pass
coord.py --repo . report test --change chg_0001 --decision pass --command "pytest"

# Resolve handled issues
coord.py --repo . finding resolve fnd_abc123 --reason "Fixed in chg_0002"
coord.py --repo . report resolve rpt_abc123 --reason "Fixed and verified in chg_0002"
```

Replace `coord.py` with the full path when needed:

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py
```

## Compatibility

`scripts/watch_changes.py` remains available for older Markdown-only workflows. New workflows should prefer `scripts/coord.py` because it provides structured events, file locking, a SQLite status index, and clearer recovery behavior.

## Limits

- This is not a daemon or cloud orchestration platform.
- Reviewer and Tester terminals still need to be started with role-specific prompts.
- There is no task lease yet, so multiple watchers can still process the same change.
- File locking currently uses Unix `fcntl`, so macOS/Linux are the intended environments.
