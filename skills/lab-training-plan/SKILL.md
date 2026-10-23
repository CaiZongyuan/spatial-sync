---
name: lab-training-plan
description: 与人逐轮制定具身训练计划，记录可测量假设和完整访谈，并提交平台人工审批。用于制定或修订计划，不执行训练作业。
---

# 训练计划

先读上一轮计划、评估和实验记录，确认失败阶段、已证伪假设与剩余预算。已有计划用 `lw plan ls --json` 和 `lw plan show plan-0006 --json` 阅读；尚无历史时记录这是第一轮。已证伪的假设只有附上新证据后才能重提。

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

## 提交

计划必须给出以下内容；替换 Task 版本、上一轮、假设和访谈，不臆造历史：

```yaml
kind: training
title: FR3 插入阶段第 1 轮
goal: 平台评估至少 85/100
task: task/fr3-pick-tube@1
algorithm: {library: rsl_rl, name: PPO, version: 5.4.1}
model: {actor: [256, 128], critic: [256, 128]}
hyperparameters: {num_envs: 2048, max_iterations: 800, learning_rate: 0.001}
jobs: {seeds: [42, 43, 44]}
budget: {gpuHours: 9}
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
