# Lab Live Operations

> **实时信息、告警与操作计划 · Technical Specification v1.0**
> 日期：2026-10-18
> 状态：Approved direction，待实施（未决项集中在 §14，均有默认值）
> 前置：[Lab Information Foundation](LAB_INFORMATION_FOUNDATION_SPEC_V1.md)（属性、动作管线、事件、录制）、[Lab Assets and Scenes](LAB_ASSETS_AND_SCENES_SPEC_V1.md)（北极星 §1，agent 接口与人工关口 §10，录制格式 v2 §8.3）。
> 姊妹规格：[Lab Training](LAB_TRAINING_SPEC_V1.md)（`training` 类型的 Plan）。
> 术语：以 [GLOSSARY.md](../../GLOSSARY.md) 为准。
> 总览与验收：[Lab RSI Platform PRD](LAB_RSI_PLATFORM_PRD_V1.md)（用户故事、交付顺序、测试接缝、错误码）；界面：[Lab Web UI](LAB_WEB_UI_SPEC_V1.md)。

## 0. 一句话定义

让人和 agent 随时知道实验室**此刻怎样、刚才怎样、哪里不对**，并在动手之前把**要做什么**摆出来：物联网设备的实时读数与历史、声明式告警与 agent 收件箱，以及像 coding agent plan 模式那样可见、可追踪的操作计划和机械臂路径预览。

## 1. 目标与范围

### 1.1 目标体验

1. 打开"实况"页：看到培养箱、生物安全柜、环境传感器的实时读数，每个数值都附 1 小时迷你曲线。2 号培养箱温度 38.6 °C，标红告警"温度 > 38 °C 持续 60 s"。
2. Claude Code 开始新一轮工作，先执行 `lw inbox --since <cursor>`，看到这条告警；再用 `lw history /Real/Incubator2 temperature --since 1h --every 1m` 看趋势，向人报告"温度从 10:31 开始缓慢上升"。
3. 人说"把两支样品离心 30 秒"。agent 用 `lw plan create` 提交一份 7 步操作计划。浏览器的会话页在视口旁显示步骤清单；选中第 1 步时，视口里出现机械臂 TCP 的虚线轨迹和目标处的半透明幽灵臂。
4. agent 逐步执行 `lw do ... --plan plan-0012 --step N`，清单实时打勾。执行中发现需要先检查转子平衡，于是提交修订 r2，界面显示 r1 → r2 的差异。
5. 会话结束后回放录制，计划清单与告警随时间轴一起重现。

### 1.2 包含

- 物联网设备类型：培养箱、生物安全柜、环境传感器，以及它们的 virtual 程序。
- 属性的 `staleAfterSeconds` 与"过期"状态。
- **实验室实况（Live Lab）**：常驻的真实设备宿主、Device Host、MQTT 程序、本地模拟设备。
- 告警规则、告警生命周期与事件。
- 遥测历史：会话录制与实况录制上的历史查询。
- agent 收件箱 `lw inbox`。
- `operation` 类型的 Plan：结构、修订、步骤状态、`--plan --step` 关联、静态校验、审批规则。
- 机械臂步骤的路径预览。
- 协议 2.1：新增 `entity.alert`、`plan.update` 两种可靠消息。
- Web："实况"页、"计划"页、会话页的计划面板、3D 读数浮标、告警标记。

### 1.3 不包含

- 实验流程编排引擎。计划由 agent 逐步执行，平台只记录与追踪。
- 预测性预演：克隆仿真分支试跑、推演前置条件。
- 数据库与时序数据库。
- MQTT 以外的协议（Modbus、OPC UA、厂商 HTTP），以及具体硬件驱动。
- 策略驱动步骤的轨迹预览。

## 2. 与已有工作的关系

| 已有内容 | 本规格中的处理 |
|---|---|
| 信息规格 §1.3 "真实设备驱动不包含，MQTT 放在后续" | 本规格实现 Device Host 与 MQTT 程序，先接模拟设备 |
| 信息规格 §13.5 真实设备（后续） | 落地为实验室实况；`/Real/...` 与 `/World/...` 永不共用身份 |
| 信息规格 §4.7 Event | 不变；告警是新的 `entity.alert` 消息，不复用 `entity.event` |
| `GET /events?after=` 与 `lw watch` | 保留；`lw inbox` 是面向 agent 的筛选视图 |
| D-05 审批策略 | 扩展到 Plan：仿真操作计划自动批准，含 `/Real` 或高风险动作的计划需要审批 |
| 录制格式 v2（素材规格 §8.3） | v2 同时定义 `kind`、属性索引附件，以及新消息的写入 |
| 协议 v2 | 语义 schema 升为 2.1，wire major 保持整数 2；元数据/health 标记 minor 1，新增消息及属性元数据；2.0 录制不受影响 |

## 3. 物联网设备类型

### 3.1 首批类型

| 类型 | 属性 | 动作 | 默认告警 |
|---|---|---|---|
| `incubator/v1` | `temperatureC`、`co2Percent`、`humidityPercent`、`doorOpen`、`setpointC` | `setSetpoint(temperatureC)` | 温度偏离设定值 > 1.0 °C 持续 60 s → critical；门开 > 120 s → warning |
| `biosafety-cabinet/v1` | `inflowMs`、`sashHeightCm`、`uvOn`、`blowerOn` | `setBlower(on)`、`setUv(on)` | 风速 < 0.4 m/s 持续 30 s → critical；UV 开且前窗打开 → critical |
| `env-sensor/v1` | `temperatureC`、`humidityPercent` | 无 | 湿度 > 70% 持续 600 s → warning |

`setUv` 与 `setSetpoint` 声明为 `risk: high`。

### 3.2 virtual 程序

- 信号模型：一阶滞后趋向设定值，加有界噪声；门开时温度和 CO₂ 按指数衰减；随机种子来自会话，回放与重跑一致。
- 生物安全柜仿真孪生的 `sashHeightCm` 可与场景中前窗关节联动，绑定配置写在模板里。
- 这些类型可以通过绑定模板放进场景（素材规格 §5.2），例如 `fr3-lab` 里的生物安全柜从"无类型"变为实现 `biosafety-cabinet/v1`。

### 3.3 过期

属性定义新增可选的 `staleAfterSeconds`。宿主超过该时长没有写入新值时，状态中该属性带 `stale: true`，并发出一次 `entity.observations` 更新。界面显示为灰色并标"过期"；条件求值把过期值视为"未知"，前置条件因此失败，提示"读数已过期"。

## 4. 实验室实况

### 4.1 定位

- 实验室实况是常驻的**真实设备宿主**，随网关启动，与仿真会话无关，也不受单活跃会话限制。
- 所有实况实体的 `entityKey` 以 `/Real/` 开头。场景里可以有同类设备的仿真孪生（`/World/...`），两者永远不共用身份。agent 先在孪生上演练，再对真实设备执行。
- 实况没有物理，也没有空间流；3D 显示可借用一个场景版本作为布局底图（§4.3）。

### 4.2 Device Host 与 MQTT

- Device Host 在网关进程内运行，按墙钟推进，使用信息规格 §5.1 的同一个 Program 接口。
- 新增程序 `*.mqtt`（如 `incubator.mqtt`）：订阅设备的遥测 topic，把载荷映射为属性；把动作映射为命令 topic，并等待确认 topic。映射写在绑定配置里：

```yaml
# devices/live/lab.yaml
live: main-lab
broker: {url: mqtt://localhost:1883}
layout: fr3-lab@2                     # 可选：3D 底图
entities:
  - key: /Real/Incubator2
    type: incubator/v1
    program: incubator.mqtt
    placeAt: /World/Lab/Incubator     # 可选：在底图中的位置
    config:
      telemetry: {topic: lab/incubator2/telemetry, map: {temperatureC: $.temp, co2Percent: $.co2}}
      commands: {setSetpoint: {topic: lab/incubator2/cmd, ack: lab/incubator2/ack, timeoutSeconds: 5}}
```

- 本地开发：`tools/mock_mqtt_device.py` 模拟 3 台设备，可注入故障（升温、断线、风速下降）；broker 使用本机 mosquitto。CI 使用进程内的假 broker。
- 连接断开时，各属性按 `staleAfterSeconds` 进入过期状态；动作返回 `device_unavailable`。

### 4.3 动作与审批

- 实况实体的动作走同一条动作管线。作用于 `/Real/...` 的 `risk: high` 动作一律进入人工审批（D-05 的 `require`），不论调用者是谁。
- 3D 底图里，`placeAt` 指定设备的显示位置，读数浮标就画在那里。没有底图时，"实况"页只显示设备卡片。

## 5. 告警

### 5.1 规则

告警规则用信息规格 §4.3 的条件语言，加上持续时间与严重度。规则可以写在 EntityType 里作为默认值，再由场景或实况绑定覆盖或补充：

```yaml
alerts:
  tempDeviation:
    when: {all: [{property: temperatureC, deviatesFrom: setpointC, by: 1.0}]}
    forSeconds: 60
    severity: critical               # info | warning | critical
    message: 温度偏离设定值超过 1 °C
    hint: 检查门是否关闭，再查看 lw history
```

条件语言新增两个比较运算符：`deviatesFrom`（与另一属性的差值）和 `stale`（属性已过期）。仍然是结构化 all-of，不支持表达式（D-07）。

告警声明属于作者配置与内部编译态；公开会话 Catalog、entity definition/binding 和录制 Catalog 继续使用已发布的 2.1 字段形状，不新增 `alerts` 字段。素材/场景库检查仍可返回原作者声明。Runner 与网关各自解析同一场景包，并以内部 JSON 规则快照核对一致性；不在进程间传递编译后的闭包。

### 5.2 生命周期

```text
raised ──► acknowledged ──► cleared
   └──────────────────────► cleared
```

- 条件持续成立达到 `forSeconds` 时 `raised`；条件不再成立时 `cleared`。
- 人或 agent 都可以确认（acknowledge），确认时记录操作者与备注。确认不是人工关口。
- 每次状态变化发出一条 `entity.alert` 消息：`{entityKey, alertId, rule, severity, state, message, hint, value, since, actor?}`。
- 可靠消息复用 `sceneRevision / epoch / simTime / sampleTimeMs` 信封；`since` 是条件首次成立的非负仿真秒数。`actor` 复用现有 Actor。确认备注属于告警 API 的实现契约，不另外发明 actor 形状。
- 已发布的 2.1 `EntityAlert` 字段保持不变。确认同时写入一条既有 `entity.event`：`event: alert.acknowledged`，`data: {alertId, actor, note}`；它是网关的确认审计，不要求设备程序声明事件。HTTP episode 的 `acknowledgement` 保留首次操作者、备注与时间，重复确认不覆盖也不重发。
- 普通比较中任一依赖读数缺失或过期时，条件为未知：未触发候选重新计时，已有告警保持，直到已知条件为假才 `cleared`。显式 `stale` 条件照常求值。
- reset、reload、会话结束或源失败会结束当前 episode 上下文，HTTP 以 `closedContext` 记录原因并将其排除于 active；这不表示设备恢复，不伪造 `cleared`。旧上下文或已 cleared 的告警不可再确认。再次持续触发生成新的 `alertId`。

## 6. 遥测历史

- **会话**：历史来自该会话的录制。
- **实况**：实况的可靠消息持续写入一个**永不结束的录制**，按天分段，保留 7 天（`SPATIAL_LIVE_RETENTION_DAYS`）。
- 录制格式 v2（与素材规格 §8.3 同一版本）定义：
  - `recording.json` 的 `kind: session | live`；实况录制的 `sceneVersion` 为 `null`；
  - 每个段可带属性索引 `seg-NNNNNN.index.json`，记录各 `(entityKey, property)` 在该段内的时间范围与记录偏移。缺少索引时读取器回退为顺序扫描。
- 查询：`GET /history?key=&property=&since=&until=&every=&agg=mean|last|min|max`，返回 `[[tMs, value], ...]`，每次最多 2,000 个点。
- CLI：`lw history <key> <property> --since 1h [--every 1m] [--agg mean]`。key 以 `/Real/` 开头时查实况，否则查活跃会话；`--recording <id>` 查指定录制。

## 7. agent 收件箱

agent 获取信息的四种方式：

| 方式 | 命令 | 何时用 |
|---|---|---|
| 当前值 | `lw get`（已有） | 做决定前 |
| 等条件 | `lw wait`（已有） | 等离心结束、等温度到位 |
| 历史 | `lw history` | 分析趋势、诊断失败 |
| 收件箱 | `lw inbox --since <cursor>` | 每轮开始，以及长时间等待之后 |

- `GET /inbox?after=<cursor>&kinds=` 合并实况与活跃会话中**需要 agent 关注**的消息：告警的 raised / cleared、待审批与审批结果、计划状态变化、失败的调用、作业状态变化（训练规格）。返回新的 `cursor`。
- cursor 不透明，跨网关重启有效（实况录制可以重建）。首次调用不带 cursor 时，返回当前所有活跃告警、待审批事项与最近 20 条消息。
- MCP 提供 `inbox_poll`。MCP server 连接时的 `instructions` 中列出当前活跃告警数。
- 平台 skill `lab-operate` 规定：每轮工作开始先查 inbox；发现 critical 告警时先向人报告，不自行处理 `/Real` 设备。在 Claude Code 中，需要长时间等待时用后台 `lw watch --until <条件或告警>`，不要轮询。

## 8. 操作计划

### 8.1 结构

Plan 是通用对象，分 `operation` 和 `training` 两种（training 见训练规格 §4）。操作计划示例：

```yaml
id: plan-0012
kind: operation
title: 离心两支样品
target: session                      # session | live
revision: 2
steps:
  - {id: 1, do: {entity: /World/Lab/RobotA, action: pickPlace, params: {from: rack.A1, to: centrifuge.slot1}},
     expect: {entity: /World/Lab/Tubes/Tube01, property: location, equals: /World/Lab/Centrifuge/Rotor/Slot1}}
  - {id: 2, do: {entity: /World/Lab/RobotA, action: pickPlace, params: {from: rack.A2, to: centrifuge.slot3}},
     expect: {entity: /World/Lab/Centrifuge, property: slots, equals: [/World/Lab/Tubes/Tube01, null, /World/Lab/Tubes/Tube02, null]}}
  - {id: 3, do: {entity: /World/Lab/RobotA, action: home}}
  - {id: 4, do: {entity: /World/Lab/Centrifuge, action: closeLid}}
  - {id: 5, do: {entity: /World/Lab/Centrifuge, action: start, params: {rpm: 3000, durationSeconds: 30}},
     expect: {entity: /World/Lab/Centrifuge, property: phase, equals: idle}}
  - {id: 6, do: {entity: /World/Lab/Centrifuge, action: openLid}}
```

- 步骤类型：`do`（动作调用）、`wait`（等待条件）、`check`（断言属性）。`do` 可带 `expect`。
- 步骤状态：`pending → running → done | failed | skipped`。
- 计划状态：`draft → submitted → approved → running → completed | abandoned`；需要审批的计划在 `submitted` 等待人工。
- `plan.update` 复用可靠消息信封，以 `update` 判别：`full` 携带 `plan: OperationPlan`；`step` 携带 `planId / revision / stepId / status`。revision 与步骤 id 为正整数。步骤保留示例中的直接条件原子，不要求额外的 `all` 包装。消息语法可先发布，计划执行追踪/API 按 L2 落地；动作的可选 planId/stepId 在关联功能实现前仍由现有请求边界拒绝。

### 8.2 执行与关联

- 平台**不执行**计划。agent 逐步执行：
  - `lw plan run <id> --revision <n>` 在本地客户端复用同一 HTTP 连接，按计划顺序调用既有单步 API；每步核验调用结果和 `expect`，遇到失败、超时或未完成状态即停止，不自动重试。网关不托管执行循环。
  - `lw do <key> <action> ... --plan plan-0012 --step 4`：调用带上计划与步骤，平台把调用状态映射为步骤状态；有 `expect` 时，调用成功且条件成立才算 `done`；
  - `lw wait ... --plan plan-0012 --step 6`、`lw check <key> <property> <op> <value> --plan plan-0012 --step 3`。
- 不按顺序执行、或执行不在计划里的动作都不会被阻止，但界面会标出"计划外"或"跳步"。
- agent 改计划时用 `lw plan revise plan-0012 plan.yaml`，生成新修订；已完成的步骤保留状态，界面显示修订差异。
- 每次计划或步骤变化发出一条 `plan.update` 可靠消息，写入录制，回放时计划面板随时间轴重现。

### 8.3 校验与审批

- 提交时做静态校验：实体与动作存在、参数符合 schema、`check` / `wait` / `expect` 的条件与属性类型匹配。失败返回 `invalid_plan`，并指出具体步骤。
- 不做预测性校验。前置条件在执行时由动作管线照常判定。
- 审批：
  - `target: session` 且不含 `risk: high` 动作：自动批准；
  - 含 `/Real/...` 实体的 `do` 步骤，或含 `risk: high` 的动作：产生"操作计划审批"，属于人工关口（素材规格 §10.4 增加这一项）。批准后，计划内的高风险调用不再逐个审批；计划外的仍逐个审批。

### 8.4 路径预览

- Program 接口新增可选方法 `preview(action, params) -> Preview`，由宿主调用：
  - `robot_arm.newton` / `robot_arm.virtual`：用 `trajectory.plan_trajectory` 规划关节轨迹，再经正运动学得到 TCP 折线（最多 64 个点）和目标关节角；
  - `robot_arm.policy`：只返回目标站点位姿，标记 `policyDriven: true`；
  - 不实现 `preview` 的程序：没有预览。
- `GET /plans/{id}/steps/{n}/preview` 在需要时计算，结果按 `(计划修订, 步骤, 当前关节状态)` 缓存。
- Web：选中或悬停机械臂步骤时，视口画出 TCP 虚线折线，并在目标关节角处画一只半透明幽灵臂（复用机器人视觉，材质改为半透明），标注"预览 · 示教轨迹"或"由策略执行 · 仅显示目标"。

## 9. 协议 2.1

| 消息 | 方向 | 可靠 | 内容 |
|---|---|---|---|
| `entity.alert` | server → client | 是 | §5.2 |
| `plan.update` | server → client | 是 | 计划全量（修订变化时）或步骤增量 |

- wire `protocolVersion` 表示主版本，Manifest、ready、subscribe、pose 与 frame 继续为整数 `2`；语义 schema 升为 2.1（schema ID `/protocol/v2.1/schema.json`）。仅录制元数据与 health 增加可选 `protocolMinorVersion: 1`；缺省表示 2.0，不向旧 Manifest 增加字段。
- 新增消息类型及可忽略的属性元数据；客户端忽略未知类型（v1.2 已有约定）。
- 2.1 的读取器能播放 2.0 录制；2.0 的读取器播放 2.1 录制时跳过新消息。
- 原始属性 `values` 保持声明的标量/数组/对象形状。`entity.snapshot` 的 `meta[property]` 可带 `stale: true`；`entity.observations.changes[]` 可带按属性的 `meta`，承载相同的 freshness。缺省为 fresh，过期转换保留最后真实采样时间；新读取器在段首快照与 seek 后保留 freshness。
- 旧普通录制读取器维持空间与原始属性回放/seek，可忽略新字段和消息，无需理解过期、告警或计划。旧 Pydantic 严格生产者模型不接受未来字段，它们不是普通文件读取路径。录制 metadata format 2、LWLG codec 1、NSYN codec 1 不变，已发布 format 1/2 夹具不重写。
- 新会话录制可保存实际使用的可选 `seed`（0 至 9,007,199,254,740,991 的整数）；未知/历史 seed 保持缺省，不推断为 42。seed 只在录制元数据/会话选项中，不进入旧 Manifest/SourceInfo。

## 10. HTTP API 与 CLI

| Method | Path | 结果 |
|---|---|---|
| GET | `/live/entities`、`/live/entity?key=`、`/live/state?keys=` | 实况实体，形状同会话版 |
| POST | `/live/actions` | 实况动作调用 |
| GET | `/history?...` | §6 |
| GET | `/alerts?state=&severity=` | 活跃与最近告警 |
| POST | `/alerts/{id}/ack` | 确认 |
| GET | `/inbox?after=&kinds=` | §7 |
| POST | `/plans` · `/plans/{id}/revisions` | 创建与修订计划 |
| GET | `/plans?kind=&status=` · `/plans/{id}` | 计划与步骤状态 |
| GET | `/plans/{id}/steps/{n}/preview` | §8.4 |

CLI 新增：

```text
lw history <key> <property> --since 1h [--every 1m] [--agg mean] [--recording ID]
lw alerts [--active]  |  lw alerts ack <id> [--note TEXT]
lw inbox [--since CURSOR] [--kinds alert,approval,plan,invocation,job]
lw check <key> <property> <op> <value> [--plan ID --step N]
lw plan create <plan.yaml>  |  lw plan revise <id> <plan.yaml>  |  lw plan show <id>  |  lw plan ls
lw do ... --plan ID --step N  |  lw wait ... --plan ID --step N
```

- key 以 `/Real/` 开头的命令自动路由到实况。
- `lw plan submit`（训练规格）与 `lw plan create` 是同一个命令组；`create` 用于操作计划，`submit` 用于训练计划。
- MCP 镜像为通用工具：`history_query`、`alerts_list`、`alert_ack`、`inbox_poll`、`plan_create`、`plan_revise`、`plan_show`；`invoke_action` 与 `wait_until` 增加可选的 `planId`、`stepId`。

## 11. 前端

- **导航**：概览 | 实况 | 素材库 | 场景库 | 计划 | 训练 | 待审批 | 会话 | 录制。告警数以徽标显示在"实况"上，待审批数显示在"待审批"上。
- **实况页**：设备卡片，或带布局底图的 3D 视图；每个数值属性附 1 小时迷你曲线；过期读数为灰色；告警列表可确认。
- **3D 读数浮标**：会话与实况视口中，在设备上方显示关键读数（离心机的阶段、转速、盖子状态；培养箱的温度），可整体开关。有告警的设备旁显示红点。
- **会话页计划面板**：视口旁显示当前操作计划的步骤清单、每步状态与关联调用、修订差异；选中机械臂步骤时显示路径预览。
- **计划页**：两种计划的列表与详情；训练计划详情见训练规格。
- 布局方向随 UI 原型结论确定（素材规格 §11.3、A-07）。

## 12. 里程碑

```text
A1 ─┬─► L1 传感器 + 告警 + 历史 + inbox ──► L3 实况 + Device Host + MQTT
    └─► L2 操作计划 + 路径预览
```

**L1 传感器、告警、历史与收件箱（仿真）**

验收：

1. `fr3-lab` 中的仿真培养箱与生物安全柜显示实时读数和迷你曲线。
2. 注入升温：60 s 后出现 critical 告警，`lw inbox` 能读到；恢复后告警变为 cleared。
3. `lw history` 返回最近 1 小时的降采样数据，结果与录制一致。
4. 过期读数在界面变灰，依赖它的前置条件以"读数已过期"失败。

**L2 操作计划与路径预览**

验收：

1. agent 经 MCP 创建信息规格 I2 流程的操作计划，逐步执行；浏览器清单与调用一一对应。
2. 中途修订后，界面显示差异，已完成的步骤保留状态。
3. 机械臂步骤显示 TCP 折线与幽灵臂，折线终点与示教站点距离 ≤ 10 mm。
4. 回放录制时，计划面板随时间轴重现。

**L3 实验室实况**

验收：

1. 网关启动后，实况连上本地 broker，显示 3 台模拟设备，无需打开任何会话。
2. 模拟设备断线：读数过期，动作返回 `device_unavailable`。
3. agent 对 `/Real/Incubator2` 调用 `setSetpoint`：产生审批；人批准前不执行。
4. 实况录制按天分段；`lw history` 能跨段查询；超过 7 天的段被清理。

## 13. 测试

| 类别 | 内容 |
|---|---|
| 单元 | 信号模型可复现；`deviatesFrom` / `stale` 条件；告警状态机与持续时间；历史降采样；计划校验与步骤状态映射；TCP 预览折线（纯函数） |
| 契约 | 协议 2.1 新消息的 Pydantic 与 TS 类型一致；2.0 与 2.1 录制互读 |
| 网关 | 进程内假 broker：遥测映射、命令确认与超时、断线过期；inbox 游标；`/Real` 高风险动作必审批 |
| CLI / MCP | 新命令与镜像工具；`--plan --step` 关联 |
| E2E | 告警出现与确认；计划面板打勾；路径预览出现 |

## 14. 待决策清单

| ID | 决策 | 默认值 | 状态 |
|---|---|---|---|
| L-01 | 真实设备接入 | Device Host + MQTT，先接模拟设备 | 已定 |
| L-02 | 实况与仿真身份 | `/Real/...` 与 `/World/...` 永不共用 | 已定 |
| L-03 | 历史存储 | 录制分段 + 属性索引；不用数据库 | 已定 |
| L-04 | 实况保留时长 | 7 天 | 默认生效 |
| L-05 | Plan 统一 | 一个 Plan 对象，`operation` / `training` 两类 | 已定 |
| L-06 | 计划执行 | agent 执行，平台追踪；不做编排引擎 | 已定 |
| L-07 | 路径预览 | 示教轨迹做 TCP 折线；策略只显示目标；不做预演 | 已定 |
| L-08 | 协议版本 | 2.1，只新增消息 | 默认生效 |
| L-09 | 历史查询上限 | 每次 2,000 个点 | 默认生效 |
| L-10 | broker | 本机 mosquitto；CI 用进程内假 broker | 默认生效 |
