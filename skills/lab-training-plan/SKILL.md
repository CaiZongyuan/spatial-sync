---
name: lab-training-plan
description: 与人逐轮制定具身训练计划，记录可测量假设和完整访谈，并提交平台人工审批。用于制定或修订计划，不执行训练作业。
---

# 训练计划

先读上一轮计划、评估和实验记录，确认失败阶段、已证伪假设与剩余预算。已有计划用 `lw plan ls --json` 和 `lw plan show plan-0006 --json` 阅读；尚无历史时记录这是第一轮。已证伪的假设只有附上新证据后才能重提。

以实际 `goalId` 读取目标记录和预算；默认目标为 `goal/<Task.name>`，跨 Task 版本保持不变。自然语言 `goal` 是结果描述。

```bash
lw notebook show goal/fr3-pick-tube --json
lw notebook show goal/fr3-pick-tube --refuted --json
lw loop show goal/fr3-pick-tube --json
```

读完各条自动判定、人工改判、比较条件与预算覆盖说明后再开始访谈。缺少历史或限额证据时记录未知。
假设 `confirmed` 只表示达成声明的预测，不表示 Candidate 已被接受或可以部署；分别阅读接受决定和人工部署状态。

## 访谈

每轮提出当前能判断的全部问题，每题写明推荐答案及理由，再等待人的回答；“是”表示接受推荐。根据回答继续追问，直到以下选择已确定：

- 目标与基线：失败在哪里，哪个评估指标应该从多少变到多少。
- 单一改动与因果假设：本轮改什么，哪些条件固定，怎样判定假设失败。
- Task、场景与评估：不可变版本、覆盖参数与种子、成功标准。
- 算法、模型与超参：推荐沿用上一轮，除非它们正是被检验的变量。
- 作业种子与预算：GPU 小时和评估回合数应在剩余闭环预算内，由人决定。

把每个问题、推荐答案、人的原始回答和最终决定完整写入与 `plan.yaml` 同目录的 `interview.md`，用三列 Markdown 表格（问题、Agent 推荐、人的回答）逐行记录。这份记录随提交快照到审批，后改本地文件不会改动已提交计划。

## 固定 Task

Task 是 Isaac Lab 下游源码包，`pyproject.toml` 声明 `isaaclab.tasks` entry point。`task.yaml` 包含 `name`、`title`、精确 `scene` 版本、`robot`、`implements`、`episode.params`、平台 `success` 条件、`control`、包内 `observation` 文件和 `isaaclab` 配置；同时声明固定 `evaluation`（场景、回合数、种子、参数覆盖）及 `loopBudget`（`gpuHours`、`episodes`、`days`，可选 `llmUsd`）。T1 使用已有 FR3 的 `robot-and-tubes@2`。

```bash
lw task push tasks/fr3-pick-tube --json
lw task ls --json
lw task show task/fr3-pick-tube@1 --json
```

检查返回的版本和 `contentHash`。同内容重推会返回已有版本；推送只做静态快照，不运行包里的代码。

## 示范输入

已有成功回合时，先按精确来源 Task 读取示范；需要标记时使用实际录制 ID。已关联具身 train 评估的录制可省略 `--task`，普通会话必须指明固定来源 Task。

```bash
lw demo ls --task task/fr3-pick-tube@1 --json
lw demo add session-EXAMPLE --task task/fr3-pick-tube@1 --json
```

录制完成不代表 Task 成功。平台核对录制中的目标动作、实际参数、成功条件及同一回合身份；缺证据时先解决来源问题，不填写猜测。已知 held-out/test 来源不能转为训练数据，Agent 题目分数也不表示具身成功。

从返回值复制实际 `set.id`，在消费 Task 的 `task.yaml` 声明用途，再推一个新 Task 版本：

```yaml
demonstrations:
  - {set: demoset/fr3-pick-tube@1, purpose: reset}
```

`purpose` 可为 `reset` 或 `imitation`。Task 的 Python 代码消费节点提供的集合/录制本地路径，平台只固定数据输入。新示范生成新集合版本；已有 Task 和 Job 不自动升级。重复标记返回首次版本，按任务列出的 `set` 是当前集合；计划明确选择实际读过的固定版本，不写“latest”。标记数据不批准训练。

## 提交

计划必须给出以下内容；替换 Task 版本、上一轮、假设和访谈，不臆造历史：

```yaml
kind: training
title: FR3 插入阶段第 1 轮
goal: 平台评估至少 85/100
goalId: goal/fr3-pick-tube
task: task/fr3-pick-tube@1
algorithm: {library: rsl_rl, name: PPO, version: 5.4.1}
model: {actor: [256, 128], critic: [256, 128]}
hyperparameters: {num_envs: 2048, max_iterations: 800, learning_rate: 0.001}
jobs: {seeds: [42, 43, 44]}
budget: {gpuHours: 9, episodes: 100}
evaluation: {scene: robot-and-tubes@2, episodes: 100, threshold: 85}
rationale: 上一轮插入阶段较弱，检验持管目标引导的影响
hypotheses:
  - id: H1.1
    cause: 插入阶段引导过弱
    change: held_goal 权重从 0.1 改为 0.2
    predict: {metric: stage.insert, from: 0.61, to: ">= 0.80"}
notebookRead: []
interview: interview.md
```

每条假设都需要 `id`、`cause`、`change` 和 `predict.metric` / `predict.to`。目标是有限数值或数值比较，例如 `>= 0.80`；“更好”不是可测量预期。`predict.from` 可省略，第一轮理由应说明无历史；有上一轮时加 `previous: plan-0006`，`notebookRead` 填实际已读条目。

```bash
lw plan submit plan.yaml --json
lw plan show plan-0007 --json
```

使用实际返回的计划 ID 与审批 ID，向人说明预算、假设及相对上一轮的变化，并给出 Web 待审批页。人在审批页或交互终端决定；MCP 没有审批工具。被驳回后回到访谈，按反馈用 `lw plan revise plan-0007 plan.yaml --json` 提交新修订。算法、模型、Task、奖励或声明的 seeds 改变都要经过新的审批。计划批准仅表示意图获批，训练执行另受作业与 GPU 授权约束。

同一 Plan ID 的修订共享预算池并保持目标；独立新一轮用新 Plan ID 和 `previous`。
审批若返回 `loop_budget_changed`，重新读取预算并提交新修订，让人看到当前依据；已有批准重试不创建新授权。
后续具身评估用实际 Plan ID 与固定修订关联，平台才会将结果和尝试消耗写入该目标：

```bash
lw eval run robot_arm.virtual --suite suite/lab-demo-pick-place@1 --plan plan-0007 --revision 1 --json
```

上例只示意接口；选择与实际 Task 场景和动作一致的评估集。实验记录只提供 train 投影，使用平台返回的失败/无效证据，保留未知；下一轮的统计接受规则仍按当前规格执行。
