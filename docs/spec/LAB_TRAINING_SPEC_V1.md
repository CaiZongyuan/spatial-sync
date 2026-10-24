# Lab Training

> **训练、评估与策略 · Technical Specification v1.0**
> 日期：2026-10-18
> 状态：Approved direction，待实施（未决项集中在 §14，均有默认值）
> 前置：[Lab Assets and Scenes](LAB_ASSETS_AND_SCENES_SPEC_V1.md)（北极星见其 §1，agent 接口与人工关口见其 §10）、[Lab Information Foundation](LAB_INFORMATION_FOUNDATION_SPEC_V1.md)。
> 参考实现：`D:\Projects\Robots\IsaacLabTutorial`（SO-101 药瓶抓放，Isaac Lab 3.0，`newton_mjwarp`，状态策略成功率 99.2%）。RSI 设计参考见 §15。
> 相关：[Lab Live Operations](LAB_LIVE_OPERATIONS_SPEC_V1.md) 定义通用的 Plan 对象与 `operation` 类型；本规格的训练计划是 `training` 类型的 Plan。[Lab RSI](LAB_RSI_SPEC_V1.md) 定义两层改进对象、评估中心、接受规则、角色与成本中心；本规格的 §7–§8 是其具身层的执行细节，框架冲突时以 RSI 规格为准。
> 术语：以 [GLOSSARY.md](../../GLOSSARY.md) 为准。
> 总览与验收：[Lab RSI Platform PRD](LAB_RSI_PLATFORM_PRD_V1.md)（用户故事、交付顺序、测试接缝、错误码）；界面：[Lab Web UI](LAB_WEB_UI_SPEC_V1.md)。

## 0. 一句话定义

agent 与人一起制定**训练计划**，经人批准后，平台在**计算节点**上以外部**作业**的形式运行 Isaac Lab 训练，实时显示**训练视图**；训练出的检查点在平台上**评估**，达标后成为**策略**，经人批准替换设备程序。这样 RSI 闭环完整转一圈。

## 1. 目标与范围

### 1.1 目标体验

1. 人在 Claude Code 里说"让 FR3 学会把试管放进离心机"。agent 按 `lab-training-plan` skill 逐轮提问：上一轮失败在哪、目标、算法、架构、预算。每题都给出推荐答案。
2. agent 用 `lw plan submit` 提交计划和完整问答记录。人在"待审批"页看到计划、问答记录和相对上一轮的差异，然后批准。
3. agent 在计划预算内启动 3 个 seed 的作业。训练页显示三个作业的进度和标题指标；点开任意一个，就能在 Three.js 里看到 4 个环境中的 FR3 在练习抓管。
4. 作业完成后，agent 运行 `lw eval run`：在 `fr3-lab@2` 上跑 100 个回合并全部录制，得到 87/100。agent 打开失败回合的录制，判断失败集中在插入阶段。
5. agent 申请把 `policy/fr3-pick-tube@3` 设为 `fr3-lab@2` 的默认程序，人批准。之后 `lw do /World/Lab/RobotA pickPlace from=rack.A1 to=centrifuge.slot1` 由策略执行，接口不变。

### 1.2 包含

- Task 包与版本（`lw task push`）。
- 训练计划：起草、提交、审批、预算。
- 作业：生命周期、计算节点、GPU 租约、作业契约、日志与 TensorBoard、标题指标。
- 训练视图：从训练进程把少量环境推到 Three.js。
- 评估：平台上的多回合录制评估。
- 策略版本、ONNX 导出、`robot_arm.policy` 程序、观测一致性测试、提升审批。
- CLI、MCP、两个平台 skill：`lab-training-plan`、`lab-train-evaluate`。
- Web："训练"页与"待审批"页的训练部分。
- T0 技术验证：FR3 接触抓取。

### 1.3 不包含

- 远程计算节点的实现。只定义契约，第一版只有本机节点。
- PhysX 后端。
- 视觉策略（相机输入）与蒸馏。第一个任务只用状态观测。
- 自动超参搜索、多 GPU 分布式训练。
- 自建指标存储（SwanLab、W&B 等）。
- 真机部署与 sim-to-real。

## 2. 关键事实与约束

| 事实 | 来源 | 影响 |
|---|---|---|
| Isaac Lab 3.0 的 Franka Lift 默认物理预设为 `newton_mjwarp` | `isaaclab_tasks/core/lift/lift_env_cfg.py:518` | 训练与平台用同一个物理引擎族 |
| kitless：`uv run isaaclab train ...` 不启动 Isaac Sim | IsaacLabTutorial README | 训练环境轻，适合远程节点 |
| SO-101 自由刚体接触抓取，成功率 99.2% | IsaacLabTutorial | 接触抓取在 Newton MJWarp 中可学；D-01 的 0/20 是 FR3 场景调参问题 |
| 本机 RTX 3070 Ti Laptop 8 GB：4096 环境显存峰值 7.71 GB；2048 环境约 12.6 s/iter，800 iter 约 2.8–3 h | 教程 benchmark 与 8 GB 教程 | 训练期间 GPU 被独占 |
| 教程环境 Newton 1.6.0rc1 / Warp 1.17；平台 Newton 1.7.0 / Warp 1.18 | 两边 lock 文件 | 训练与评估版本可能不同，见 §8.3 |
| `isaaclab play/train` 支持 `--external_callback` | IsaacLabTutorial 评估 | 训练视图桥接无需修改 Isaac Lab |
| rsl_rl 默认写 TensorBoard 事件文件 | rsl_rl 5.4.1 `logger.py` | 指标无需自建 |
| kitless Newton 预设需 `PXR_WORK_THREAD_LIMIT=1`（OpenUSD 25.05 并发缺陷） | 教程 README | 作业启动环境必须设置 |

## 3. Task

### 3.1 Task 包

Task 是一个 Isaac Lab 下游 Python 包（同 IsaacLabTutorial 的结构），通过 `isaaclab.tasks` entry point 注册，外加一份 `task.yaml`：

以下保留 `fr3-lab@2` 的目标场景示例；T1 开发期以 `robot-and-tubes@2`（含 FR3）代入。

```yaml
name: fr3-pick-tube
title: FR3 从试管架取管放入离心机
scene: fr3-lab@2                     # 训练场景（只取实体，不取布景）
robot: /World/Lab/RobotA
implements: robot-arm/v1.pickPlace   # 训练出的策略实现哪个动作
episode:
  params: {from: rack.A1, to: centrifuge.slot1}   # 训练时的动作参数；评估可另给
success:                             # 平台条件语言（信息规格 §4.3）
  all:
    - {entity: /World/Lab/Tubes/Tube01, property: location, equals: /World/Lab/Centrifuge/Rotor/Slot1}
    - {entity: /World/Lab/RobotA, property: gripperWidth, gte: 0.0799} # 0.08 m 打开，容许浮点反馈
    - {entity: /World/Lab/RobotA, property: gripperWidth, lte: 0.0801}
control: {hz: 30, action: joint_position_delta}
observation: obs_spec.yaml           # 观测与动作规格，供一致性测试（§8.2）
isaaclab: {task_id: Lab-FR3-Pick-Tube-v0, presets: [newton_mjwarp]}
evaluation:                           # 固定评估声明（§7）
  scene: fr3-lab@2
  episodes: 100
  seeds: [42, 43]
  params: {from: [rack.A1, rack.A2], to: [centrifuge.slot1, centrifuge.slot2]}
loopBudget: {gpuHours: 18, episodes: 1000, days: 7}   # 目标闭环预算（§8.9）
```

- 环境配置加载 `scene` 指向的 Scene Version 的 USD：只取 `/World/Lab/**`，按环境数复制。
- `isaaclab.sceneInput` 默认 `usd`，节点下载固定场景及素材包后提供包内 USD 路径。对于保留的 Python builder 场景（如 T1 的 `robot-and-tubes@2`），Task 必须显式声明 `sceneInput: scene-package` 并在自己的 Isaac Lab 配置中消费场景包；节点不把 Python builder 当成 USD，也不替换成其他场景。未声明的 builder 输入在作业预检时明确拒绝。
- 奖励、观测、重置与随机化写在 Python 里。agent "改奖励"就是改这里，然后推一个新版本。
- 训练端的成功判定由 Task 自己用 torch 实现，必须与 `success` 语义一致。**平台评估以 `success` 为准**（§7）。

### 3.2 版本

- Task 源码放在工作区：独立 git 仓库或 `tasks/<name>/`，人和 agent 在那里修改。
- `lw task push <dir>` 把它快照进库，得到 `task/fr3-pick-tube@N`，带内容哈希；同一内容重复 push 返回已有版本。
- 作业只引用库里的 Task 版本。

## 4. 训练计划

训练计划是 `kind: training` 的 Plan，与操作计划共用 Plan 的修订、状态、界面面板和 `lw plan` 命令组（实时操作规格 §8）。

### 4.1 内容

以下计划的评估场景保留为 `fr3-lab@2`；T1 开发期以 `robot-and-tubes@2` 代入。

```yaml
id: plan-0007
kind: training
title: fr3-pick-tube 第 3 轮
goal: 提高插入成功率；平台评估 ≥ 85/100
task: task/fr3-pick-tube@3
previous: plan-0006                    # 上一轮，用于显示差异
algorithm: {library: rsl_rl, name: PPO, version: 5.4.1}
model: {actor: [256, 128, 64], critic: [256, 128, 64], activation: elu}
hyperparameters: {num_envs: 2048, max_iterations: 800, learning_rate: 1.0e-3}
jobs: {seeds: [42, 43, 44]}            # 计划可以包含多个作业
budget: {gpuHours: 9.0}
evaluation: {scene: fr3-lab@2, episodes: 100, threshold: 85}
rationale: 第 2 轮评估 41/100 失败于插入阶段；held_goal 权重 0.1 → 0.2
hypotheses:                            # 每条都要可测量（§8.7）
  - id: H7.1
    cause: 插入失败是因为持管靠近插入位姿的引导（held_goal）太弱
    change: held_goal 权重 0.1 → 0.2
    predict: {metric: stage.insert, from: 0.61, to: ">= 0.80"}
notebookRead: [H6.1, H6.2, H5.3]       # 访谈前读过的实验记录条目
interview: interview.md                # lab-training-plan 产出的完整问答记录
```

### 4.2 流程与规则

1. **对话确认**：agent 先读实验记录（§8.7），然后按 `lab-training-plan` skill 与人逐轮访谈（grilling 方式：每轮提出当前能问的全部问题，每题附推荐答案，"是"即采纳推荐）。已被证伪的假设不能原样再提，除非说明新的证据。这一步只是对话，不是平台对象。
2. **提交**：`lw plan submit plan.yaml`，附访谈记录，产生"训练计划审批"请求。
3. **审批**：人在 Web 或交互式 `lw approve` 中批准或驳回，可附备注。驳回后计划状态为 `rejected`，agent 回到访谈。
4. **执行**：计划批准后，agent 可以在 `jobs` 声明的范围内自主启动、取消作业。
5. **预算**：作业启动前，用 `hyperparameters` 和节点上实测的每 iter 耗时估算 GPU 小时。累计预计用量超过 `budget` 时，产生"超预算作业"审批。
6. **改动即新计划**：改算法、模型、奖励（即 Task 版本）、超出声明的 seed，都必须提交新计划。

计划状态：`draft → submitted → approved | rejected → completed | abandoned`。

## 5. 作业

### 5.1 生命周期

`queued → running → succeeded | failed | cancelled`。作业属于一个已批准的计划。网关重启时，运行中的作业由节点重新上报状态；节点失联超过 60 s，作业标记为 `failed`（原因 `node_lost`）。

### 5.2 计算节点与 GPU 租约

- **计算节点**：一台机器上的一个节点程序，管理该机器的 GPU 和作业队列。第一版只有本机节点 `local`，由网关以子进程启动。
- **GPU 租约**：每块 GPU 同时只运行一个作业，由节点按队列顺序分配。
- 训练作业持有 GPU 期间，新建的 Newton 会话使用 CPU 设备；已有的 GPU 会话继续运行，训练作业排队等待。
- `lw node ls` 显示节点、GPU、租约持有者、队列。
- `GET /nodes` 为每块 GPU 附加在网关内存中保留的最近一小时遥测历史：每 10 s 桶保留最后一次实际心跳采样，缺值仍为 null 并保留原因；没有有效观测时间时记录收到心跳的时间，但 `observedAt` 保持 null。网关重启、新节点实例或设备移除后，较早历史留空。
- 算力页以作业开始时间和同配置实测耗时显示预计释放时间；估计缺失、已超时或租约正在隔离清理时显示未知。节点失联仍保留未清理租约，不能把它显示为空闲算力。

### 5.3 作业契约

作业契约必须自包含，远程节点只需要在另一台机器上运行同一个节点程序：

- **输入按引用**：Task 版本、Scene Version 及其引用的素材版本、计划中的超参与 seed。节点从平台拉取这些包，不假设共享文件系统。
- **运行**：节点在独立的 Isaac Lab 环境中执行 `isaaclab train --rl_library rsl_rl --task <task_id> ... presets=newton_mjwarp`，设置 `PXR_WORK_THREAD_LIMIT=1`，并注入 `--external_callback`（训练视图与心跳，§6）。网关从不 import Isaac Lab。
- **输出上传**：检查点、导出的 ONNX、TensorBoard 事件文件、stdout/stderr 日志、`versions.json`。`versions.json` 记录 Isaac Lab、Newton、Warp、torch、rsl_rl 版本、GPU 型号与 seed，格式同教程 benchmark 的 `versions` 与 `hardware` 段。产物存到 `<库>/jobs/<jobId>/`。
- **心跳**：每 10 s 上报一次：iteration、平均奖励、训练端成功率、已用时间、显存占用。

### 5.4 指标

- 完整曲线看 TensorBoard：平台按需从已配置的冻结 Node Python 运行时启动一个本地进程，读取
  `<库>/jobs` 的正式事件文件，并以独立 `live-<jobId>` 名称读取运行中 local Job 的精确训练
  `logs` 目录，不扫描其他环境。作业页与 `lw tensorboard` 返回地址。
  进程使用随机 loopback 端口、独立 URL 前缀且禁用 CUDA。相同输入集合重复调用复用同一进程；
  新作业改变输入集合时，下一次调用只重启自身进程并返回新地址。启动等待不占用会话、心跳或取消锁，
  调用者断开不丢失创建任务；Gateway 关闭时通过原创建 handle 回收进程树。
- 平台只存储并显示心跳里的标题指标。Job 的 `metricsHistory` 最多保留 900 条已接受报告，
  `receivedAt` 是 Gateway 接收该报告的 Unix 秒，`reportedStatus/reason` 保留节点原报告，
  metrics 的 0 与 null 分开保留；相同报告重试不增加历史。旧 Job 无历史时为空，不补造过去数据。
  标题曲线以 iteration 为横轴，同一 iteration 取最后一次观测，缺值处断开；终态最后报告仍保留。
- 第三方实验管理（SwanLab、W&B）不在 v1 范围，可以由 Task 包自己配置 rsl_rl 的日志写入器接入。

## 6. 训练视图

### 6.1 只读通道与身份

网关为运行中的作业提供 `/jobs/{id}/view` WebSocket。训练视图不是 Session，
不创建会话、录制或动作管线，没有动作、暂停或重置；可以同时打开多个，
并与一个普通可控制的 CPU 交互会话共存。浏览器关闭或视图出错不取消作业、
不释放训练租约。作业和节点生命周期仍由 §5 管理。

该端点使用独立的 JSON 外层封装 `schema: "lw.training-view/1"`，内层复用**原样的**
spatial-v2 `SceneManifest` 与 `TransformFrame`，不修改 NSYN-v1、`entityKey` 或旧录制格式。
`envIndex` 只在外层，不添加到已有严格空间消息、不拼入实体 key，也不追加到二进制帧。

| 身份 | 含义 |
|---|---|
| `jobId` | 已有 Job 及其固定的 Task、Scene、Asset、seed |
| `attemptId` | 与当前 dispatch 绑定的不透明公开标识；不是认证秘密，不增加重试或新的 Attempt 资源 |
| `viewId` | 一次实际生产者实例；该实例的 socket、节点或网关重连保持不变，替代生产者使用新值 |
| `envIndex` | 实际环境编号 0–3；环境少于 4 个就只显示真实数量，复制 env 0 不能充当四环境验收 |

每条封装有 `schema`、`type`、`jobId`、`attemptId`、`viewId`；环境消息另有 `envIndex`。
类型为 `hello`、`manifest`、`frame`、`status`、`ended`、`error`。
身份尚未建立时，`status` / `error` / `ended` 的 `attemptId`、`viewId` 可为明确的 null；
不能由此创建环境 source。实际 `hello` / `manifest` / `frame` 必须携带非空身份。
未知版本/类型、身份不符、越界环境、非有限或非法位姿均拒绝。
生产者通过 `/jobs/{id}/view-frames` WebSocket 推送，由节点令牌、节点实例与当前 dispatch
认证；浏览器端不接受生产者输入。

内层 `sessionId` 固定为 `training-view:{viewId}:env:{envIndex}`，仅表示一个空间流，
不得注册、解析为 Session 或用于拼接 `/sessions/...` 请求。
一个不可变 descriptor revision 绑定固定几何与 canonical streamId 表，所有环境共用它。

`hello` 绑定确切 Task/Scene/Asset 内容哈希、选定环境、canonical descriptor digest、
实际生产者的来源/版本/solver/physicsDt，以及最多 10 Hz 的发布率。
所有类型的数据放在 `payload`：hello 使用 `task`、`taskContentHash`、`scene`、
`sceneContentHash`、`assets`、`descriptorDigest`、`mappingDigest`、`environments`、`source`、
`maxRateHz`，可附 `capture.source`；manifest/frame 分别承载原空间消息。
status 使用 `state: waiting | live | stale` 与可选 message；error 使用 code/message；
ended 使用真实 Job status/reason。空间 `sceneRevision` 为 `training-scene-{descriptorDigest}`，
`revisionNumericId=1`，不依赖捕获时的临时流身份。
每个环境先收到验证后的 Manifest 与一份**真实完整快照**，再接收后续帧。
v1 每次发布完整刚体位姿表，不保留增量历史；Site 按 canonical 父体与固定 localPose 推导。

### 6.2 reset、重连与结束

每个环境有独立 PoseBuffer、选择与渲染位姿状态。`seq` 在整个 `viewId` 生命周期中严格递增，
包括 episode reset；`epoch` 只在该环境真实 reset 时增加，下一次样本为完整快照。
其他环境继续，不把 randomized episode age 当物理时钟；使用实际累计 physics clock。

同一 `viewId` 重连保留 buffer 与 lastSeq，调用 `expectSnapshot`，核对 descriptor 未变，
并按 buffer 的恢复规则接受同一最新完整样本；旧样本等待新生产者数据，不通过重新 configure
掩盖倒退。只有新 `viewId` 才销毁/重建环境 source 并重新开始计数；旧 socket/实例消息丢弃。

网关缓存丢失后等待生产者重新绑定 hello/descriptor 与真实完整样本，不从预览恢复动态状态。
生产者断开显示 waiting/stale 并停止外推。慢读者只保留最新完整样本，不形成无界队列，
不阻塞训练；多个读者共享同一份有界提取。

Job 进入终态后停止接受 live frame，发送含真实状态/原因的 `ended`，正常关闭读者 socket，
释放活动视图资源与最新位姿缓存。已终态 Job 返回明确 not-running，不假装仍有 live 视图。
指标、日志、产物与 TensorBoard 仍可读取；不保留空间回放或终帧录制。

### 6.3 固定几何与实际物理状态

Manifest 来自作业固定的 Scene Version/Asset closure，包含实体、Site、joint 与静态布景。
严格验证的**确切固定版本**预览可以提供 Manifest/几何字节；其 snapshot、EntitySnapshot、
catalog 值及动态体初始位姿不能当训练状态。所有 mesh/material/GLB 子资源必须重绑到
Job 拥有的不可变 `/jobs/{id}/view-assets/{descriptorDigest}/...` 资源，不能请求 Session 路径。
资源独立于可删除或改变的预览缓存；终态释放活动引用，共享不可变内容缓存可按自身规则淘汰。

Manifest.source 使用实际 trainer 的 SourceInfo（真实 Newton 版本、solver、dt、时钟及 10 Hz
上限），几何捕获来源单独保留，不能把捕获运行时冒充训练端。实际 Newton source.kind 仍为
已有的 `newton`，不向空间枚举添加 `training`。

必须核对每个环境完整 canonical body/Site/joint closure 与 streamId 映射；预览 Newton index
不是 trainer index。使用实际 `NewtonManager.get_model/get_state` 读取完整 Lab，不只取 FR3
Articulation 而遗漏 tube/lid/rotor。编译、固定关节合并或 clone 后，消费 Task 的 hash-bound
canonical/world map；从真实保留父体与固定变换恢复 canonical frame。普通模型也必须用实际
label/world membership 证明映射，不能猜索引或任意剥除 env 前缀。

Task 通过实际 unwrapped env 的 `lw_training_view()` 提供 `lw.training-view-map/1` 数据：
`taskContentHash`、`sceneContentHash`、`assets[{id,contentHash}]`、实际 `solver`，以及按真实
env 0–3 选择的 `environments[{envIndex,world,cloneTransform,bodies}]`。
每个 body 必须给出 canonical `entityKey`、实际 `bodyIndex/bodyLabel` 和保留父体到该 canonical
frame 的 `localTransform`；两个 transform 均为 Newton 的 `[x,y,z,qx,qy,qz,qw]`。
桥接核对固定输入 hash、完整 body closure 与实际 model label/world membership，固定变换和
clone 变换必须来自 Task 建模时保留的数据。该 SPI 不替代旧空间协议，也不赋予 Task 节点凭据。

传输前移除该环境**实际完整 clone rigid transform**，恢复固定场景的 canonical 世界坐标。
平铺只改变相机/视口，不改实体 key、几何、坐标约定或物理位置。映射或 descriptor 缺失/歧义时
发明确 `training_view_mapping_unavailable` / `training_view_manifest_unavailable` 错误，训练继续；
不补预览位姿、不猜机器人模型、不创建假的 Session。

### 6.4 回调与成本边界

Isaac 的零参数 `external_callback` 在环境创建前安装桥接；在已有 `OnPolicyRunner.learn`
入口取得真实 env 后，观察该**实例**的 public step/reset。每次原调用恰好执行一次，保留结果和
异常，在 finally 恢复；不编辑 SDK、不全局替换 step 类。每步都跟踪真实 autoreset/显式 reset，
即使这次未达到采样时刻；状态采集只在 stepping thread 成功 stepping/reset 后进行。

回调至多每 100 ms 提取选定真实环境（默认 env 0；详情的四格可选择实际 env 0–3），上限是
4 环境 × canonical 实体刚体数 × 10 Hz。先在 producer device gather 这些体、恢复固定 frame
并移除 clone transform，再 CPU readback；不能先拷全部训练环境再过滤。样本时钟、epoch、seq
与 gather 一起固定，网络/序列化走有界异步路径，不读取后台线程中的可变物理状态。

训练进程不获得节点令牌。由持有原令牌的节点转发；使用有界临时 latest-state/IPC 通道时，
其文件必须在 `LW_NODE_OUTPUT` **之外**，不能追加 view.jsonl、进入 Job 产物或录制清单。
结束/异常均关闭 worker 并清理临时状态；Task 自有诊断日志与正式训练视图传输不是同一种数据。

### 6.5 浏览器与验证

一个 Canvas/WebGL context 通过四个 Drei View/scissor 区域构成 2 × 2 只读视图，
各有 env 标签、相机、buffer 与选择。不可见区域不渲染，未收到真实完整样本前不显示动态体。
视图仅在运行中显示，与普通 CPU Session 互不控制。

公开路径验证包括：相同 canonical key/seq 的 env 0/1 具有不同真实位姿；仅 env 1 reset 后以
更高 seq 出快照；same-view 等 seq 恢复；新生产者 viewId；终态 socket/cache 清理；缺映射或
descriptor 明确失败而不影响训练；完整 canonical frame/Site closure；四个实际 world 的映射；
网络/存储证据无 Session/recording 访问或写入；并发 CPU Session 仍可控制。
假节点四环境 E2E 与手动真实作业四环境/TensorBoard 验收分别记录，原一环境 CPU Task 不能充当后者。

## 7. 评估

- 每个目标有一套固定的**评估集**，在 `task.yaml` 中声明：场景版本、回合数、动作参数的覆盖范围（例如 `from: rack.A1–A20`，`to: centrifuge.slot1–4`）与初始状态种子。同一目标的所有候选（策略或脚本程序）都在同一评估集上比较。
- `lw eval run <checkpoint|policy|program> --suite <task>` 创建一个评估；`--scene fr3-lab@2 --episodes 100` 形式用于临时评估，结果不参与接受判定。平台在**自己的 Newton** 中顺序运行回合；每个回合是一次录制会话，结束时按 Task 的 `success` 条件判定。
- 评估运行期间占用单活跃会话，Web 显示为"评估中"。
- 结果包括：成功数、按里程碑（如抓取、抬升、插入）的分阶段通过率，以及每个回合的录制 ID 与失败阶段。
- **平台评估是唯一裁判**。训练端成功率只作参考。
- agent 读取失败回合的录制和事件，作为下一轮访谈的输入。

## 8. 策略

### 8.1 版本与运行

- 检查点经评估后，用 `lw policy create <jobId>/<checkpoint>` 生成 `policy/fr3-pick-tube@N`：包含 ONNX、`obs_spec.yaml`、来源作业、Task 版本、评估结果。
- 新程序 `robot_arm.policy` 实现 `robot-arm/v1`：`pickPlace` 等动作由策略在 Runner 中用 onnxruntime 按 Task 的控制频率推理执行，替代示教位置轨迹。配置示例：`program: robot_arm.policy`、`config: {policy: policy/fr3-pick-tube@3}`。
- 使用策略时抓取方式为接触抓取，不使用焊接约束。
- 动作参数超出 Task 训练覆盖的范围时（例如训练只覆盖 `rack.A1–A4`），以 `precondition_failed` 拒绝，并附提示。

### 8.2 观测一致性

Isaac Lab 和平台各自计算观测，算法必须一致：

- Task 包的 `obs_spec.yaml` 声明每个观测项的含义、坐标系、单位与顺序，以及动作的含义与缩放。
- `robot_arm.policy` 按规格从 PhysicsPort 读取状态并组装观测。
- **一致性测试**（策略提升前必须通过）：从训练环境采样 256 个状态，分别送入 Isaac Lab 和平台的观测组装，逐项比较，最大绝对误差 ≤ 1e-4（位置单位为米）。

### 8.3 Newton 版本差异

训练用 Isaac Lab 锁定的 Newton 版本，平台用自己的版本；两边版本都写进作业和评估记录。平台评估是唯一裁判。若某策略在平台评估中的成功率比训练端低 15 个百分点以上，评估结果标记为 `sim_gap`，并建议在下一轮计划中统一版本。

### 8.4 提升

提升前必须满足**接受规则**（§8.8）。`lw policy promote policy/fr3-pick-tube@3 --scene fr3-lab@2` 产生"策略提升为默认程序"审批。批准后，平台为该场景生成新的场景版本（如 `fr3-lab@3`），其 `devices.yaml` 把机械臂的程序换成 `robot_arm.policy`。原版本不变，保证旧录制可复现。

### 8.5 agent 编写的设备程序

RSI 的改进对象不只是策略权重（参考 EmbodiedRSI 的代码与技能共同进化、EmbodiedSWE 的 coding agent 解题）。agent 也可以直接写控制代码：

- agent 在工作区写一个实现 `robot-arm/v1` 某个动作的**脚本程序**：Python 类，只能使用 Program 接口与 PhysicsPort，不能 import Newton 内部 API。`lw program push <dir>` 把它快照进库，得到 `program/robot_arm.script.pick-tube@N`。
- 脚本程序与策略一样，在评估集上评估，满足接受规则后，经同一个人工关口提升为场景的默认程序。
- 脚本程序在 Runner 的同一进程中运行。v1 不做沙箱，只在仿真会话中允许加载；实况（`/Real`）拒绝加载脚本程序。
- 脚本程序也是 Plan 的产物：一轮"写脚本"同样以 `training` 类型的 Plan 提交，只是没有训练作业，`jobs` 为空、预算为评估回合数。

### 8.6 示范

- 任何设备程序（示教、策略、脚本）在评估或会话中成功完成的回合，都可以用 `lw demo add <recordingId>` 标记为**示范**，记录来源程序、任务与参数。
- Task 包可以声明使用示范：作为分阶段重置数据集的来源（替代教程里手工生成的重置状态），或作为模仿学习的数据。训练作业的输入因此多了一项"示范集版本"，按引用传递（§5.3）。
- 这让"agent 写脚本解出一次 → 录下示范 → 训练出更鲁棒的策略"成为闭环里的一条路径（EmbodiedSWE 的做法）。

### 8.7 实验记录

- 每个评估完成后，平台把它与对应 Plan 的假设对照：预测指标达到 → `confirmed`；明显未达到（置信区间上界低于预测值）→ `refuted`；其余 → `inconclusive`。人可以改判，改判记录操作者。
- 实验记录按目标累积，每条包括：轮次、假设、改动、评估集结果、判定、相关录制。存放在库的 `notebooks/<task>/`，以 Markdown 加 YAML 头保存，人和 agent 都能直接阅读。
- `lw notebook show <task> [--refuted]`。`lab-training-plan` 访谈前必须读取（§4.2）。

### 8.8 接受规则与 RSI 看板

- **接受规则**：候选在评估集上的成功率 95% 置信区间（Wilson）下界，必须高于当前最佳版本的点估计，才算"被接受"，才能申请提升。未被接受的候选仍保留在库里，可用于分析。
- **RSI 看板**（训练页）：按轮次显示评估集成功率及置信区间、每轮"缩小的剩余差距"比例（借鉴 RSIGym 的 RSI-Index：`(新 − 旧) / (1 − 旧)`）、各假设的判定，以及当前默认程序。

### 8.9 闭环预算

- 每个目标有一份**闭环预算**：GPU 小时、评估回合数、墙钟天数，在 `task.yaml` 中声明，由人设定。
- 计划预算从闭环预算中扣除；闭环预算用尽后，新计划一律需要审批，并在审批卡上显示"闭环预算已用尽"。
- 训练页显示闭环预算的使用进度。

## 9. HTTP API、CLI、MCP 与 skill

### 9.0 HTTP API

路径前缀 `/api/v1`。

| Method | Path | 结果与错误 |
|---|---|---|
| GET | `/tasks`、`/tasks/{name}/{rev}` | Task 版本列表与 `task.yaml` 内容 |
| POST | `/tasks` | 上传 Task 包（tar），返回版本；内容相同返回已有版本 |
| POST | `/plans`（`kind: training`） | 提交训练计划，返回计划与审批 ID；`invalid_plan` |
| POST | `/jobs` | `{planId, seed}` → 202 `queued`；计划未批准 `plan_not_approved`；超出预算返回 `pending_approval` 与审批 ID |
| GET | `/jobs?plan=&status=&kind=`、`/jobs/{id}` | 作业状态、最近心跳指标、`versions.json`、产物清单 |
| POST | `/jobs/{id}/cancel` | 取消；已终态时返回当前状态 |
| GET | `/jobs/{id}/logs?after=&timeoutSeconds=` | 日志行长轮询，返回新游标 |
| WS | `/jobs/{id}/view` | `lw.training-view/1` 只读通道；hello → 每环境 Manifest/真实完整快照 → 完整帧；见 §6 |
| GET | `/jobs/{id}/view-assets/{descriptorDigest}/...` | 该 Job 固定、已校验哈希的几何资源；无 Session 路径 |
| GET | `/nodes` | 节点、GPU、利用率、显存、租约持有者、队列 |
| POST | `/evaluations` | `{subject, suite, split}` → 评估 ID；占用单活跃会话，会话忙时排队 |
| GET | `/evaluations/{id}` | 回合列表（录制 ID、结果、失败阶段）、分阶段通过率、成功率与 Wilson 区间、判定 |
| POST | `/policies` | `{job, checkpoint}` → 策略版本（含 ONNX、`obs_spec`、来源） |
| GET | `/policies`、`/policies/{id}` | 策略版本与评估成绩 |
| POST | `/policies/{id}/promote` | `{scene}` → `pending_approval`；未满足接受规则时 409 并附判定 |
| POST | `/programs`、GET `/programs` | 脚本程序版本 |
| POST | `/demos`、GET `/demos?task=` | 标记与列出示范 |
| GET | `/notebooks/{goal}` | 实验记录条目；`PATCH /notebooks/{goal}/{entry}` 由人改判，记录操作者 |
| GET | `/loops/{goal}` | RSI 看板数据：各轮成绩与区间、剩余差距缩小、闭环预算使用量 |
| POST | `/tensorboard` | 启动或返回本地 TensorBoard 地址 |

节点内部接口（本机令牌认证，不对 CLI / MCP 开放）：`GET /jobs/{id}/inputs`（按引用拉取输入包）、`POST /nodes/{id}/heartbeat`、`POST /jobs/{id}/artifacts`、`WS /jobs/{id}/view-frames`（以当前节点实例/dispatch 认证，推送 §6 独立封装；浏览器不能生产帧）。

### 9.1 CLI

```text
lw task push <dir>  |  lw task ls  |  lw task show <id>
lw plan submit <plan.yaml>  |  lw plan ls  |  lw plan show <id>
lw job start <planId> [--seed N]  |  lw job ls  |  lw job show <id>
lw job logs <id> [--follow]  |  lw job cancel <id>
lw node ls
lw eval run <ref> --scene <name@rev> --episodes N  |  lw eval show <id>
lw policy create <jobId>/<checkpoint>  |  lw policy ls  |  lw policy show <id>
lw policy promote <id> --scene <name@rev>
lw tensorboard                           启动或返回本地 TensorBoard 地址
lw program push <dir>  |  lw program ls  |  lw program show <id>
lw demo add <recordingId>  |  lw demo ls [--task T]
lw notebook show <task> [--refuted]
lw loop show <task>                      RSI 看板与闭环预算
```

全部支持 `--json`。MCP 镜像为通用工具：`task_*`、`plan_*`、`job_*`、`node_ls`、`eval_*`、`policy_*`、`program_*`、`demo_*`、`notebook_show`、`loop_show`。没有审批工具。

### 9.2 平台 skill

- **`lab-training-plan`**：何时启动访谈；先读实验记录与 RSI 看板；在"训练策略"与"写脚本程序"两条路径之间给出推荐；每条假设必须可测量；按 grilling 方式分轮提问（失败分析 → 目标 → 算法 → 模型 → 超参 → 预算 → 评估标准），每题附推荐；生成 `plan.yaml` 与 `interview.md`；提交后等待审批，不得自行审批；被驳回时读备注，回到访谈。
- **`lab-train-evaluate`**：在已批准计划的范围内启动作业；监控心跳，异常（OOM、发散）时取消并说明原因；作业完成后评估；读取失败录制并总结；达到阈值时申请提升；未达标时带着失败分析回到 `lab-training-plan`。

## 10. 前端

- **训练页**：作业列表，显示状态、计划、节点、进度、标题指标，以及 TensorBoard 链接；计算节点与 GPU 租约；计划列表。
- **作业详情**：训练视图（2×2 环境平铺）、标题指标曲线、日志、计划与差异、检查点、相关评估。
- **评估详情**：成功率、分阶段通过率、回合列表，每个回合链接到回放页。
- **RSI 看板**：每个目标一张，含评估集成功率曲线与置信区间、剩余差距缩小比例、实验记录（假设与判定）、闭环预算进度。
- **待审批页**：三类训练关口卡片。计划卡片并排显示计划、访谈记录和相对上一轮的差异；批准或驳回时可写备注。
- 布局与视觉方向随素材与场景规格 §11.3 的原型结论统一确定。

## 11. 里程碑

```text
T0 FR3 接触抓取技术验证（与平台开发并行）──► #35（策略与 robot_arm.policy）
A2 + L2 ──► T1 作业 + 节点 + 训练视图 ──► T2 评估 + 策略 + 实验记录 + 一圈 RSI ──► T3 脚本程序 + 示范
A3 fr3-lab ──► T2 的 fr3-lab 验收（#35）
```

**T0 技术验证（平台外，Isaac Lab 工作区）**

内容：把教程的任务设计（里程碑奖励、分阶段重置数据集、受限的高斯动作分布）移植到 FR3 + 试管 + 试管架；USD 素材与场景自带，不依赖 A 系列。在本机 3070 Ti 上用 2048 个环境训练。

验收：

1. 训练端成功率 ≥ 90%（教程方式的 1,024 回合审计）。
2. 记录每 iter 耗时、显存峰值、总时长，写进 `.scratch/` 与 PR 描述。
3. 结论：FR3 接触抓取在 Newton MJWarp 中可行或不可行，并据此给出 D-01 的建议。

**T1 作业、节点与训练视图**

前置：A2 与 L2。T1 不等待 A3 与 T0，从 #30 开始；#30–#33 以 `robot-and-tubes@2`（含 FR3）开发与验收。A3（#18）与 T0（#29）只阻挡 #35，T2 的里程碑验收仍在 `fr3-lab` 上进行；#34 可先用 `lab-demo` 的 virtual 程序作为被测对象。

内容：Task 版本、训练计划与审批、本机节点与 GPU 租约、作业契约、心跳、TensorBoard 启动、训练视图、训练页与待审批页、`lab-training-plan` skill。

验收：

1. agent 按 `lab-training-plan` 完成访谈并提交计划；人批准后 agent 启动作业；计划未批准时 `lw job start` 被拒绝。
2. 训练视图在浏览器中显示 4 个环境；同时可以打开一个 CPU 交互会话。
3. 预计超预算的作业产生审批，未批准前保持排队。
4. 作业产物与 `versions.json` 完整落盘；`lw tensorboard` 能看到曲线。

**T2 评估、策略与一圈 RSI**

内容：评估与录制、策略版本、ONNX 推理程序 `robot_arm.policy`、观测一致性测试、提升审批、`lab-train-evaluate` skill。

验收：

1. 在 `fr3-lab` 上平台评估 100 回合并全部录制，成功率 ≥ 80%。
2. 观测一致性测试通过。
3. 提升获批后，`lw do /World/Lab/RobotA pickPlace from=rack.A1 to=centrifuge.slot1` 由策略完成，浏览器可见。
4. Claude Code 只依靠平台 skill 完成一整圈：读实验记录 → 失败分析 → 访谈 → 计划（含假设）→ 批准 → 训练 → 评估集评估 → 假设判定写入实验记录 → 满足接受规则后申请提升。这一圈全程录制。
5. RSI 看板显示两轮以上的评估集结果与置信区间；闭环预算的使用量与实际一致。

**T3 脚本程序与示范**

内容：脚本程序（`lw program push`）、脚本程序评估与提升、示范标记、Task 包使用示范作为重置数据集。

验收：

1. agent 写出一个 `pickPlace` 脚本程序，在评估集上评估，结果进入 RSI 看板，与策略可比。
2. 把脚本程序的成功回合标记为示范，作为下一轮训练的重置数据来源，训练作业的 `versions.json` 记录示范集版本。
3. 脚本程序在实况中被拒绝加载。

## 12. 测试

| 类别 | 内容 |
|---|---|
| 单元 | 计划状态机与预算估算；作业状态机；条件语言判定成功；观测规格解析 |
| 契约 | 作业契约、心跳、`versions.json` schema；训练视图消息与空间协议一致 |
| 网关 | 用假节点（不跑 Isaac Lab）测试排队、租约、取消、失联、超预算审批、未批准计划拒绝启动 |
| CLI / MCP | 新命令输出与退出码；MCP 镜像工具；无审批工具 |
| Skill | 命令存在性检查（与素材规格共用） |
| 非门禁 | T0 训练、真实节点作业、观测一致性测试与真实评估：手动运行 |

CI 不安装 Isaac Lab，也不需要 GPU。

## 13. 目录结构

```text
skills/lab-training-plan/  skills/lab-train-evaluate/
packages/training/              与 Isaac Lab 无关
  plans.py  jobs.py  budget.py  contract.py
apps/
  node/                         节点程序（本机以子进程运行；以后部署到远程）
    runner.py                   拉取输入、启动 isaaclab、上传输出
    bridge_callback.py          --external_callback：训练视图与心跳
  api/app/training_routes.py
  cli/lw/{task.py, plan.py, job.py, eval.py, policy.py}
packages/programs/robot_arm/policy.py   ONNX 推理程序
```

## 14. 待决策清单

| ID | 决策 | 默认值 | 状态 |
|---|---|---|---|
| T-01 | 物理后端 | Newton MJWarp；不退回 PhysX | 已定 |
| T-02 | 第一个任务 | FR3 取管放入离心机，状态观测 | 已定 |
| T-03 | 指标 | TensorBoard；平台只显示标题指标 | 已定 |
| T-04 | 训练视图 | ≤ 4 环境，10 Hz，只读，不录制 | 已定 |
| T-05 | 策略运行时 | ONNX + onnxruntime，CPU | 已定 |
| T-06 | 观测一致性阈值 | 1e-4 | 默认生效 |
| T-07 | Newton 版本差异 | 接受差异，平台评估为准；差距 ≥ 15 个百分点标 `sim_gap` | 已定 |
| T-08 | 节点失联判定 | 60 s | 默认生效 |
| T-09 | T2 平台评估阈值 | ≥ 80/100 | 默认生效 |
| T-10 | 远程节点 | 只定义契约，实现后续 | 已定 |
| T-11 | D-01（交互场景的抓取方式） | T0 之后重新评估 | 首轮固定验收 0/1,024，未达 922；维持 D-01 的 weld，#29 未通过。T0 只阻挡 #35，不阻挡 T1；第二轮遵守 #83 的假设、预算与固定判据，失败后由用户决定 T2 修订。见 [T0 结果](../research/FR3_CONTACT_T0_RESULT_V1.md) |
| T-12 | 改进对象 | v1：Task/奖励 → 策略；agent 编写的脚本程序；场景与素材留到平台内搭建之后 | 已定 |
| T-13 | 接受规则 | Wilson 95% 下界 > 当前最佳点估计 | 默认生效 |
| T-14 | 假设判定 | 平台自动判定，人可改判 | 已定 |
| T-15 | 脚本程序隔离 | v1 无沙箱；只在仿真中加载 | 默认生效 |

## 15. 参考

| 参考 | 借鉴点 |
|---|---|
| EmbodiedRSI（arXiv:2610.10498） | 假设图与按信息价值选实验 → §8.7 假设与实验记录；代码与技能共同进化 → §8.5 |
| EmbodiedSmith（arXiv:2610.07969） | 场景与任务互相改进 → 后续方向（平台内场景搭建之后） |
| EmbodiedSWE（arXiv:2609.27308） | coding agent 在仿真中解题，验证过的解扩增为示范 → §8.5、§8.6 |
| Kinex（github.com/stacx-lab/kinex） | 面向 coding agent 的 Devices / Tools / Skills 三层 harness，经 MCP 接 Claude Code → 印证 CLI + MCP + 平台 skill 路线 |
| Artisan（arXiv:2610.13947） | 用物理仿真反馈修正带关节素材 → 素材规格的物理检查；后续可让 agent 依验证报告修素材 |
| RSIGym（arXiv:2610.10310） | 训练、评估、rollout 作为服务；共享预算与权限；只带入被接受的改动；RSI-Index → §8.8、§8.9 |
