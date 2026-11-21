# Agent 优先的用户自助具身平台闭环（总规格）

> 本文记录 2026-11-19 获批的 v1.1 方案、契约与验收要求。当前交付范围、依赖关系和实施进度以总票及其子票为准。 [#290](https://github.com/CaiZongyuan/spatial-sync/issues/290)

## 与既有规格、契约及 ADR 的关系

| 既有文档 | 本总规格中的处理 |
|---|---|
| [AGENTS.md](../../AGENTS.md) 仓库硬契约 | 公共协议、历史录制兼容、`entityKey`、`Z-up` 几何与单 HTTP 动作管线保持不变；设备行为契约经用户确认扩充：内置设备仍在 `devices/` 与 `packages/programs/`，用户自定义机器人 `EntityType` 以不可变 `u-<slug>/v<N>` 库资源存储并由内置通用宿主驱动，MCP 工具保持通用目录发现。 |
| [ADR-0001](../adr/0001-usd-canonical-scene-format.md) / [ADR-0006](../adr/0006-mujoco-warp-training-backend.md) / [ADR-0007](../adr/0007-standard-usd-joint-drives-for-simulated-embodiments.md) / [ADR-0008](../adr/0008-multi-policy-dispatch-and-velocity-guided-waypoint-locomotion.md) | 保持 USD 唯一物理事实源、标准关节驱动与分层速度导引不变；补齐训练派生器碰撞过滤、摩擦与双侧模型一致性校验，并在现有通用 `embodied.policy` 上统一收敛四种动作完成原语。 |
| [ADR-0003](../adr/0003-external-asset-preparation-boundary.md) 外部素材准备边界 | 追加 2026-11-19 修订节：将平台确定性准备扩展至 `URDF`/`MJCF`/`USD` 机器人压缩包，所有非几何标定覆盖直接烘焙为新的不可变 OpenUSD `Asset` 版本并记录源关节正反转映射。 |
| [ADR-0004](../adr/0004-scene-authoring-through-external-factories.md) / [Lab Scene Factory](LAB_SCENE_FACTORY_SPEC_V1.md) | 追加 2026-11-19 修订节：复杂美术实验室环境继续使用外部 Blender 工厂，新增平台内确定性训练/评测 `Scene` 编排（仅存烘焙场地与初始刚体位姿，课程与随机化归入 `Task`）。 |
| [具身训练首航规格](LAB_EMBODIED_TRAINING_SPEC_V1.md)（#277）/ [Lab Training](LAB_TRAINING_SPEC_V1.md) | 升级观测资格信任根至平台声明式环境编译器（见 [ADR-0011](../adr/0011-declarative-mjlab-compiler-trust-root-and-reward-dsl.md)）；扩展 `Job` 挂起续训、多 `Compute Node`、安全 `safetensors` 检查点、视频 `Motion Clip`/`Reference Motion` 及 `Policy Export`（见 [ADR-0012](../adr/0012-user-owned-multi-node-compute-and-checkpoint-resumption.md)、[ADR-0013](../adr/0013-user-owned-video-imitation-and-policy-exports.md)）。 |
| [Lab RSI](LAB_RSI_SPEC_V1.md) / [Lab Web UI](LAB_WEB_UI_SPEC_V1.md) | 自动生成的 `Evaluation Suite` 绑定 `（EntityType 动作, Goal）` 并在首次开训关口冻结判据以维持 Wilson 可比性；保留超预算 `Approval Gate`；Web 延续仪器控制台风格提供轻量预览、摆放微调、多节点看板与审批中心。 |

## Problem Statement

立项时，双足 Microduck 标杆已验证 **Lab World** 从规范 USD `Asset`、MuJoCo Warp 并行训练、确定性物理 `Evaluation` 到跨场景运行的链路。用户或外部 `Agent` 接入自己的机器人并训练自定义场景与动作仍有以下障碍。

1. **外部机器人格式与双侧物理保真度断层**。平台导入接口接受规范 USD `Asset` 包，`.glb` 可由 USD 派生，绑定模板可为空或省略。接口仍无法直接导入 `URDF` 或 `MJCF` 机器人包。评估侧（Newton USD 导入器）与训练侧（USD 到求解器派生器）对碰撞排除对、接触摩擦及闭链和开口网格告警的处理存在缺口，导致两侧物理响应不一致，甚至在启动时发生自碰撞。 [素材包契约](LAB_ASSETS_AND_SCENES_SPEC_V1.md#5-素材包)
2. **具身资源只读，自定义类型缺乏不可变引用**。`Embodiment`、`Robot Model` 与 `Controller` 缺少用户态写接口。自定义设备类型仅靠测试环境变量注入，`Scene` 引用与 `Evaluation` 可比身份缺少对应的内容哈希，用户无法安全地定义新机器人构型或动作。
3. **观测资格依赖逐任务白名单**。策略资格审查按 `taskContentHash` 查找仓库内置白名单。用户或 `Agent` 新建的 `Task` 因缺少白名单记录而收到 `observation_source_unverified`，无法晋升。
4. **并行训练只在空平地运行**。训练任务依赖手工打包的 Python 环境入口，启动并行环境时只把机器人放在无限平地上，忽略 `Scene Version` 中的台阶、斜坡、障碍物与可交互刚体 `Entity`。
5. **缺乏从人类视频直观迁移技能的可行性链路**：当用户希望机器人学会挥手、鞠躬、舞蹈、踢腿等动作时，缺少“单人视频提取源 `Motion Clip` → 按肢体语义映射与可行性体检生成绑定机器人的 `Reference Motion` → Web 幽灵预览 → 强化学习平衡跟踪”的产品闭环。
6. **算力锁死单本机回环，且训练产物缺少清晰的内外双出口**：计算节点地址强制回环且绑定单本机身份、耗时剖面混入临时主机名、节点失联即判终态失败且不回传中间检查点，导致用户无法稳定挂载远程服务器或易断线的个人 Colab；同时平台缺少包含源电机正反转符号映射表的独立 `Policy Export` 导出包来衔接外部工程与后续 Sim2Real 真机落地。

## Solution

以 **“用户自备算力自托管 + 外部 CLI / MCP `Agent` 优先包办 + 轻量 Web 可视化工作台预览微调 + 关键成本与质量 `Approval Gate` 把关”** 为顶层原则，按以下八个正交模块构建完整产品闭环：

1. **双侧一致的外部机器人免脚本导入与烘焙标定（ADR-0003 修订）**：支持上传 `URDF`、`MJCF` 与 `USD` 机器人压缩包，将坐标轴/尺度归一化、体积质量惯量估算、连杆对碰撞过滤、接触摩擦、规范关节电枢惯量、标准 `UsdPhysics` 驱动参数与初始平衡站姿**直接烘焙为新的不可变 OpenUSD `Asset` 版本**，保留“源关节名 ↔ 规范关节名/轴向符号/零位偏移”映射表，并在 `Validation Report` 中强制执行仿真侧与训练派生侧的模型一致性校验（对闭链、球关节、开口碰撞网格等不支持结构显式拒收告警，绝不静默丢弃）。同一 `Goal` 在首次训练审批后冻结机器人 `Asset` 版本。
2. **不可变用户态 `EntityType`、只追加具身目录与统一四模式策略宿主（ADR-0010）**：开放 `u-<slug>/v<N>` 语法的不可变用户态 `EntityType` 发布（`Scene` 绑定与评估可比身份均钉住其内容哈希，自动注入标准状态属性），开放用户库只追加创建 `Embodiment`、`Robot Model` 与 `Controller` 头记录（具体 `Controller` 版本仍仅由人工晋升事务签发），并通过通用 MCP 目录发现暴露动作；在现有通用策略宿主 `embodied.policy` 上统一扩展 `phase_clip`、`posture_recovery`、`command_track`、`waypoint_nav` 四种声明式完成原语。
3. **平台内确定性训练 `Scene` 编排与多实体入训编译（ADR-0004 修订）**。`Scene Version` 承载确定性烘焙场地布局，包括平面、以盒体分解的台阶、斜坡、与求解器一致的起伏地面、静态碰撞障碍、动态刚体 `Entity` 的初始位姿及 `Waypoint`、`Teach Location`、`Site`。`mjlab` 编译器把场地几何和声明的 `Entity` 完整载入并行环境。`Task` 定义每回合的重置随机化、指令采样与地形难度课程。
4. **编译器级资格信任根、声明式 `Task` 与纯数学奖励表达式 DSL（ADR-0011）**：将平台声明式 `mjlab` 环境编译器设为自助通道的唯一观测资格信任根（任意 Python `Task` 包限制为内置或人工审计通道）；内置标准 MDP 原语菜单（Layer A）覆盖常规动作，长尾任务通过基于版本化 `lw.reward-state/1` 张量（统一 `Z-up` SI 单位与 `xyzw` 四元数）的非 Python 纯数学表达式 DSL（Layer B，带节点数上限与运行时 `nan_to_num`/限幅防护）表达；在 `Training Plan` 人工审批前自动运行目标环境规模下的**计划校验与耗时剖析 `Job`**，将实测 GPU 小时估算附于审批单。
5. **绑定 `Goal` 的服务端封存 `Evaluation Suite`（ADR-0011）**。服务端按 `(EntityType 动作, Goal)` 生成 `Evaluation Suite`，在该 `Goal` 首个 `Training Plan` 获人工批准时冻结成功判据、扰动参数与选定的回合数，并独立生成和封存 `split: test` 种子，确保多轮奖励调优可比。人工晋升关口将首个达标 `Candidate` 确立为初始 `Reference`。
6. **单人视频 `Motion Clip` 与机器人 `Reference Motion` 模仿闭环（ADR-0013）**：在用户接入的 `Compute Node` 上从单人清晰视频提取构型无关的源 `Motion Clip`（保留上游人体模型许可证声明），经显式肢体部位语义映射与可行性体检（无映射自由度拒收、脚底滑移、关节限位饱和度、质心支撑裕度、可选原地模式）生成绑定具体机器人 `Asset` 版本的 `Reference Motion`，经 Web 3D 幽灵预览确认后启动 `mjlab` 平衡模仿训练，并由三方共用金标准规范对齐相位时钟与插值。
7. **用户自备多 `Compute Node`、安全 `Checkpoint` 与按尝试分段续训（ADR-0012）**：放开认证远程网关模式，签发一次性注册码换取可吊销单节点令牌，剥离耗时剖面中的临时主机名并将本机交互会话 CUDA 锁限定在同机 GPU；训练期间定期回传纯张量 `safetensors` 格式 `Checkpoint`（ONNX 导出与 256 样本原生观测捕获始终在计算节点完成），节点断线后转为可恢复 `suspended` 态，支持在兼容节点续训并按每次尝试分段累加计费。日常保持“批准 `Training Plan`”与“批准 `Controller` 晋升”两道人工关口，同时保留换慢卡续训超出原批预算时的“超预算追加审批”兜底关口。
8. **内外双价值出口与 `Policy Export` 真机对接包（ADR-0013）**：晋升动作对内供外部 `Agent` 在 `Operation Plan` 中通过通用动作管线自主编排复杂任务；对外提供单 `Policy` 级 `Policy Export`（`lw.policy-export/1`）一键导出，内含 `.onnx` 模型、源 `URDF`/`MJCF` 关节名与正反转符号/零位偏移对照表、四元数顺序、`previous_action` 与双重限位裁剪规则、执行器 SI 参数、许可证声明及零平台依赖参考推理脚本（`float32` `1e-5` 容差一致）。

## User Stories

### Agent 优先协作与人工关口治理

1. 作为使用外部 `Agent Runtime` 的研究员，我希望通过 `lw` CLI、通用 `lw mcp` 工具与 `Platform Skill` 让 `Agent` 代办机器人导入标定、搭场景、转视频、提交预检、发起 `split: train` 评估与导出策略包，以便无需手写配置或拼装脚本。
2. 作为审批人，我希望日常准备、预览与 `split: train` 评估对 `Agent` 免审放行，仅在批准 `Training Plan` 开训（含冻结新 `Goal` 的 `Evaluation Suite` 判据）、批准 `Controller` 晋升以及跨节点续训预计超出原批 GPU 预算时要求人工审批，以便把审批集中在算力支出和晋升质量上。
3. 作为 Web 用户，我希望 Web 工作台聚焦拖拽上传、3D 重力与幽灵预览对比、资产/`Waypoint` 轻量摆放与参数滑块、多 `Compute Node` 看板及审批中心，以便直观验收和微调 `Agent` 产出的结果。

### 外部机器人免脚本导入与双侧一致标定

4. 作为机器人构建者，我希望直接上传 `URDF`、`MJCF` 或 `USD` 外部机器人压缩包，由平台在受限解包后确定性转换为规范 `Z-up` OpenUSD（`UsdPhysics`）与 `.glb` `Asset Visual` 并保留上游哈希与许可证声明，以便免除手写离线转换脚本。
5. 作为机器人构建者，我希望提交非几何标定配方（翻正朝向与比例、按体积估算缺失质量惯量并标记 `massSource: estimated`、配置连杆对碰撞过滤与接触摩擦、设置规范关节电枢惯量与标准驱动刚度阻尼、标定默认平衡站姿与 `Binding Template`），每次标定均**烘焙生成新的不可变 USD `Asset` 修订版**，并在首次批准训练后对该 `Goal` 冻结 `Asset` 版本，以便消除可变覆盖带来的可比性漂移。
6. 作为机器人构建者，我希望导入验证自动比对交互仿真构建器与训练派生构建器在质量、惯量、关节轴/限位、碰撞排除对和摩擦系数上的一致性，对闭合运动链、球/耦合关节或非封闭开口碰撞网格显式拒收并在 `Validation Report` 列出原因，同时在两侧模型上通过短时重力静置检查，以便从源头消灭训练与评估的物理鸿沟。
7. 作为真机开发者，我希望平台在翻转或重排外部 `URDF`/`MJCF` 关节轴时精确记录“源关节名 ↔ 规范 USD 关节名 ↔ 轴向正负号与零位偏移”映射元数据，以便后续导出模型装回真机时绝不会发生电机反转。

### 用户态不可变 `EntityType`、具身目录与四模式通用宿主

8. 作为平台用户，我希望在用户库中以 `u-<slug>/v<N>` 命名语法发布不可变的自定义机器人 `EntityType`（自动注入宿主必需的标准状态属性，禁止修改已发布版本或覆盖内置类型），且 `Scene` 绑定与 `Evaluation` 可比身份自动钉住其内容哈希，以便安全扩展任意新构型与自定义动作。
9. 作为平台用户，我希望通过公开写接口在用户库中只追加注册 `Embodiment`、`Robot Model` 与初始空 `Controller` 头（`currentVersion: null`，禁止覆盖同名内置 `Embodiment`/`Robot Model`，具体 `Controller` 版本仅由人工晋升事务产生），以便打通全套用户态具身身份建档。
10. 作为机器人操作者，我希望平台统一扩展既有的跨构型通用策略宿主 `embodied.policy`，原生支持 `phase_clip`（单次或循环相位/`Reference Motion` 播放）、`posture_recovery`（直立恢复）、`command_track`（有界参数指令跟踪）与 `waypoint_nav`（跨场景地面 `Waypoint` 分层速度导引）四类声明式完成原语，并通过通用 MCP 目录发现机制暴露动作，以便零仓库代码运行任何新训动作。

### 平台内确定性训练场景编排与多实体入训

11. 作为场景构建者，我希望通过 CLI、MCP 或 Web 轻量摆放控件，将固定版本的入库 `Asset`、基本体分解台阶、斜坡、起伏地面、静态碰撞障碍、动态刚体 `Entity` 的初始位姿及地面 `Waypoint`、`Teach Location` 和目标 `Site` 合成为确定性的不可变 USD `Scene Version`（排除流体与软体），以便快速搭建带地形和可交互 `Entity` 的训练与评测场。
12. 作为算法训练者，我希望在 `Task` 内定义每回合 `Entity` 的随机复位范围、指令采样分布与地形难度升降级课程，并由编译器将 `Scene Version` 的地形几何、静态障碍与声明的可交互刚体 `Entity` 完整编译进并行环境，以便让 USD 场景保持唯一事实源并复现各回合的条件。

### 编译器级资格认证、声明式 `Task` 与纯数学表达式 DSL

13. 作为终端用户与 `Agent`，我希望以声明式 `mjlab` 环境编译器作为观测资格信任根（观测契约向后兼容，并支持相位与参考帧观测源），并将任意 Python `Task` 包限定在内置或人工审计通道，以便自行完成新 `Task` 的原生观测资格核验并进入晋升流程。
14. 作为训练设计者，我希望日常动作直接通过内置标准 MDP 原语菜单（Layer A）勾选组合并在 Web 呈现权重滑块；当遇到特殊复合动作时，`Agent` 可使用基于版本化 `lw.reward-state/1` 标准张量（规范 `xyzw` 四元数与 SI 单位）的**非 Python 纯数学表达式 DSL（Layer B）**编写打分与终止项，由编译器静态限制算子节点深度并在运行时自动施加 `nan_to_num` 与幅值裁剪防护，以便彻底杜绝沙盒逃逸、版本脆弱性与罕见除零崩溃。
15. 作为审批人，我希望在 `Training Plan` 提交人工审批前，平台先在目标 `Compute Node` 与目标并行环境规模（`num_envs`）下自动执行一次短步数的**计划校验与耗时剖析 `Job`**，验证显存与表达式合法并将实测预计 GPU 小时数直接展示在审批卡片上，以便做出知情的算力审批决定。

### 单人视频 `Motion Clip` 与 `Reference Motion` 动作模仿

16. 作为平台用户，我希望上传受时长与大小约束的单人清晰全身动作视频后，由用户接入的 `Compute Node` 运行预处理 `Job` 提取构型无关的源 `Motion Clip` 并完整保留上游人体姿态模型许可证声明，以便提取一次即可尝试映射到不同机器人。
17. 作为平台用户，我希望用显式的身体部位语义映射表将 `Motion Clip`（或外部导入的关键帧）重定向为绑定指定机器人 `Asset` 版本的 `Reference Motion`。平台在目标机器人缺少必需的肢体自由度时明确拒收，支持用原地模式消除单目相机漂移，并在 `Validation Report` 中记录关键点误差、脚底滑移、关节限位饱和度与静态支撑裕度，以便我在投入训练前判断目标机器人能否模仿该动作。
18. 作为平台用户，我希望在 Web 3D 视口叠加播放目标机器人的 `Reference Motion` 幽灵预览，确认动作后再发起 `mjlab` 强化学习平衡跟踪训练。训练编译器、`embodied.policy` 宿主与导出脚本共用相位时钟和姿态 `slerp` 插值规范，以便我在训练前确认模仿目标，并在平台运行和外部推理时得到一致的动作时序。

参考跟踪训练可通过 Training Plan 选择 `lw.reference-actor-init/1`。预检和普通训练使用同一受管策略起点，续训与最终导出继续使用已学习的参数及冻结的观测归一化。该配置绑定精确的 Task、Reference Motion、观测布局及模型设置，由现有审批流程批准。

### 用户自备多 `Compute Node`、Colab 接入与安全检查点续训

19. 作为自托管平台用户，我希望开启认证远程网关模式，通过一次性注册码换取可吊销的独立节点令牌，将本机显卡、自有远程 Linux GPU 服务器或个人 Google Colab 通过外向 HTTP 轮询挂载为命名 `Compute Node`。`nodeId` 标识持久注册的节点，`instanceId` 标识该节点的一次连接。耗时剖面独立于随机主机名，本机交互会话的 CUDA 锁仅作用于同机 GPU，以便灵活调配自有算力。
20. 作为使用易断线弹性算力的用户，我希望训练 `Job` 定期回传 `Checkpoint`（纯张量 `safetensors` 和 JSON 元数据）。节点超时失联时，网关立即封禁旧租约纪元（Lease Fencing），将 `Job` 转为可恢复的 `suspended` 状态。版本兼容的 `Compute Node` 可拉取最近的 `Checkpoint` 进行统计意义续训，或运行 `checkpoint_finalize` `Job` 在节点端为评估完成 ONNX 导出与 256 样本观测捕获。平台按尝试累加实际 GPU 小时，以便我保留已完成的训练进度并核对每次尝试的支出。

### `Goal` 级封存 `Evaluation Suite`、动作复用与策略导出

21. 作为评估与优化负责人，我希望具身 `Evaluation Suite` 按 `（EntityType 动作, Goal）` 首次生成并在该 `Goal` 的首个 `Training Plan` 人工关口处冻结成功判据、随机扰动维度与样本量（默认 ≥ 30 局），由服务端独立生成并封存 `split: test` 私密种子（忽略或仅将任务自带种子用于 `train`），允许 `Agent` 免审运行 `split: train` 评估，而 `split: test` 仅在人工晋升时触发，以便保证多轮调参的统计可比性与防作弊隔离。
22. 作为实验室自动化用户与硬件开发者，我希望 `Agent` 能通过 `Operation Plan` 编排晋升动作完成巡检与跌倒自恢复，也能一键导出单个 `Policy` 的 `Policy Export`（`lw.policy-export/1`）。导出包包含 `.onnx`、源电机名及其正反转符号和零位偏移映射、四元数顺序、`previous_action` 和双重裁剪规则、执行器 SI 参数、评估资格标记、传递许可证和零平台依赖的参考推理脚本。独立进程与平台的 `float32` 推理结果在 `1e-5` 容差内一致，以便我在实验室复用可靠动作，并在对接真机前验证外部推理。

## Implementation Decisions

### 用户态类型、作业与训练契约

- **用户态 `EntityType`**。标识符采用 `u-<slug>/v<N>`，其中 `<slug>` 由小写英文字母、数字和连字符组成，`<N>` 为数字版本号。无 `u-` 前缀的命名空间保留给内置库。类型发布后不可修改，`Scene` 设备绑定用 `typeContentHash` 固定其内容，`Evaluation` 将引用的用户类型内容哈希纳入可比身份。静态预览无需全局失效。
- **`Job` 状态机与分段账单**。公开状态为 `queued`、`running`、`suspended`、`succeeded`、`failed` 和 `cancelled`。受管作业种类为 `training`、`plan_validation`、`motion_preprocess` 和 `checkpoint_finalize`。每条 `Job` 记录 `attempts[]`，包括单调递增的 `leaseId`、`nodeId`、`instanceId`、硬件摘要、起止迭代步与实耗 GPU 秒。续训前，平台从 `Training Plan` 已批预算中扣除各次既往尝试的累计实耗和其他仍占用额度的预留。如果预计完成剩余训练所需的额外 GPU 用量超过剩余额度，则触发既有超预算 `Approval Gate`。例如，获批 10 GPU 小时、已用 8 小时且无其他预留时，预计还需 4 小时，超过剩余的 2 小时，须追加审批。
- **多节点注册与安全 `Checkpoint`**。远程模式须明确启用认证，一次性注册码换取可吊销的节点专属令牌。节点心跳和派发以持久 `nodeId` 路由，每次连接使用自己的 `instanceId`，并兼容默认本机节点。耗时剖面键仅绑定任务与编译器特征、算法超参与标准化 GPU 型号。节点认领前核对锁定的训练框架版本。经网关或跨节点传输的中间 `Checkpoint` 只含 `safetensors` 张量与结构化 JSON 元数据。无论来源是否被声明为可信，网关一律禁止对 `Checkpoint` 数据执行 pickle 反序列化。节点本地训练器自行管理仅供内部使用的检查点。`Compute Node` 导出 ONNX 并采集 256 个原生观测样本，网关依据导出的模型和采集证据独立核验观测资格。
- **声明式 `Task` 与观测资格**。`Task` 锁定 `scene`、`robot`、`implements`、`control`（含控制频率和动作空间）、确定性随机化、地形课程配置及 `ObservationSpec`。平台观测资格档案钉住编译器实现哈希与已验证的 `mjlab`、`rsl_rl` 版本。`lw.obs-spec/2` 与 `lw.policy-native-observation-samples/2` 保持向后兼容，并支持 `motion_phase` 与 `reference_motion_frame`。
- **奖励表达式**。`Task` 组合 Layer A 原语列表与可选的 Layer B 纯数学表达式 AST。JSON 算子仅限标量或张量算术、范数、点积、裁剪、平滑核函数与比较选择。AST 最多 128 个节点、深度最多 12 层，禁止导入、属性反射和任意形状分配。表达式统一应用 `nan_to_num` 与幅值限幅。`lw.reward-state/1` 对世界坐标系和机体坐标系采用 `Z-up` 右手系、SI 单位与 `xyzw` 四元数顺序。
### 动作与导出契约

- **`Motion Clip` 与 `Reference Motion`**。两个产物均不可变。`Motion Clip` 记录源帧率、根轨迹、标准人体关节序列和上游体模许可证。`Reference Motion` 钉住所用 `Motion Clip`（或关键帧源）、目标 `Asset` 版本及 `contentHash`、显式肢体和站点映射、原地模式开关与重定向可行性报告。报告涵盖关键点误差、脚底滑移、关节限位饱和度、角速度界限和静态支撑多边形裕度。训练、`embodied.policy` 与导出共用姿态 `slerp` 和关节插值规范，循环动作使用 `sin/cos` 相位，一次性动作使用剩余时间并在结束后保持末帧。
- **`Policy Export` 契约**。`lw.policy-export/1` 归档包含烘焙观测归一化的静态 `.onnx` 模型、所需的 `Reference Motion` 片段和零平台依赖的 Python 参考推理脚本。`manifest.json` 记录以下约定，以支持独立进程与平台推理的 `float32` `1e-5` 容差一致性。

  - `.onnx` 张量签名与 opset、逐观测项的坐标系、`xyzw` 四元数顺序、`projected_gravity` 符号和角速度坐标系。
  - 原始未裁剪的 `previous_action` 归零与步进规则、`offset` 和 `delta` 的 `maxDelta`（相对实测关节角）、动作与物理限位的双重裁剪顺序。
  - 如资产声明了实际求解器位置控制范围，按 `actuators[].controlRange` 在物理限位之后夹紧规范目标，再逆变换成源电机目标。
  - 控制频率、物理步长和抽取比、关节 `kp`、`kv`、力矩上限和规范电枢惯量（SI 单位），以及标定基座变换矩阵。
  - 源 `URDF` 或 `MJCF` 关节名与规范关节名、轴向符号（`+1/-1`）及零位偏移之间的映射。
  - 相位时钟和插值规范、资格及评估证据状态、完整的传递许可证链。

### 双侧物理模型一致性与 `Goal` 冻结点

- 所有外部机器人导入与标定覆盖均烘焙进 OpenUSD 舞台，使用 `MassAPI`、`FilteredPairsAPI`、`MaterialAPI`、单一规范电枢惯量属性与标准 `UsdPhysics` 关节驱动。根节点须使用单位缩放。训练侧 USD 派生器读取碰撞排除对和材质摩擦，对闭链、球关节、耦合关节以及开口非体积碰撞网格显式报错。导入验证自动比对两侧的模型参数。
- 该 `Goal` 的首个 `Training Plan` 获人工批准时，冻结机器人的标定 `Asset` 版本及 `Evaluation Suite` 的成功判据、随机扰动维度和实际选定的回合数（至少 30 个随机扰动回合）。同一 `Goal` 下修订奖励时，后续评估复用同一回合数、随机扰动维度与成功判据。后续修改机器人物理标定或动作成功判据时，必须开启新 `Goal` 并重新建立初始 `Reference`。

## Testing Decisions

### 好测试的标准与真实 CI 边界

- 通过公开 HTTP API、`lw` CLI、`lw mcp`、归档字节流、节点外向轮询 HTTP 接口和真实浏览器页面验证可观察结果。对结构校验与 DSL 使用其接受或拒绝输入的边界。
- GitHub Actions 核心门禁在无 GPU、无可选 `Newton` 与 `mjlab` 重依赖的条件下运行，单作业中位耗时目标低于 4 分钟。重物理闭环走专项验收。每张子票声明所属测试车道及预计新增耗时。

### 测试接缝与既有先例

1. **S1 CI 网关公开 HTTP、CLI 和 MCP**。用 `SyntheticSource`、假时钟和假节点验证不可变用户 `EntityType` 注册与哈希钉住、只追加具身资源写接口、`Goal` 级自动 `Evaluation Suite` 冻结与私密测试种子隔离、三类 `Approval Gate`、多节点租约纪元封禁、`suspended` 挂起续训及按尝试计费。在零平台依赖的独立 `onnxruntime` 子进程中检验导出包的 `float32` `1e-5` 容差一致性，并以金标准向量校验相位与插值的三方一致性。

   既有 HTTP 测试覆盖预算、令牌、失联、租约隔离和私密种子隔离。训练计划旅程覆盖已安装的 `lw` 和官方 MCP 客户端。 [作业调度](../../tests/gateway/test_training_jobs.py) · [评估隔离](../../tests/gateway/test_evaluation_api.py) · [CLI 与 MCP](../../tests/cli/test_training_commands.py)

2. **S2 CI 归档、结构和 DSL**。使用 `URDF` 与 `MJCF` 模糊语料验证解包及模型准入，涵盖路径穿越、符号链接、越界引用、开口网格、闭链和不支持的关节。检验烘焙后的 USD 结构、派生求解器表示和源关节正反转映射，以及 Layer B 越界和非法算子拒收与 `nan_to_num` 边界。

   既有归档测试验证路径越界和链接的拒收，USD 派生测试检查关节层级、惯量、几何和标准驱动。 [归档导入](../../tests/gateway/test_asset_imports.py) · [USD 派生](../../tests/unit/test_mjlab_task_and_usd_bridge.py)

3. **S3 本地 Newton CPU 闭环**。在安装物理扩展的 Linux 环境检验 Newton 与训练派生模型的一致性报告、双侧重力静置、`embodied.policy` 的四模式闭环、`Agent` 多步 `Operation Plan` 编排与正式 CPU `Evaluation` 晋升。

   既有自定义非机械臂具身测试通过公开接口贯通 Newton CPU 推理、`Evaluation` 与多动作晋升。 [CPU 物理闭环](../../tests/sim_runtime/test_custom_embodied_closed_loop.py)

4. **S4 真实 GPU 与跨节点实测**。在本机、远程 Linux GPU 和 Colab 式外向连接环境实测计划预检及其耗时剖析、`mjlab` 多实体及台阶地形并行训练，以及中断后从 `safetensors` `Checkpoint` 跨节点续训。记录从单人视频提取 `Motion Clip`、重定向 `Reference Motion` 到模仿训练达标并导出 `Policy Export` 的全程验收结果。

   既有 Microduck 验收从 RTX 3090 `mjlab` 训练衔接 ONNX 导出、256 样本原生观测资格和 Newton CPU 评估。 [Microduck 实训验收](https://github.com/CaiZongyuan/spatial-sync/pull/288)

5. **S5 浏览器端到端**。通过 Playwright 分片与 `SyntheticSource` 验证拖拽上传、幽灵动画预览、轻量摆场与滑块、多节点看板、审批中心及 `Policy Export` 下载。

   既有 Playwright 旅程通过真实 HTTP 检查训练审批、`Job` 与假节点遥测。 [Web 训练与算力](../../tests/e2e/training-resources.spec.ts)

## Out of Scope

- **平台内三维网格自由曲面建模与模块化零件拼装 CAD**（留待后续独立迭代）。
- **Web 前端内嵌大模型聊天运行时**（本期统一由外部 `Agent Runtime` 经 CLI / MCP 驱动）。
- **多租户公有云共享算力池与陌生人不可信算力撮合**（本期明确为用户自托管平台 + 用户接入自有/自开计算节点）。
- **任意野生动物或多主体混乱视频的无标定三维骨骼提取、流体/布料软体训练、VLA 大模型微调，以及特定真机底层串口/总线嵌入式固件驱动**。
- **单场景多台独立策略机器人同时晋升授权**（延续单场景单受控策略主体约束，多智能体协同另立规格）。
