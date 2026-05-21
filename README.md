# Agent Coordination

<p>
  <a href="README.md"><img alt="中文" src="https://img.shields.io/badge/Language-%E4%B8%AD%E6%96%87-blue"></a>
  <a href="README.en.md"><img alt="English" src="https://img.shields.io/badge/Language-English-lightgrey"></a>
</p>

一个 Codex skill，用本地结构化事件日志协调 Main Codex、Reviewer Codex 和 Tester Codex。它适合你想开多个 Codex 终端协同工作，但又不想靠聊天复制粘贴、人工等待和记忆状态来同步的场景。

## 和 Codex 内置子代理有什么区别

Codex 自带的子代理系统已经适合很多并行任务：主 Codex 可以把一个明确、边界清楚的子任务委派给 explorer、worker 这类子代理，然后等待结果并整合。这个 skill 不是替代内置子代理，而是补齐另一类工作流：多个独立 Codex 终端长期围绕同一个 repo 协作，并且需要可恢复、可审计、可查询的状态。

| 能力 | Codex 内置子代理 | Agent Coordination skill |
| --- | --- | --- |
| 典型用途 | 单轮或短周期委派：探索代码、实现局部改动、并行处理明确子任务 | 长周期协作：Main 持续开发，Reviewer/Tester 独立 watch、claim、report |
| 生命周期 | 跟随当前会话，由主 Codex 创建、等待、整合 | 跨多个终端运行，状态落在目标 repo 的 `.agent-coordination/` |
| 状态记录 | 主要存在于对话和子代理返回结果里 | `events.jsonl` 是真相来源，`coord.db` 可查询，Markdown ledger 可人工阅读 |
| 恢复能力 | 适合当前任务内恢复；会话/上下文变化后依赖主 Codex 记忆和摘要 | 可运行 `status/open/blockers/timeline/doctor --strict` 恢复现场 |
| 审查/测试责任 | 可以委派，但结果通常由主 Codex 一次性接收 | Reviewer/Tester 有固定角色、claim/lease、report 质量门槛和 processed 状态 |
| 防重复处理 | 由主 Codex 调度避免重复 | `claim --ttl` 和 lease 机制避免多个 Reviewer/Tester 处理同一 change |
| 审计和交接 | 依赖聊天记录 | change、review、test、resolve 都是结构化事件，可导出 HTML |

简单说：内置子代理更像“主 Codex 的并行工作线程”；这个 skill 更像“本地、文件驱动的协作协议”。如果只是让一个子代理读一段代码或改一个小模块，用内置子代理更直接；如果你要长期保持 Main/Reviewer/Tester 三个终端分工、不中断地推进实现和验证，用这个 skill 更合适。

## 它解决什么问题

你以前可能是这样用的：

```text
开三个 Codex 终端
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

进入你要开发的目标 repo：

```bash
cd /path/to/your/repo
```

初始化协调目录：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/setup_agent_coordination.py --repo .
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . init
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor --strict
```

确认状态：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . status
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . open
```

第一次通常会看到没有 change、没有 blocker。

## 三个终端怎么开

推荐你还是开三个 Codex 终端，但每个终端的职责更清楚。

### 终端 1：Main Codex

Main Codex 负责写代码、运行 targeted verification、发布 change、处理 blocker、提交和推送。

给 Main Codex 的提示词可以直接用：

```text
你是 Main Codex。使用 agent-coordination skill。

在这个 repo 中：
1. 你拥有源码修改、git 状态、验证、提交/推送和最终用户沟通。
2. 每个实现增量前运行：
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . open
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . status
3. 每个小增量完成后运行 targeted verification，然后发布 change：
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change create --capture-diff --file <file> --summary "<summary>" --verify "<command>" --risk medium
4. 不要等待 fresh reviewer/tester report 才继续低/中风险 verified increment。
5. 如果 blockers/fail/blocked 出现，先修，再继续无关工作。
6. 修复 blocker 后，用 finding resolve 和 report resolve 关闭已处理问题。
7. 最终交付前运行：
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . doctor
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
   git status --short
```

也可以直接生成最新版提示词：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt main
```

Main 每做完一个小改动，就发布一个 change：

```bash
pytest tests/test_parser.py

python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . change create \
  --capture-diff \
  --file src/parser.py \
  --summary "Add empty input validation" \
  --verify "pytest tests/test_parser.py" \
  --risk medium
```

命令会输出类似：

```text
chg_0001
```

这就是 Reviewer/Tester 后续报告要引用的 change id。

### 终端 2：Reviewer Codex

Reviewer Codex 默认只读代码，不改源码。

给 Reviewer Codex 的提示词可以直接用：

```text
你是 Reviewer Codex。使用 agent-coordination skill。

规则：
1. 不改源码，不 commit，不 push，不 reset，不安装依赖，不跑 broad formatter。
2. 只做只读代码审查。
3. 等待并领取新 change：
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role reviewer --actor reviewer-a --claim --interval 60
4. 发现新 change 后，读取输出中的 change_id、files、verification、risk、diff_path。
5. 优先审查 diff 快照，再审查 touched files 和相关契约，只报告真实缺陷、回归、兼容性风险、缺失测试、安全/并发问题。
6. 发布报告：
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report review --actor reviewer-a --change <change_id> --decision pass|concerns|blocking --files-read <file> --finding "severity:file:line:message"
7. 报告后标记 processed，这会完成已领取的 task：
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . mark-processed --role reviewer --actor reviewer-a --change <change_id>
8. 然后继续 watch。
```

也可以直接生成最新版提示词：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt reviewer --actor reviewer-a
```

Reviewer 开始等待：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role reviewer --actor reviewer-a --claim --interval 60
```

如果 Main 发布了 change，Reviewer 会看到类似：

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

如果审查发现非阻塞问题：

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

如果没有问题：

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

如果是必须先修的问题：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report review \
  --actor reviewer-a \
  --change chg_0001 \
  --decision blocking \
  --files-read src/parser.py \
  --finding "high:src/parser.py:42:Empty input crashes the parser"
```

### 终端 3：Tester Codex

Tester Codex 负责真实运行验证，不假装覆盖。

给 Tester Codex 的提示词可以直接用：

```text
你是 Tester Codex。使用 agent-coordination skill。

规则：
1. 不改源码，不 commit，不 push，不 reset，不安装依赖，不跑破坏性命令。
2. 不假装硬件、vendor SDK、外部服务覆盖。
3. 等待并领取新 change：
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role tester --actor tester-a --claim --interval 60
4. 发现新 change 后，优先运行 change 里列出的 verification command；再根据 touched files 补充 focused tests。
5. 发布测试报告：
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report test --actor tester-a --change <change_id> --decision pass|fail|blocked --command "<command>" --untested "<reason>"
6. 报告后标记 processed，这会完成已领取的 task：
   python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . mark-processed --role tester --actor tester-a --change <change_id>
7. 然后继续 watch。
```

Tester 开始等待：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . watch --role tester --actor tester-a --claim --interval 60
```

测试通过：

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

测试失败：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report test \
  --actor tester-a \
  --change chg_0001 \
  --decision fail \
  --command "pytest tests/test_parser.py" \
  --finding "high:tests/test_parser.py:18:Parser regression test fails"
```

环境缺依赖或外部服务不可用：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report test \
  --actor tester-a \
  --change chg_0001 \
  --decision blocked \
  --command "pytest tests/test_parser.py" \
  --untested "pytest is not installed in this environment"
```

## Main 怎么处理反馈

Main 随时可以查状态：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . status
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . open
```

如果看到：

```text
rpt_abc123 chg_0001 reviewer:blocking actor=reviewer-a
fnd_def456 chg_0001 high src/parser.py:42 Empty input crashes the parser
```

Main 应该先修这个问题，发布新的 change：

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

然后关闭旧 finding 和 report：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . finding resolve fnd_def456 --reason "Fixed in chg_0002"
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . report resolve rpt_abc123 --reason "Fixed and verified in chg_0002"
```

再确认：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . blockers
```

输出：

```text
No open blockers.
```

如果要给人看当前协作状态，可以导出 HTML 面板：

```bash
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . export-html
```

默认输出到 `.agent-coordination/reports/status.html`。

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

# 提示词和面板
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt main
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . prompt reviewer --actor reviewer-a
python3 ~/.codex/skills/agent-coordination/scripts/coord.py --repo . export-html

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

- 这不是常驻 daemon，也不是云端 agent 编排平台。
- 需要你为 Reviewer/Tester 分别启动 Codex 终端并给出对应角色提示词。
- 任务 lease 只在本地协调目录内生效，不是跨机器分布式锁。
- 当前文件锁使用 Unix `fcntl`，适合 macOS/Linux。
