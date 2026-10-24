# Lab RSI

> **递归自我改进框架：两层改进对象、评估中心、成本中心与资源 · Technical Specification v1.0**
> 日期：2026-10-18
> 状态：Approved direction，待实施（未决项集中在 §16，均有默认值）
> 前置：[Lab Assets and Scenes](LAB_ASSETS_AND_SCENES_SPEC_V1.md)（北极星 §1、agent 接口与人工关口 §10）、[Lab Live Operations](LAB_LIVE_OPERATIONS_SPEC_V1.md)（Plan）、[Lab Training](LAB_TRAINING_SPEC_V1.md)（作业、节点、评估、策略）。
> 术语：以 [GLOSSARY.md](../../GLOSSARY.md) 为准。设计参考见 §17。
> 总览与验收：[Lab RSI Platform PRD](LAB_RSI_PLATFORM_PRD_V1.md)（用户故事、交付顺序、测试接缝、错误码）；界面：[Lab Web UI](LAB_WEB_UI_SPEC_V1.md)。

## 0. 一句话定义

平台用同一套框架改进两类对象：操作实验室的 **Agent**（模型固定，改它的 Harness），以及驱动机器人的 **Controller**（本体固定，改策略、程序、奖励与数据）。每一轮是"一个可证伪假设 → 一处改动 → 在评估集上评估 → 满足接受规则才成为新的 Reference"，分数、成本、版本都可追溯；人在关口审批。

## 1. 与已有规格的关系

| 已有内容 | 本规格中的处理 |
|---|---|
| 素材规格 §1 北极星 | 不变；本规格把"改进"细化为两层 |
| 训练规格 §7 评估、§8.7 实验记录、§8.8 接受规则与看板、§8.9 闭环预算 | 框架层面以本规格为准；训练规格保留具身层的执行细节 |
| 训练规格 §5 计算节点 | 归入"资源 › 算力"（§12） |
| 实时操作规格 §8 Plan | 两层的每一轮都以 `training` 类型的 Plan 提交（Agent 层同样适用） |
| 信息规格 D-12 单活跃会话 | 不变；Agent 层评估与具身层评估都顺序占用会话（§6.3） |

## 2. 范围

### 2.1 包含

- 两层改进对象的定义、版本与快照。
- Embodiment 与 Robot Model 目录。
- Agent runtime 接入：导入已有运行记录；完整评估交接另行交付（§4）。
- 评估中心：统一的评估集、记分板、可比较条件、基线与 test 集。
- 接受规则、角色与信息隔离、Default RSI 工具包。
- 成本中心：LLM Token、GPU 小时，价格表与异常。
- 资源：算力、模型（LLM 与 Robot Model）、机器人。
- CLI、MCP、平台 skill、Web 页面。

### 2.2 不包含

- VLA 推理与微调（只登记、展示）。
- Default 之外的 RSI 算法（只定义接入协议）。
- 多用户、权限、计费结算。
- 真机机器人时间计费（预留字段）。

## 3. 两层改进对象

| | Agent 层 | 具身层 |
|---|---|---|
| 改进对象 | **Agent**，如 `agent/lab-operator` | **Controller**，如 `controller/fr3-arm` |
| 固定不变 | LLM 模型 `(provider, model_id)` 与思考等级 | Embodiment 与场景版本 |
| 可以改 | **Harness**：平台 skill 版本、Agent 指令（`AGENTS.md`）、钩子、工具与 MCP 配置 | 策略版本、设备程序（示教 / 策略 / 脚本）、奖励（Task 版本）、示范数据、控制参数 |
| 一轮的产物 | Harness 新版本 | Controller 新版本 |
| 评估 | Agent 评估集（实验室操作任务） | 具身评估集（机器人回合） |
| 成本 | LLM Token | GPU 小时（训练 + 评估） |

### 3.1 Agent 与 Harness

```text
<库>/agents/lab-operator/
  agent.yaml            名称、默认模型与思考等级、当前 Reference 版本
  harness/              可编辑的 Harness 目录
    AGENTS.md
    skills.lock         平台 skill 名称与版本
    hooks/
    mcp.yaml
  snapshots/v4.tar.gz   每个版本一份快照（不含凭据）
```

- Harness 版本是整数，每接受一个 Candidate 加一；落选的版本号不复用。
- 改动 Harness 前，平台确保当前版本已有快照。快照可导出、导入，也可从快照创建新 Agent。
- 模型与思考等级不属于 Harness；换模型就是另一组可比较条件（§7.3）。

### 3.2 Embodiment 与 Controller

```yaml
# <库>/embodiments/fr3-sim.yaml
id: fr3-sim
title: FR3 + franka_hand（仿真）
kind: sim                      # sim | real
asset: robot/fr3@1
bindings: [{scene: fr3-lab@2, entity: /World/Lab/RobotA}]
---
# <库>/controllers/fr3-arm/5.yaml
controller: fr3-arm
version: 5
embodiment: fr3-sim
robotModel: ppo-mlp-256-128-64
policy: policy/fr3-pick-tube@2
program: robot_arm.policy
action: {space: joint_position_delta, hz: 30}
observation: obs_spec@1
task: task/fr3-pick-tube@3
```

- Controller 版本记录"这台机器人此刻由什么驱动"的完整组合。提升一个策略或脚本程序，就是产生一个新的 Controller 版本，并按素材规格 §10.4 的关口生成新场景版本。
- 真机 Embodiment（`kind: real`）与仿真 Embodiment 永不共用身份（实时操作规格 L-02）。

### 3.3 Robot Model 目录

```yaml
# <库>/robot-models/<id>.yaml
id: ppo-mlp-256-128-64
family: rl-policy              # vla | rl-policy | scripted
source: 本平台训练（rsl_rl PPO）
license: 随训练代码
embodiments: [fr3-sim]
actionSpace: joint_position_delta
compute: {inference: cpu}
runnable: true                 # v1 只有 onnx 策略与脚本程序可运行
```

VLA 条目（π0、GR00T N、OpenVLA、SmolVLA 等）在 v1 只登记来源、许可、支持的本体、动作空间与推理算力，`runnable: false`。条目内容由人或 agent 依据官方来源填写，并注明来源链接。

## 4. Agent runtime 接入

#44 首版采用 [Runtime records integration A](LAB_AGENT_RUNTIME_RECORDS_SPEC_V1.md)：
现有 Claude Code/pi 等 runtime 通过 skills、CLI、MCP 接入；平台导入、持久化和展示其已有会话、事件与报告用量。
本轮不启动 SDK/runtime、不代理模型凭据、不自动驱动完整 Suite；原托管 SDK、全题自动运行与付费 smoke 首版验收由新规格替代。

### 4.1 运行时与平台职责

- 外部 runtime 拥有 Agent 执行、模型、上下文、工具、取消与计量；平台 skills 描述工作流，CLI/MCP 调用共享 HTTP API。
- 平台首版接收明确选择的 Claude Code 原生记录文件或记录数组，保留身份、消息、工具事件、来源与报告用量，支持持久读回和幂等刷新。pi 等格式须有真实格式导入器后才支持。
- 导入记录可显式关联 Agent/Harness/Plan/Goal；关联不是执行授权，也不产生平台评分、人工审批或 Evaluation 结果。

### 4.2 凭据与模型

- 外部 runtime 保留自身凭据和模型配置；导入无需平台 Vault、模型登记或人工价格表。
- 既有 Vault 能力保持原用途。导入流程不读取 runtime 认证或配置文件，不扫描用户目录。

### 4.3 完整评估交接

人在 Claude Code 里通过平台工具操作实验室照常可用。外部 runtime 自动驱动完整 Suite、受信过程证据与记分板写入属于后续范围，须有明确规格与验收；本次导入不声称完成这些能力。

## 5. 评估集

### 5.1 结构

```yaml
id: suite/lab-ops-basic
layer: agent                       # agent | embodied
scene: lab-demo@1                  # 题目运行的场景版本
runsPerCase: 3
cases:
  - id: centrifuge-two
    statement: statements/centrifuge-two.md      # 公开题面，交给被测对象
    grading: grading/centrifuge-two.yaml         # 私有评分条件
    split: train                                  # train | test
```

- **Agent 评估集**：题目是实验室操作任务。评分由平台依据**最终状态**（条件语言，如试管位置）与**过程指标**（被拒绝的高风险动作数、步数、用时、是否提交了操作计划）加权计算，满分 100。可选的 LLM 评审只用于无法从状态判定的项，并单独标注。
- **具身评估集**：训练规格 §7 的评估集，增加 `split` 与私有初始状态种子。分数为成功率。
- `split: test` 的题目或回合只在申请提升时运行并揭示；其余时间对 Optimizer 不可见。

### 5.2 基线

Builder 建好评估集后，对当前对象跑一次完整评估，记为**正式基线**，成为第一个 Reference。没有正式基线就不能开始优化。

## 6. 评估与记分板

### 6.1 记分板

每个评估集一份追加写入的记分板 `scoreboard.jsonl`。每条记录：

```json
{"suite": "suite/lab-ops-basic", "subject": "agent/lab-operator", "version": 4,
 "key": {"model": "anthropic/claude-opus-5-5", "thinking": "high"},
 "split": "train", "score": 88.1, "runs": 3, "ci95": null,
 "cost": {"llmUsd": 4.61, "tokens": 1830000, "gpuHours": 0},
 "durationSeconds": 1290, "decision": "accepted", "plan": "plan-0021",
 "traces": ["trace-..."], "at": "2026-10-18T10:02:00Z"}
```

- `decision` ∈ `baseline / accepted / rejected / test`。落选的 Candidate 也写入（与 PenguinHarness 不同），以便实验记录引用。
- 记分板只由平台写入；Optimizer 与 Evaluator 不能直接写。

### 6.2 可比较条件

| 层 | 只有以下相同时分数才可比较 |
|---|---|
| Agent | 评估集版本、模型、思考等级 |
| 具身 | 评估集版本、场景版本、Newton 版本、Embodiment |

界面把可比较条件显示为记录旁的标签；条件不同的记录不画在同一条曲线上。

### 6.3 执行

- 两层的评估都在平台仿真会话中顺序运行，占用单活跃会话（D-12）；评估期间界面显示"评估中"，交互操作排队。
- 每个 Case × run 是一次录制会话，录制 ID 写入记分板。

## 7. 接受规则

| 层 | 规则 |
|---|---|
| 具身 | Candidate 成功率的 Wilson 95% 置信区间下界 > Reference 的点估计（训练规格 §8.8） |
| Agent | Candidate 在 train 题上的平均分**严格高于** Reference，且每题至少 3 次运行 |

- 满足规则的 Candidate 才能申请提升；申请提升时运行 test 集，结果随审批卡片展示。test 分数低于 Reference 的 test 分数时，审批卡片标红，由人决定。
- 接受后 Candidate 成为新的 Reference；Harness 或 Controller 版本加一。

## 8. 角色与信息隔离

| 角色 | 由谁承担 | 读取 | 不得读取 |
|---|---|---|---|
| **Builder** | 平台 skill `lab-eval-build` | 全部题目与评分条件、被测对象 | — |
| **Optimizer** | 平台 skill `lab-training-plan` + `lab-train-evaluate`（具身）/ `lab-harness-optimize`（Agent） | 公开题面、train 记分板、train 录制与 Trace、实验记录、被测对象当前版本 | 私有评分条件、test 题目与结果、种子 |
| **Evaluator** | 平台代码（状态判定）；可选 LLM 评审 | 评分条件、本次 Trace 与状态 | 其他对象、凭据 |
| **Target** | 被测 Agent 或 Controller | 题面 | 评分条件 |

- 隔离靠平台 API 的访问控制加 skill 约定：私有评分条件与 test 集不经任何对 Optimizer 开放的 CLI / MCP 命令返回；Agent 运行器的工作目录里不放评分文件。
- 每次工具调用都在 Trace 中。发现私有信息进入 Optimizer 上下文时，回滚正在测试的 Candidate，计划标记为"污染"并停止。
- 人仍在关口审批：训练计划、提升、超预算（素材规格 §10.4）。

## 9. RSI 工具包

- v1 只有 **Default 工具包**，两层通用：读实验记录与记分板 → 诊断 → 一个可证伪假设 → 一处有边界的改动 → 以 Plan 提交并审批 → 评估 → 按接受规则保留或回滚 → 写入实验记录。
- Agent 层的"一处改动"限于：`AGENTS.md` 中的一段行为指引、一个 Agent 自有 skill、Harness 配置中允许修改的字段。不改模型与思考等级。
- 工具包接口：一个平台 skill + 本规格的 Plan / 评估 / 记分板协议。以后的算法（Eureka 式奖励搜索、DAgger、脚本程序 → 示范 → 行为克隆、OPRO / ACE 式 Harness 优化）以新 skill 接入，不改平台代码。

## 10. 实验记录与闭环预算

训练规格 §8.7、§8.9 扩展到两层：实验记录按"改进目标"组织（如 `goal/fr3-pick-tube`、`goal/lab-ops-basic`），闭环预算增加 LLM 成本上限（美元，按价格表估算）。

## 11. 成本中心

#44 首版 runtime 来源和幂等用量口径见 [Runtime records integration A](LAB_AGENT_RUNTIME_RECORDS_SPEC_V1.md)。
直接保留 runtime 报告的用量、成本与来源，缺失为未知；原价格表不作为导入门槛，也不与 runtime 成本重复相加。

### 11.1 用量记录

| 类型 | 记录 | 来源 |
|---|---|---|
| LLM | 原生会话/捕获身份、模型、报告的输入 / 输出 / 缓存 Token、请求与 USD 成本；可选显式 Agent/Harness/Plan/Goal | 支持格式的 runtime 记录导入；保留来源标签 |
| GPU | 节点、作业、GPU 秒、显存峰值、所属 Plan | 节点心跳 |
| 机器人 | 真机使用时长（预留） | — |

### 11.2 价格与汇总

- 价格表 `prices.yaml` 保留既有用途，GPU 账本沿用入账价格版本。Runtime 导入直接使用其报告成本并标注 `runtime-reported` 或 `runtime-estimate`，不重复套用价格；缺失金额保持未知，明确零保留为零。
- 汇总维度：时间范围（最近一小时 / 一天 / 7 天 / 30 天 / 自定义）、Agent、模型、节点、改进目标、Plan。
- 异常面板：失败的 LLM 请求、失败的作业、超时的评估；可标记为已读。

R1 的 GPU 账本与 `chargedGpuHours` 在同一次 Job 状态提交中持久化：仅累计计数增加时记增量，
重复报告不重复计费，CPU Job 的 GPU 秒为 0。每笔用量归属 Job 固定的 Plan 修订与目标，
Agent 维度表示发起该 Job 的 Agent；模型维度预留给 LLM 用量，不能以训练算法冒充模型用量。
LLM 指标只采用原生记录提供的字段；缺失请求、Token、成功率和金额返回 `null`，
不从 GPU 作业、turn 数或消息文本推算。Runtime 成本按首次导入时间分桶，明确 `timeBasis: importedAt`，不冒充原生执行时间。

汇总返回 GPU 累计秒、已上报秒、未确认的保守秒、历史累计秒与未定价秒。节点失联时沿用
作业预算的保守计数，并明确标为估计；之后的有效报告可确认同一累计前缀而不重复入账。
升级前的累计计数保留为历史来源，不伪造心跳；原显存报告缺少观测时间时保持未知，不能改记为重启时刻。
Job 状态无法持久化时成本读取返回 503，不公开尚未提交的累计值。

R1 时间桶按 UTC：`1h` 为 5 分钟桶、`1d` 为小时桶、`7d` / `30d` 为日桶，
自定义不超过 92 天为日桶，更长为周桶；自定义起止必须带时区。GPU 增量按相邻网关观测区间
与时间桶的重叠比例分配，总和保持不变；这不是逐秒物理采样。已知排队区间排除在用量区间外，
初始边界不早于已分配租约；未收到新报告不会仅因一次零计数保存而推进观测时间。

每笔增量固定入账时的人维护价格版本与费率，后续改价仅影响新用量，不改写旧估算。
没有价格的部分保持 `null` 金额与 `*` 标记，同时单列已定价部分；配置的零费率仍为已知零金额。
旧用量没有保留原价格时保持未定价。价格文件不可用不会破坏心跳或 GPU 租约，当前增量降级为未定价并带原因。
异常列表仅投影公开的作业与 `train` 评估，不暴露尚未公开的 `test` 评估；操作者可幂等标记已读，
状态持久化并记录操作者和时间。Web 侧栏显示全局未读点，筛选与时间范围保存在 URL 和本地偏好中。

## 12. 资源

| 标签页 | 内容 |
|---|---|
| **算力** | 计算节点、GPU 利用率与显存、租约、队列（训练规格 §5.2） |
| **模型** | LLM 供应商与模型表 `(provider, model_id)`、默认思考等级、凭据名（值在 Vault）、健康度与延迟、价格；下方是 Robot Model 目录 |
| **机器人** | 每个 Embodiment 一张卡片：分层展示当前 Controller（Robot Model → 策略版本 → 设备程序 → 动作空间与频率 → 观测 → 评估集成绩）、版本历史、所在场景或实况 |

## 13. HTTP API、CLI、MCP 与平台 skill

### 13.0 HTTP API

路径前缀 `/api/v1`。

| Method | Path | 结果与错误 |
|---|---|---|
| GET | `/agents`、`/agents/{id}` | Agent、默认模型与思考等级、当前 Reference 版本 |
| GET | `/agents/{id}/versions`、`/agents/{id}/versions/{v}?against={u}` | Harness 版本表；带 `against` 时返回两版本差异 |
| POST | `/agents`、`/agents/{id}/snapshots` | 从快照创建 Agent、导入快照 |
| POST | `/runtime-sessions/import` | 导入明确的 `content` 或 `records`、`format` 与可选关联；不接受服务端文件路径 |
| GET | `/runtime-sessions`、`/runtime-sessions/{id}` | 已导入会话列表、详情与规范化证据链接 |
| GET | `/suites`、`/suites/{id}` | 评估集元数据与公开题面；**私有评分条件与 test 题目从不由 API 返回** |
| GET | `/suites/{id}/scoreboard?subject=` | 记分板记录与可比较条件；比较不同条件的记录返回 `not_comparable` |
| POST | `/suites/{id}/runs` | `{subject, version, split}`；`split: test` 只能由提升流程触发 |
| GET | `/embodiments`、`/embodiments/{id}` | 本体、类型（sim / real）、绑定、当前 Controller |
| GET | `/controllers/{id}`、`/controllers/{id}/versions` | Controller 组合与版本历史 |
| GET | `/robot-models` | Robot Model 目录 |
| GET | `/cost/summary?range=&agent=&model=` | 今日、近 7 天、累计（或所选范围）的 Token、请求、LLM 估算、GPU 小时 |
| GET | `/cost/series?range=&agent=&model=` | 按时间桶的请求、失败、输入 / 输出 / 缓存 Token、LLM 估算；桶粒度由范围决定 |
| GET | `/cost/breakdown?by=goal\|plan\|node&range=` | 分摊表 |
| GET | `/cost/anomalies`、POST `/cost/anomalies/{id}/read` | 异常列表与已读状态 |
| GET / PUT | `/prices` | 价格表；修改记录操作者 |
| GET / POST | `/models` | LLM 供应商与模型表、健康度与延迟 |
| GET | `/vault` | 只返回凭据名称；写入只能通过本机交互式 `lw vault set` |

成本三个用量查询还支持 `node`、`goal`、`plan`（`planId@revision`），自定义范围用
`range=custom&start=<带时区 ISO 时间>&end=<带时区 ISO 时间>`；`summary` 同时返回所选范围与固定三张摘要卡。
`breakdown` 的 `by` 支持 `agent / model / node / goal / plan / job`；`series` 返回窗口与各桶边界。
`lw cost --since <range> --by <维度>` 与 MCP `cost_query` 共享命令声明和这些只读接口，
`--view series|anomalies` 读取时间序列或异常，不提供审批、凭据或付费执行能力。

私有评分条件只存在于评估集包文件中，由 Builder 直接编辑包并重新导入；平台运行评估时在 Evaluator 进程内读取。

### 13.1 CLI

```text
lw agent ls | show <id> | snapshot <id>
lw runtime import <capture.jsonl> --format claude-code-stream-json
lw runtime ingest <records.json> --format claude-code-stream-json
lw runtime ls
lw runtime show <id>
lw harness diff <id> <vA> <vB>
lw suite ls | show <id> | run <id> --subject <agent|controller> [--split train]
lw score ls --suite <id> [--subject <id>]
lw cost [--since 7d] [--by agent|model|node|goal|plan]
lw model ls | add <provider>/<model_id> | default <ref>
lw vault set <name> | ls
lw robot ls | show <embodiment>
lw robot-model ls | show <id>
lw controller show <id>[@N]
```

- 全部支持 `--json`。MCP 镜像为通用工具：`agent_*`、`runtime_import/list/show`、`suite_*`、`score_ls`、`cost_query`、`model_ls`、`robot_*`、`controller_show`。不提供 `vault_*` 与审批工具。
- 新增平台 skill：`lab-eval-build`（Builder）、`lab-harness-optimize`（Agent 层 Optimizer）。`lab-training-plan` 与 `lab-train-evaluate` 改为引用本规格的角色与隔离规则。

## 14. 前端

侧栏（可折叠）：概览 | 实况 | 素材库 | 场景库 | 计划 | 训练 | **评估** | **成本** | **资源** | 待审批 | 会话 | 录制。

- **评估**：评估集列表（分层标签）；评估集详情含记分板、可比较条件标签、版本分数曲线、按题或按阶段明细、锁定的 test 行。
- **成本**：标题"成本与统计"；筛选；今日 / 近 7 天 / 累计三张卡片；各 Agent 请求与成功率、Token 与缓存命中率、成本变化、GPU 小时四类图；按改进目标与 Plan 的拆分；异常面板。
- **资源**：算力 / 模型 / 机器人三个标签页（§12）。
- **训练**：增加层切换，分别显示 Agent 层与具身层的 RSI 看板、Reference / Candidate、实验记录与预算。

## 15. 里程碑

```text
T2 ──► R1 评估中心 + 记分板 + 成本中心（GPU）
A1、L2 ──► R2 runtime 记录接入 + Agent 评估集 + LLM 成本 ──► 后续完整评估交接 ──► R3 Agent 层一圈 RSI
A2 ──► R4 资源：Embodiment、Controller、Robot Model 目录
```

**R1**：具身评估集加 train / test；记分板与可比较条件；评估页；成本中心（GPU 小时）。验收：两版策略在同一评估集上可比较，test 只在申请提升时运行；GPU 小时与作业心跳一致。

**R2 / #44 首版 A**：已有 runtime 记录的导入、持久化、CLI/MCP/Web 读回与报告用量/成本；`suite/lab-ops-basic` 的公开题面和既有 Agent/Harness 元数据继续可读。首版验收以 [runtime records integration](LAB_AGENT_RUNTIME_RECORDS_SPEC_V1.md) 为准。全题每题 3 次的真实评估、可信记分板与自动交接仍为后续 B，不由导入记录代替，也不因 A 交付而关闭。

**R3**：`lab-harness-optimize`、Harness 快照与回滚、Agent 层接受规则。验收：一轮 Candidate 被接受、一轮被拒绝并回滚；实验记录与成本中心对得上。

**R4**：Embodiment 与 Controller 文件、机器人卡片、Robot Model 目录（含至少 3 个只登记的 VLA 条目与 1 个可运行条目）。验收：提升策略后，机器人卡片显示新的 Controller 版本与评估成绩。

## 16. 待决策清单

| ID | 决策 | 默认值 | 状态 |
|---|---|---|---|
| R-01 | 两层改进对象 | Agent（改 Harness）+ Controller（改控制组合） | 已定 |
| R-02 | Agent 运行 | 外部 runtime 经 skills/CLI/MCP 接入；平台首版导入已有记录 | A 已定，完整评估另行交付 |
| R-03 | Agent 层接受规则 | train 平均分严格更高，每题 ≥ 3 次 | 已定 |
| R-04 | 具身层接受规则 | Wilson 95% 下界 > Reference 点估计 | 已定 |
| R-05 | 落选 Candidate 是否进记分板 | 进，标 `rejected` | 默认生效 |
| R-06 | VLA | 只登记，v1 不推理 | 已定 |
| R-07 | 评估集 test 部分 | 只在申请提升时运行与揭示 | 已定 |
| R-08 | 凭据存储 | 本机 Vault 文件，权限 0600，只以环境变量注入 | 默认生效 |
| R-09 | RSI 算法 | v1 只有 Default 工具包 | 已定 |
| R-10 | 人工关口在评估中的处理 | 自动驳回并计入评分 | 默认生效 |

## 17. 参考

| 参考 | 借鉴点 |
|---|---|
| PenguinHarness（github.com/Prism-Shadow/penguin-harness，文档"核心概念""自我进化""评估中心""成本中心"） | 模型 × Harness；Agent State 即文件；版本与快照；Reference / Candidate 严格提升才保留；Builder / Target / Evaluator / Optimizer 角色均为 skill；信息隔离靠约定加 Trace 审计；记分板记录分数、成本、耗时；可比较条件；成本中心的维度与异常面板；RSI 工具包作为插件 |
| 训练规格 §15 所列论文 | 具身层的改进对象与方法 |
