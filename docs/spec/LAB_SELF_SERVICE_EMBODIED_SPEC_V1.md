# Agent 优先的用户自助具身平台闭环（总规格）

> 对应总规格 Issue：[#290](https://github.com/CaiZongyuan/spatial-sync/issues/290) · 关联决策：[ADR-0003](../adr/0003-external-asset-preparation-boundary.md)、[ADR-0004](../adr/0004-scene-authoring-through-external-factories.md)、[ADR-0010](../adr/0010-user-space-entity-types-and-embodied-resources.md)、[ADR-0011](../adr/0011-declarative-mjlab-compiler-trust-root-and-reward-dsl.md)、[ADR-0012](../adr/0012-user-owned-multi-node-compute-and-checkpoint-resumption.md)、[ADR-0013](../adr/0013-user-owned-video-imitation-and-policy-exports.md)
>
> Umbrella Specification v1.1 · 2026-11-19 · 基于 `/grill-with-docs` 访谈与 Claude Opus 5.5（`xhigh`）独立代码审查修订收口，已同步更新至 GitHub Issue [#290](https://github.com/CaiZongyuan/spatial-sync/issues/290) 已拆分子票 [#291](https://github.com/CaiZongyuan/spatial-sync/issues/291)–[#300](https://github.com/CaiZongyuan/spatial-sync/issues/300) 待实施（本文描述已确认目标规格，尚未合入运行时实现）。

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

双足 Microduck 标杆验证了 **Lab World** 从规范 USD `Asset`、MuJoCo Warp 并行训练、确定性物理 `Evaluation` 到跨场景运行的底层链路，但对照真实代码库审视，普通用户或外部 `Agent` 若想在平台上搭建/接入自己的机器人并训练自定义场景与动作，仍面临 **六道“只有平台工程师改仓库才能跨过”的硬阻断**：

1. **外部机器人格式与双侧物理保真度断层**：平台导入接口只接受手工打包好的 `.usd` + `.glb` + 绑定清单，无法直接接入主流的 `URDF` / `MJCF` 机器人包；更关键的是，评估侧（Newton USD 导入器）与训练侧（USD 到求解器派生器）当前丢失了碰撞排除对、接触摩擦与闭链/开口网格告警，导致用户导入的机器人在训练与仿真两侧物理响应不一致甚至初始自碰撞爆炸。
2. **具身资源只读且自定义类型缺乏不可变钉住机制**：`Embodiment`、`Robot Model` 与 `Controller` 尚无用户态写接口；自定义设备类型目前仅靠测试环境变量注入，未纳入 `Scene` 内容哈希钉住与 `Evaluation` 可比身份计算，用户无法安全地定义新机器人构型或自定义动作名。
3. **观测资格审查信任根锁死在仓库逐任务白名单文件**：现有策略资格审查按 `taskContentHash` 精确查找仓库内置白名单档案——任何由用户或 `Agent` 在平台新建的 `Task` 都会因缺少仓库白名单文件直接报错 `observation_source_unverified`，从源头阻断策略晋升。
4. **训练 `Scene` 与 `Task` 耦合且并行训练退化为空平地**：训练任务仍依赖打包安装数百行自定义 Python 环境入口，且在启动并行环境时丢弃了 `Scene Version` 中的台阶、斜坡、障碍物与交互刚体道具，只抽单一机器人放在无限平地上。
5. **缺乏从人类视频直观迁移技能的可行性链路**：当用户希望机器人学会挥手、鞠躬、舞蹈、踢腿等动作时，缺少“单人视频提取源 `Motion Clip` → 按肢体语义映射与可行性体检生成绑定机器人的 `Reference Motion` → Web 幽灵预览 → 强化学习平衡跟踪”的产品闭环。
6. **算力锁死单本机回环，且训练产物缺少清晰的内外双出口**：计算节点地址强制回环且绑定单本机身份、耗时剖面混入临时主机名、节点失联即判终态失败且不回传中间检查点，导致用户无法稳定挂载远程服务器或易断线的个人 Colab；同时平台缺少包含源电机正反转符号映射表的独立 `Policy Export` 导出包来衔接外部工程与后续 Sim2Real 真机落地。

## Solution

以 **“用户自备算力自托管 + 外部 CLI / MCP `Agent` 优先包办 + 轻量 Web 可视化工作台预览微调 + 关键成本与质量 `Approval Gate` 把关”** 为顶层原则，按以下八个正交模块构建完整产品闭环：

1. **双侧一致的外部机器人免脚本导入与烘焙标定（ADR-0003 修订）**：支持上传 `URDF`、`MJCF` 与 `USD` 机器人压缩包，将坐标轴/尺度归一化、体积质量惯量估算、连杆对碰撞过滤、接触摩擦、规范关节电枢惯量、标准 `UsdPhysics` 驱动参数与初始平衡站姿**直接烘焙为新的不可变 OpenUSD `Asset` 版本**，保留“源关节名 ↔ 规范关节名/轴向符号/零位偏移”映射表，并在 `Validation Report` 中强制执行仿真侧与训练派生侧的模型一致性校验（对闭链、球关节、开口碰撞网格等不支持结构显式拒收告警，绝不静默丢弃）。同一 `Goal` 在首次训练审批后冻结机器人 `Asset` 版本。
2. **不可变用户态 `EntityType`、只追加具身目录与统一四模式策略宿主（ADR-0010）**：开放 `u-<slug>/v<N>` 语法的不可变用户态 `EntityType` 发布（`Scene` 绑定与评估可比身份均钉住其内容哈希，自动注入标准状态属性），开放用户库只追加创建 `Embodiment`、`Robot Model` 与 `Controller` 头记录（具体 `Controller` 版本仍仅由人工晋升事务签发），并通过通用 MCP 目录发现暴露动作；在现有通用策略宿主 `embodied.policy` 上统一扩展 `phase_clip`、`posture_recovery`、`command_track`、`waypoint_nav` 四种声明式完成原语。
3. **平台内确定性训练 `Scene` 编排与多实体入训编译（ADR-0004 修订）**：`Scene Version` 严格只承载确定性烘焙场地布局（基本体分解台阶、斜坡、静态碰撞障碍、动态刚体道具初始位姿及 `Waypoint` / `Teach Location` / `Site`，不含流体与软体），由 `mjlab` 编译器完整载入并行环境；而每回合重置随机化、指令采样与地形难度课程统一归入 `Task`。
4. **编译器级资格信任根、声明式 `Task` 与纯数学奖励表达式 DSL（ADR-0011）**：将平台声明式 `mjlab` 环境编译器设为自助通道的唯一观测资格信任根（任意 Python `Task` 包限制为内置或人工审计通道）；内置标准 MDP 原语菜单（Layer A）覆盖常规动作，长尾任务通过基于版本化 `lw.reward-state/1` 张量（统一 `Z-up` SI 单位与 `xyzw` 四元数）的非 Python 纯数学表达式 DSL（Layer B，带节点数上限与运行时 `nan_to_num`/限幅防护）表达；在 `Training Plan` 人工审批前自动运行目标环境规模下的**计划校验与耗时剖析 `Job`**，将实测 GPU 小时估算附于审批单。
5. **绑定 `Goal` 的服务端封存 `Evaluation Suite`（ADR-0011）**：考卷按 `（EntityType 动作, Goal）` 生成并在该 `Goal` 首次 `Training Plan` 人工关口处冻结成功判据与扰动参数（默认 ≥ 30 局随机扰动回合），由服务端独立封存 `split: test` 种子，确保多轮奖励调优前后严格可比；首个达标候选在晋升关口由人工确立为初始 `Reference`。
6. **单人视频 `Motion Clip` 与机器人 `Reference Motion` 模仿闭环（ADR-0013）**：在用户接入的 `Compute Node` 上从单人清晰视频提取构型无关的源 `Motion Clip`（保留上游人体模型许可证声明），经显式肢体部位语义映射与可行性体检（无映射自由度拒收、脚底滑移、关节限位饱和度、质心支撑裕度、可选原地模式）生成绑定具体机器人 `Asset` 版本的 `Reference Motion`，经 Web 3D 幽灵预览确认后启动 `mjlab` 平衡模仿训练，并由三方共用金标准规范对齐相位时钟与插值。
7. **用户自备多 `Compute Node`、安全 `Checkpoint` 与按尝试分段续训（ADR-0012）**：放开认证远程网关模式，签发一次性注册码换取可吊销单节点令牌，剥离耗时剖面中的临时主机名并将本机交互会话 CUDA 锁限定在同机 GPU；训练期间定期回传纯张量 `safetensors` 格式 `Checkpoint`（ONNX 导出与 256 样本原生观测捕获始终在计算节点完成），节点断线后转为可恢复 `suspended` 态，支持在兼容节点续训并按每次尝试分段累加计费。日常保持“批准 `Training Plan`”与“批准 `Controller` 晋升”两道人工关口，同时保留换慢卡续训超出原批预算时的“超预算追加审批”兜底关口。
8. **内外双价值出口与 `Policy Export` 真机对接包（ADR-0013）**：晋升动作对内供外部 `Agent` 在 `Operation Plan` 中通过通用动作管线自主编排复杂任务；对外提供单 `Policy` 级 `Policy Export`（`lw.policy-export/1`）一键导出，内含 `.onnx` 模型、源 `URDF`/`MJCF` 关节名与正反转符号/零位偏移对照表、四元数顺序、`previous_action` 与双重限位裁剪规则、执行器 SI 参数、许可证声明及零平台依赖参考推理脚本（`float32` `1e-5` 容差一致）。

## User Stories

### Agent 优先协作与人工关口治理
1. 作为使用外部 `Agent Runtime` 的研究员，我希望通过 `lw` CLI、通用 `lw mcp` 工具与 `Platform Skill` 让 `Agent` 代办机器人导入标定、搭场景、转视频、提交预检、发起 `split: train` 评估与导出策略包，以便无需手写配置或拼装脚本。
2. 作为审批人，我希望日常准备、预览与 `split: train` 评估对 `Agent` 免审放行，仅在“批准 `Training Plan` 开训（含冻结新 `Goal` 考卷判据）”、“批准 `Controller` 晋升上架”以及“跨节点续训预计超出原批 GPU 预算”这三类关口要求人工审批，以便兼顾流畅度与算力/质量安全。
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
11. 作为场景构建者，我希望通过 CLI / MCP 或 Web 轻量摆放控件，将已入库 `Asset`、基本体分解台阶/斜坡/起伏地面、静态碰撞障碍物、动态刚体道具初始摆放以及地面 `Waypoint` / `Teach Location` / 目标 `Site` 合成为确定性的不可变 USD `Scene Version`（排除流体与软体），以便快速搭建非空平地训练与评测场。
12. 作为算法训练者，我希望“每回合道具随机复位范围、指令采样分布与地形难度升降级课程”严格定义在 `Task` 中而非混入 `Scene Version`，且训练编译器将所选 `Scene Version` 的地形几何、静态障碍与声明交互刚体完整编译进并行环境，以便保持 USD 场景唯一事实源与跨回合可复现性。

### 编译器级资格认证、声明式 `Task` 与纯数学表达式 DSL
13. 作为终端用户与 `Agent`，我希望平台将内置声明式 `mjlab` 环境编译器作为统一的观测资格信任根（支持向后兼容的相位与参考帧观测源），使所有走声明式通道新建的 `Task` 无需工程师往仓库提交白名单文件即可完成原生观测资格核验并进入晋升流程，同时将任意 Python `Task` 包限制为内置或人工审计通道。
14. 作为训练设计者，我希望日常动作直接通过内置标准 MDP 原语菜单（Layer A）勾选组合并在 Web 呈现权重滑块；当遇到特殊复合动作时，`Agent` 可使用基于版本化 `lw.reward-state/1` 标准张量（规范 `xyzw` 四元数与 SI 单位）的**非 Python 纯数学表达式 DSL（Layer B）**编写打分与终止项，由编译器静态限制算子节点深度并在运行时自动施加 `nan_to_num` 与幅值裁剪防护，以便彻底杜绝沙盒逃逸、版本脆弱性与罕见除零崩溃。
15. 作为审批人，我希望在 `Training Plan` 提交人工审批前，平台先在目标 `Compute Node` 与目标并行环境规模（`num_envs`）下自动执行一次短步数的**计划校验与耗时剖析 `Job`**，验证显存与表达式合法并将实测预计 GPU 小时数直接展示在审批卡片上，以便做出知情的算力审批决定。

### 单人视频 `Motion Clip` 与 `Reference Motion` 动作模仿
16. 作为平台用户，我希望上传受时长与大小约束的单人清晰全身动作视频后，由用户接入的 `Compute Node` 运行预处理 `Job` 提取构型无关的源 `Motion Clip` 并完整保留上游人体姿态模型许可证声明，以便提取一次即可尝试映射到不同机器人。
17. 作为平台用户，我希望将 `Motion Clip`（或外部导入关键帧）通过显式的身体部位语义映射表重定向到指定机器人 `Asset` 版本以生成 `Reference Motion`：若目标机器人缺失动作必需的肢体自由度（如无手臂机器人模仿纯挥手）则明确拒收告警，支持开启“原地模式”消除单目相机漂移，并在 `Validation Report` 中记录关键点误差、脚底滑移、关节限位饱和度与静态支撑裕度。
18. 作为平台用户，我希望先在 Web 3D 视口观看目标机器人叠加播放 `Reference Motion` 的幽灵预览动画确认动作神似，再发起 `mjlab` 强化学习平衡跟踪训练，且训练编译器、`embodied.policy` 宿主与导出脚本三方严格共用同一套相位时钟与姿态 `slerp` 插值规范。

### 用户自备多 `Compute Node`、Colab 接入与安全检查点续训
19. 作为自托管平台用户，我希望开启认证远程网关模式，通过一次性注册码换取可吊销的独立节点令牌，将本机显卡、自有远程 Linux GPU 服务器或个人 Google Colab 通过外向 HTTP 轮询挂载为命名 `Compute Node`（区分持久 `nodeId` 与单次连接 `instanceId`，耗时剖面脱钩随机主机名，且本机交互会话 CUDA 锁不阻塞远程节点），以便灵活调配自有算力。
20. 作为使用易断线弹性算力的用户，我希望训练 `Job` 按间隔自动回传纯张量 `safetensors` + JSON 元数据格式的 `Checkpoint`；当节点超时失联时，旧租约纪元被立即栅栏封禁（Lease Fencing），`Job` 转为可恢复 `suspended` 态，可在任意训练环境版本兼容的 `Compute Node` 上拉取最近 `Checkpoint` 进行统计意义续训（或派发专用收尾 `Job` 在节点端完成 ONNX 导出与 256 样本观测捕获以供评估），并按每次尝试分段累加 GPU 小时账单。

### `Goal` 级封存考卷与内外双价值出口
21. 作为评估与优化负责人，我希望具身 `Evaluation Suite` 按 `（EntityType 动作, Goal）` 首次生成并在该 `Goal` 的首个 `Training Plan` 人工关口处冻结成功判据、随机扰动维度与样本量（默认 ≥ 30 局），由服务端独立生成并封存 `split: test` 私密种子（忽略或仅将任务自带种子用于 `train`），允许 `Agent` 免审运行 `split: train` 评估，而 `split: test` 仅在人工晋升时触发，以便保证多轮调参的统计可比性与防作弊隔离。
22. 作为实验室自动化用户与硬件开发者，我希望晋升后的动作既能在平台内由大模型 `Agent` 通过 `Operation Plan` 自动编排完成复杂巡检与跌倒自恢复作业，又能一键导出单 `Policy` 级 `Policy Export`（`lw.policy-export/1`）压缩包（含 `.onnx`、源电机名/正反转符号/零位偏移映射表、四元数顺序、`previous_action` 与双重裁剪规则、执行器 SI 参数、评估资格标记、传递许可证及零平台依赖参考推理脚本），在独立进程中与平台推理保持 `float32` `1e-5` 容差一致。

## Implementation Decisions

### 1. 关键 Schema 与公开契约定义
- **用户态 `EntityType` 契约**：标识符严格采用 `u-<slug>/v<N>`（匹配既有 `[a-z][a-z0-9-]*/v\d+` 语法，保留无 `u-` 前缀命名空间归内置库独占），一经发布不可修改；`Scene` 设备绑定新增 `typeContentHash` 钉住字段，评估可比身份摘要将引用的用户态 `EntityType` 内容哈希纳入环境输入，静态预览无需全局连坐失效。
- **`Job` 状态机与分段账单契约**：公开 `Job` 状态扩展为 `queued | running | suspended | succeeded | failed | cancelled`，受管作业种类覆盖 `training`、`plan_validation`、`motion_preprocess` 与 `checkpoint_finalize`；每条 `Job` 记录 `attempts[]`（含单调递增 `leaseId`、`nodeId`、`instanceId`、硬件摘要、起止迭代步、实耗 GPU 秒），总计费为各次尝试实耗之和；当换慢卡续训重估剩余耗时超出已批 `Training Plan` 预算时，复用既有超预算 `Approval Gate`。
- **多节点注册与安全 `Checkpoint` 契约**：新增远程认证网关开关与一次性节点注册码换取独立作用域令牌接口，节点心跳与作业路由参数化 `nodeId`（保留默认本机节点兼容）；耗时剖面键去除主机名，仅绑定任务/编译器特征、算法超参与标准化 GPU 型号特征；训练节点认领作业前强制核对锁定的训练框架版本集合；中间 `Checkpoint` 仅允许上传 `safetensors` 权重与结构化 JSON 状态，严禁在网关反序列化 pickle 或执行模型导出。
- **声明式 `Task`、`lw.reward-state/1` 与观测契约演进**：声明式 `Task` Schema 锁定 `scene`、`robot`、`implements`、`control`、确定性随机化与地形课程配置、`ObservationSpec`（升级至向后兼容的 `lw.obs-spec/2`，新增 `motion_phase` 与 `reference_motion_frame` 源；同步升级 `lw.policy-native-observation-samples/2`）以及 Layer A 原语列表 + Layer B 纯数学表达式 AST（JSON 算子表：仅限标量/张量算术、范数、点积、裁剪、平滑核函数与比较选择，节点数 ≤ 128、深度 ≤ 12，零属性反射与任意形状分配，外层统一包裹 `nan_to_num` 与幅值限幅）。`lw.reward-state/1` 统一采用世界/机体 `Z-up` 右手系、SI 单位与 `xyzw` 四元数顺序。
- **`Motion Clip`、`Reference Motion` 与 `Policy Export`（`lw.policy-export/1`）契约**：`Motion Clip` 记录源帧率、根轨迹、标准人体关节序列与上游体模许可证；`Reference Motion` 钉住源 `Motion Clip`（或关键帧源）、目标 `Asset` 版本及 `contentHash`、显式肢体/站点映射表、原地模式开关与重定向可行性报告；`lw.policy-export/1` 清单完整包含 `.onnx` 张量签名与 opset、逐观测项坐标系与 `xyzw` 四元数约定、`projected_gravity` 符号、原始未裁剪 `previous_action` 归零与步进规则、`offset`/`delta` 的 `maxDelta`（相对实测关节角）及动作/物理限位双重裁剪顺序、控制频率/物理步长/抽取比、关节 `kp`/`kv`/力矩上限/规范电枢惯量、标定基座变换矩阵、**源 `URDF`/`MJCF` 关节名 ↔ 规范关节名 ↔ 轴向符号（`+1/-1`）与零位偏移映射表**、相位/插值规范、资格证据状态与完整许可证链。

### 2. 双侧物理模型一致性与 `Goal` 冻结点
- 所有外部机器人导入与标定覆盖必须烘焙写入生成的 OpenUSD 舞台（`MassAPI`、`FilteredPairsAPI`、`MaterialAPI`、单一规范电枢惯量属性与标准 `UsdPhysics` 关节驱动），根节点禁止非单位缩放；训练侧 USD 派生器同步补齐碰撞排除对、材质摩擦读取及对闭链、球/耦合关节、开口非体积碰撞网格的显式错误报告，并在导入验证阶段自动执行双侧模型参数一致性断言。
- 机器人的标定 `Asset` 版本与 `Goal` 级 `Evaluation Suite` 均在该 `Goal` 的首个 `Training Plan` 通过人工关口时冻结；后续若修改机器人物理标定或修改动作成功判据，必须开启新 `Goal` 并重新建立初始 `Reference`。

## Testing Decisions

### 好测试的标准与真实 CI 边界
- **只测公开边界外部可观测行为**：所有测试通过公开 HTTP API、`lw` CLI、`lw mcp`、归档字节流、节点外向轮询 HTTP 接口及真实浏览器页面驱动，绝不断言私有内部函数或实现类。
- **尊重仓库真实 CI 约束（GitHub Actions 无 GPU、不安装可选 `Newton` 与 `mjlab` 重依赖，单作业中位耗时 < 4 分钟）**：自动化门禁严格按依赖分层，绝不把需要 `Newton` 或 `mjlab` 的重物理闭环塞入核心网关 CI 分片，同时每张子票明确声明所属测试车道与预计新增耗时。

### 重构后的五层测试接缝（Testing Seams）
1. **接缝 S1（CI 核心门禁 —— 网关公开 HTTP / CLI / MCP + `SyntheticSource` + 假时钟/假节点替身）**：验证用户态 `u-<slug>/v<N>` 不可变类型注册与哈希钉住、只追加具身资源写接口、`Goal` 级自动 `Evaluation Suite` 冻结与私密 `test` 种子隔离、三类 `Approval Gate`、`Job` 多节点租约纪元封禁/`suspended` 挂起续训/按尝试分段累加计费，以及 `lw.policy-export/1` 导出包在独立零平台依赖 `onnxruntime` 子进程中的 `float32`（`1e-5`）数值一致性与相位/插值三方金标准向量一致性。
2. **接缝 S2（CI 结构与编译器门禁 —— OpenUSD + 派生求解器静态结构校验 + Layer B DSL 语料库）**：覆盖 `URDF`/`MJCF` 归档安全解包模糊语料（路径穿越、符号链接、越界引用、开口网格、闭链与不支持关节显式拒收）、标定烘焙后 USD 结构与源关节正反转映射表校验，以及 Layer B 表达式 DSL 越界/非法算子拒收与 `nan_to_num` 边界单测。
3. **接缝 S3（本地/专项 CPU 物理验收 —— 真实 `Newton` CPU 闭环）**：在安装物理扩展的本地 Linux 环境中验证 `Newton` 与训练派生模型的双侧物理一致性报告、双侧重力静置检查、`embodied.policy` 四种动作完成模式闭环、`Agent` 多步 `Operation Plan` 编排与正式 CPU 物理 `Evaluation` 晋升全流程。
4. **接缝 S4（真实 GPU 与跨节点实测证据）**：在本机/远程 Linux GPU 节点及 Colab 式外向连接环境下实测计划预检耗时剖析、`mjlab` 多实体/台阶地形并行训练、中断后从 `safetensors` `Checkpoint` 跨节点续训，以及单人动作视频提取 `Motion Clip` → 重定向 `Reference Motion` → 模仿训练达标并导出 `Policy Export` 的完整验收记录。
5. **接缝 S5（Web 工作台浏览器端到端接缝 —— Playwright 分片 + `SyntheticSource`）**：验证拖拽上传、幽灵动画预览、轻量摆场与滑块、多 `Compute Node` 状态看板、审批中心交互与 `Policy Export` 下载。

## Out of Scope

- **平台内三维网格自由曲面建模与模块化零件拼装 CAD**（留待后续独立迭代）。
- **Web 前端内嵌大模型聊天运行时**（本期统一由外部 `Agent Runtime` 经 CLI / MCP 驱动）。
- **多租户公有云共享算力池与陌生人不可信算力撮合**（本期明确为用户自托管平台 + 用户接入自有/自开计算节点）。
- **任意野生动物或多主体混乱视频的无标定三维骨骼提取、流体/布料软体训练、VLA 大模型微调，以及特定真机底层串口/总线嵌入式固件驱动**。
- **单场景多台独立策略机器人同时晋升授权**（延续单场景单受控策略主体约束，多智能体协同另立规格）。

## Further Notes

本总规格按最小风险依赖顺序拆分为以下 10 张实施子票（子票挂接于本 Issue 下并通过原生 `blocked_by` 依赖边管理前线）：
- **M1（[#291](https://github.com/CaiZongyuan/spatial-sync/issues/291)）**：USD↔MJCF 模型保真度补齐（碰撞过滤、摩擦、规范电枢、非法拓扑显式拒收）与双侧模型一致性校验
- **M2a（[#292](https://github.com/CaiZongyuan/spatial-sync/issues/292)）**：用户自备多 `Compute Node` 远程认证接入、独立节点令牌与计划校验耗时剖析 `Job`
- **M2b（[#293](https://github.com/CaiZongyuan/spatial-sync/issues/293)）**：训练 `Job` `suspended` 挂起态、`safetensors` 安全 `Checkpoint` 回传与跨节点按尝试分段计费续训（依赖 M2a）
- **M3（[#294](https://github.com/CaiZongyuan/spatial-sync/issues/294)）**：用户态不可变 `EntityType`（`u-*/vN`）哈希钉住、只追加具身资源写接口与评估可比身份对齐
- **M4（[#295](https://github.com/CaiZongyuan/spatial-sync/issues/295)）**：声明式 `mjlab` 环境编译器、编译器级观测资格信任根、`embodied.policy` 四模式扩展与 `Policy Export` 导出包（依赖 M1、M3）
- **M5（[#296](https://github.com/CaiZongyuan/spatial-sync/issues/296)）**：绑定 `Goal` 的服务端自动 `Evaluation Suite`（首次开训关口冻结判据、≥30 扰动回合与私密种子封存）（依赖 M4）
- **M6（[#297](https://github.com/CaiZongyuan/spatial-sync/issues/297)）**：外部 `URDF` / `MJCF` 机器人确定性导入、烘焙式标定配方、源关节正反转映射表与双侧重力静置检查（依赖 M1、M3）
- **M7（[#298](https://github.com/CaiZongyuan/spatial-sync/issues/298)）**：平台内确定性训练 `Scene` 编排、多实体入训编译与 Layer B 纯数学表达式奖励 DSL（依赖 M2a、M4）
- **M8（[#299](https://github.com/CaiZongyuan/spatial-sync/issues/299)）**：单人视频 `Motion Clip` 提取、跨构型 `Reference Motion` 重定向可行性体检与动作模仿训练（依赖 M2b、M4、M6）
- **M9（[#300](https://github.com/CaiZongyuan/spatial-sync/issues/300)）**：全链路自助具身 `Platform Skill` 升级与轻量 Web 可视化工作台整合（依赖 M5、M6、M7、M8）
