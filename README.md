# Agent Coordination

<p>
  <a href="README.md"><img alt="中文" src="https://img.shields.io/badge/Language-%E4%B8%AD%E6%96%87-blue"></a>
  <a href="README.en.md"><img alt="English" src="https://img.shields.io/badge/Language-English-lightgrey"></a>
</p>

一个 Codex skill，用本地结构化事件日志协调 Main Codex、Reviewer Codex 和 Tester Codex。它适合你想在同一个项目里开多个 Codex 对话/会话协同工作，但又不想靠聊天复制粘贴、人工等待和记忆状态来同步的场景。

## 和 Codex 内置子代理有什么区别

Codex 自带的子代理系统已经适合很多并行任务：主 Codex 可以把一个明确、边界清楚的子任务委派给 explorer、worker 这类子代理，然后等待结果并整合。这个 skill 不是替代内置子代理，而是补齐另一类工作流：多个独立 Codex 终端长期围绕同一个 repo 协作，并且需要可恢复、可审计、可查询的状态。

| 能力 | Codex 内置子代理 | Agent Coordination skill |
| --- | --- | --- |
| 典型用途 | 单轮或短周期委派：探索代码、实现局部改动、并行处理明确子任务 | 长周期协作：Main 持续开发，Reviewer/Tester 独立 watch、claim、report |
| 生命周期 | 跟随当前会话，由主 Codex 创建、等待、整合 | 跨多个 Codex 对话/会话运行，状态落在目标 repo 的 `.agent-coordination/` |
| 状态记录 | 主要存在于对话和子代理返回结果里 | `events.jsonl` 是真相来源，`coord.db` 可查询，Markdown ledger 可人工阅读 |
| 恢复能力 | 适合当前任务内恢复；会话/上下文变化后依赖主 Codex 记忆和摘要 | 可运行 `status/open/blockers/timeline/doctor --strict` 恢复现场 |
| 审查/测试责任 | 可以委派，但结果通常由主 Codex 一次性接收 | Reviewer/Tester 有固定角色、claim/lease、report 质量门槛和 processed 状态 |
| 防重复处理 | 由主 Codex 调度避免重复 | `claim --ttl` 和 lease 机制避免多个 Reviewer/Tester 处理同一 change |
| 审计和交接 | 依赖聊天记录 | change、review、test、resolve 都是结构化事件，可导出 HTML |

简单说：内置子代理更像“主 Codex 的并行工作线程”；这个 skill 更像“本地、文件驱动的协作协议”。如果只是让一个子代理读一段代码或改一个小模块，用内置子代理更直接；如果你要长期保持 Main/Reviewer/Tester 三个 Codex 对话分工、不中断地推进实现和验证，用这个 skill 更合适。

## 它解决什么问题

你以前可能是这样用的：

```text
开三个 Codex 对话/会话
-> 让它们都读 skill
-> Main 写代码
-> Reviewer 人工看
-> Tester 人工跑
-> 你自己在中间协调状态
```

这个方式能跑，但有几个问题：

- Main Codex 容易因为等 Reviewer/Tester 反馈而空转。
- 审查和测试反馈散落在聊天里，难以绑定到具体 change。
- 多个 agent 如果都能改源码，会造成冲突和责任边界混乱。
- 迟到的 test failure 或 blocking review 容易被忽略。
- 中断恢复困难，不知道哪个 change 已经 review/test。

这个 skill 的核心思路是：

```text
Main Codex: 持续实现 + 验证 + 发布 change
Reviewer Codex: watch -> 只读审查 -> report -> mark-processed -> watch
Tester Codex: watch -> 运行测试 -> report -> mark-processed -> watch
Main Codex: status/open/blockers -> 修 blocker -> 继续
```

## 文件和状态模型

在目标项目里初始化后，会生成：

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
- `changes.md`：人类可读的变更 ledger。
- `reviews.md`：人类可读的审查报告 ledger。
- `tests.md`：人类可读的测试报告 ledger。
- `coord.py`：推荐的结构化 CLI，负责写锁、事件写入、查询、报告和恢复。

`.agent-coordination/` 是运行时状态，不是 skill 本体，不要提交到业务仓库。初始化脚本默认会把它加入 `.gitignore`。

## 安装

把 skill clone 到 Codex skills 目录：

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/ppep1/agent-coordination.git ~/.codex/skills/agent-coordination
```

如果你已经装过：

```bash
cd ~/.codex/skills/agent-coordination
git pull
```

重新打开 Codex 会话后即可使用。

## 第一次在项目里启用

在目标 repo 里初始化协调目录后，就可以启动多 Codex 协作。具体命令统一放在后面的“常用命令速查”。

第一次启用时只需要做三件事：

1. 进入目标 repo。
2. 初始化 `.agent-coordination/` 并运行 `doctor --strict`。
3. 为 Main、Reviewer、Tester 生成对应 role prompt；如果需要 Observer，也生成 Observer prompt。

第一次通常会看到没有 change、没有 blocker。

## 四个 Codex 对话怎么开

核心执行角色是 3 个 Codex 对话：Main、Reviewer、Tester。第 4 个 Observer Codex 是可选的，只负责和用户交流状态。

这些对话看到的都是普通 Codex 聊天界面，区别是角色提示词不同。它们通过同一个 `.agent-coordination/` 目录交换状态，不靠你在聊天之间复制粘贴结果。

目标工作流是无人值守的：Main Codex 先理清任务和路线，并在开始前做一次预检，找出可能需要人工决策的因素：缺少权限或外部服务、命令有破坏性风险、必须购买/登录/提供凭据、需求本身冲突。你确认路线和这些风险处理方式后，Main 持续执行到最终交付，不在 roadmap 的每个 phase 结束时停下来汇报或等待确认。

正常的代码修改、读取项目文件、运行本地测试、运行非破坏性构建/格式化检查、提交和推送，在用户已经授权交付的情况下不应触发中途确认。真正无法由 skill 解决的是运行环境层面的限制：Codex/app interrupt、上下文限制、系统睡眠、进程退出、应用重启。

### 一句话启动 role prompt

role prompt 是固定模板，`coord.py prompt <role>` 是权威来源。你不需要手动复制 prompt 输出；在新开的 Codex 对话里直接发送一句启动语，让它自己运行 prompt 命令并按输出执行。

如果这个 Codex 对话已经在目标 repo 中，直接发对应启动语即可。如果不确定当前目录，先在启动语里补一句“请先切到 `/path/to/your/repo`”。

Reviewer 的启动语示例：

```text
请作为 Reviewer Codex 启动。先确认当前目录是目标 repo，然后运行：
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt reviewer --actor reviewer-a
读取该命令输出的完整 role prompt，并严格按它执行。启动后不要等待我的进一步任务；进入 watch/claim/review/report/mark-processed 循环。
```

Main、Tester、Observer 的启动语在各自小节里给出。

### 对话 1：Main Codex

Main Codex 负责实现、验证、发布 change、处理 blocker、commit/push 和最终用户交付。

具体做法：

1. 在目标 repo 打开第 1 个 Codex 对话。
2. 给这个对话发送：

```text
请作为 Main Codex 启动。先确认当前目录是目标 repo，然后运行：
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt main
读取该命令输出的完整 role prompt，并严格按它执行。接下来我会给你任务；你先做 preflight review，等我确认开始后持续推进到最终交付。
```

3. 再把你的任务发给 Main；它会先做 preflight review，你确认开始后持续推进。

Main 的关键规则：

- 开始前做一次 preflight review；如果有权限、凭据、破坏性命令或需求冲突风险，先问用户。
- 用户确认开始后，不在 phase 边界停下来请求继续；进展写入 coordination 状态后继续推进。
- 每个有意义的小增量都要验证并发布 change。
- 不等待 fresh reviewer/tester report 才继续低/中风险 verified increment。
- 一旦出现有效 `blocking`、`fail`、`blocked`，先处理再做无关工作。
- 用户只问状态时，简短报告 `status/open/blockers` 后继续；只有明确说暂停、停止、等待确认、换方向或不要提交，才中断无人值守流程。

### 对话 2：Reviewer Codex

Reviewer Codex 是只读审查角色。它复制 role prompt 后进入 watch 循环，发现 change 后 claim、审查、写 review report、mark processed，然后继续 watch。

具体做法：

1. 在同一个目标 repo 打开第 2 个 Codex 对话。
2. 给这个对话发送：

```text
请作为 Reviewer Codex 启动。先确认当前目录是目标 repo，然后运行：
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt reviewer --actor reviewer-a
读取该命令输出的完整 role prompt，并严格按它执行。启动后不要等待我的进一步任务；进入 watch/claim/review/report/mark-processed 循环。
```

3. 不需要再给 Reviewer 具体任务；它会等 Main 发布 change 后自动 claim、审查、写 report、mark processed。

Reviewer 的责任边界：

- 不改源码，不 commit/push/reset，不安装依赖，不跑 broad formatter。
- 只报告真实缺陷、回归、兼容性风险、缺失测试、安全/并发问题。
- 不要求用户转述结果，也不等待用户确认；报告写入 coordination 状态即可。

### 对话 3：Tester Codex

Tester Codex 负责真实验证。它复制 role prompt 后进入 watch 循环，发现 change 后 claim、运行验证、写 test report、mark processed，然后继续 watch。

具体做法：

1. 在同一个目标 repo 打开第 3 个 Codex 对话。
2. 给这个对话发送：

```text
请作为 Tester Codex 启动。先确认当前目录是目标 repo，然后运行：
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt tester --actor tester-a
读取该命令输出的完整 role prompt，并严格按它执行。启动后不要等待我的进一步任务；进入 watch/claim/test/report/mark-processed 循环。
```

3. 不需要再给 Tester 具体任务；它会等 Main 发布 change 后自动 claim、运行验证、写 report、mark processed。

Tester 的责任边界：

- 不改源码，不 commit/push/reset，不安装依赖，不跑破坏性命令。
- 优先运行 Main 发布 change 时列出的验证命令，再补充 focused tests。
- 不假装硬件、vendor SDK、外部服务覆盖；没测到就写 `untested` 或 `blocked`。
- 不要求用户转述结果，也不等待用户确认；报告写入 coordination 状态即可。

### 可选对话 4：Observer Codex

Observer Codex 是可选的用户交流角色，适合用轻量模型，例如 GPT-5.4-Mini。它不自动跑项目任务，只读查询 coordination 状态并解释给你。

具体做法：

1. 在同一个目标 repo 打开第 4 个 Codex 对话，模型可以选轻量模型。
2. 给这个对话发送：

```text
请作为 Observer Codex 启动。先确认当前目录是目标 repo，然后运行：
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt observer --actor observer-a
读取该命令输出的完整 role prompt，并严格按它执行。你只负责回答我的状态问题，不参与实现、审查、测试或任务调度。
```

3. 之后你只向 Observer 问状态，例如“现在做到哪了？”、“有没有 blocker？”、“Reviewer/Tester 做了什么？”。

Observer 的责任边界：

- 可以回答“现在做到哪了、有没有 blocker、Reviewer/Tester 做了什么、下一个 change 是什么、HTML 面板在哪里”。
- 只能运行只读查询：`status`、`open`、`blockers`、`show`、`timeline`、`export-html`。
- 不 claim 任务，不写 review/test report，不 mark processed，不改源码，不 commit/push。
- 不指挥 Main 改方向；如果你要改变任务方向，直接对 Main Codex 发明确指令。

也可以不用 Observer，直接开一个普通 shell 终端运行只读查询命令。区别是 Observer 会把状态解释成自然语言。

## Main 怎么处理反馈

Main 不需要等待每个 change 都拿到 fresh review/test 才继续；但是每轮实现后都要查 `blockers/open/status`。

如果 Reviewer 或 Tester 写入有效 blocker，Main 应该先修复，再发布新的 change，并关闭已经处理的 finding/report。迟到的 blocker 如果发生在 commit/push 之后，也用后续 change 修复。

缺失报告表示“还在 pending review/test”，不是 approval。对于低/中风险且已经本地验证的增量，Main 可以继续推进；对于高风险改动，Main 应更保守地等待或主动检查状态。

## 如何观察当前状态

推荐用 Observer Codex 或普通 shell 终端观察，不要打断 Main/Reviewer/Tester 的执行循环。

常用观察入口：

- `status`：整体状态。
- `open`：还未完成 review/test 的 change。
- `blockers`：当前阻塞问题。
- `show <change>`：单个 change 的详情。
- `timeline <change>`：单个 change 的事件时间线。
- `export-html`：导出 HTML 状态面板。

## 推荐日常循环

```text
Main:
  blockers/open/status -> 实现一个小增量 -> targeted verification -> change create -> blockers/open/status -> 继续

Reviewer:
  watch -> review -> report review -> mark-processed -> watch

Tester:
  watch -> run tests -> report test -> mark-processed -> watch
```

Main 不需要等每个 change 都拿到 fresh review/test 才继续；但是一旦 `blockers` 出现有效问题，必须先处理。

## 常用命令速查

```bash
# 初始化 / 修复
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --version
python3 ~/.codex/skills/agent-coordination/scripts/setup_agent_coordination.py --repo .
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . init
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . rebuild
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor --strict

# 查询
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . status
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . open
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . next --role tester --actor tester-a
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . show chg_0001
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . timeline chg_0001
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . export-html

# 任务 claim / lease
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . claim --role reviewer --actor reviewer-a --ttl 900
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . release --role reviewer --actor reviewer-a --change chg_0001

# change 生命周期
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change create --capture-diff --file src/a.py --summary "..." --verify "pytest ..."
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change verify chg_0001
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change commit chg_0001
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change push chg_0001

# 报告
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report review --change chg_0001 --decision pass
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report test --change chg_0001 --decision pass --command "pytest"

# 提示词
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt main
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt reviewer --actor reviewer-a
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt tester --actor tester-a
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt observer --actor observer-a

# 复制 prompt 后分别粘贴到 Main / Reviewer / Tester / Observer 四个 Codex 对话

# 关闭已处理问题
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . finding resolve fnd_abc123 --reason "Fixed in chg_0002"
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report resolve rpt_abc123 --reason "Fixed and verified in chg_0002"
```

## 运行自测

skill 自带一个端到端测试脚本，会在临时目录里验证初始化、change、diff 快照、claim/lease、报告质量门槛、report、mark-processed、show/open/timeline、prompt、export-html、resolve 和 rebuild：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/test_coord.py
```

GitHub Actions 也会运行同一组基础检查：`py_compile` 和 `scripts/test_coord.py`。

## 兼容说明

`scripts/watch_changes.py` 仍然保留，用于旧的 Markdown-only 工作流。新项目应优先使用 `scripts/coord.py`，因为它提供结构化事件、文件锁、SQLite 状态索引和更明确的恢复能力。

## 限制

- 这不是常驻 daemon，也不是云端 agent 编排平台；无法绕过 Codex/app interrupt、上下文限制、系统睡眠、进程退出或应用重启。
- 权限、破坏性命令、外部登录/凭据、需求冲突应在 Main 开始前预检并一次性询问；正常改代码、跑本地测试、提交和推送不应在每个 phase 打断。
- 需要你为 Reviewer/Tester 分别启动 Codex 对话/会话并给出对应角色提示词；Observer 是可选的第 4 个 Codex 对话。
- 任务 lease 只在本地协调目录内生效，不是跨机器分布式锁。
- 当前文件锁使用 Unix `fcntl`，适合 macOS/Linux。
