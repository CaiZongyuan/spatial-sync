# Lab Assets and Scenes

> **素材与场景管理 · Technical Specification v1.0**
> 日期：2026-10-18
> 状态：Approved direction，待实施（未决项集中在 §15，均有默认值）
> 前置：[Lab Information Foundation](LAB_INFORMATION_FOUNDATION_SPEC_V1.md)（信息平面）与 [v1.2](NEWTON_SPATIAL_SYNC_SPEC_V1.2.md)（空间平面）。本规格只描述新增与改动。
> 姊妹规格：[Lab Training](LAB_TRAINING_SPEC_V1.md)、[Lab Live Operations](LAB_LIVE_OPERATIONS_SPEC_V1.md)。三份规格共用 §1 的北极星与 §10 的 agent 接口。
> 术语：以仓库根目录 [GLOSSARY.md](../../GLOSSARY.md) 为准。格式决策见 [ADR 0001](../adr/0001-usd-canonical-scene-format.md)。
> 总览与验收：[Lab RSI Platform PRD](LAB_RSI_PLATFORM_PRD_V1.md)（用户故事、交付顺序、测试接缝、错误码）；界面：[Lab Web UI](LAB_WEB_UI_SPEC_V1.md)。

## 0. 一句话定义

平台新增一个**库**：人和 agent 在这里看到实验室有哪些**素材**（形体）、哪些**场景版本**（布好的实验室），每样东西能做什么、缺什么、能不能启动。场景以 USD 为规范格式，经验证后才能启动会话。

## 1. 北极星：RSI 闭环

RSI 指 Recursive Self-Improvement：agent 通过平台闭环地改进实验室和机器人能力。

```text
素材 ─► 场景 ─► 训练任务 ─► 训练计划 ─► 作业 ─► 评估 ─► 策略 ─► 部署为设备程序
  ▲                              （人工关口）              （人工关口）      │
  └──────────── agent 读失败录制，改素材 / 场景 / 奖励 ◄──────────────────────┘
```

- 本规格交付闭环的前两环：素材与场景。训练、评估、策略见 [Lab Training](LAB_TRAINING_SPEC_V1.md)。
- 人只在**人工关口**介入（§10.4）。其余步骤 agent 可以自主执行。
- 闭环之所以成立，是因为交互仿真（平台 Newton 会话）和批量训练（Isaac Lab）读同一份 USD 场景，`entityKey` 相同（ADR 0001）。

## 2. 目标与范围

### 2.1 目标体验

1. 打开**素材库**：看到 FR3、离心机、生物安全柜、试管、试管架等素材，每个都有缩略图、尺寸、来源与许可、完整度徽章；离心机详情里列出它能实现的 `centrifuge/v1` 及其动作说明。
2. 打开**场景库**：看到 4 个内置场景和导入的 `fr3-lab`；`fr3-lab@1` 标红并写明"示教位置 centrifuge.slot3 不可达"，`fr3-lab@2` 可启动。
3. 在 `fr3-lab@2` 详情页点"启动会话"，浏览器里看到带 PBR 布景的实验室；离心机、机械臂、试管可按信息规格的方式操作。
4. agent 执行 `lw scene show fr3-lab@2 --json`，一次拿到实体、设备能力、示教位置、缺口与验证结果，然后开始操作。

### 2.2 包含

- 库：目录布局、内置库与用户库、版本与内容哈希。
- 素材包与绑定模板；场景包。
- 验证报告：结构检查与物理检查；可启动规则；完整度与缺口。
- 场景发现与按版本启动会话，替换硬编码的 `SCENES`。
- Runner 加载 USD 场景：实体进 Newton 模型，布景只进 Manifest。
- 录制格式 v2：记录场景版本。
- HTTP API、`lw asset` / `lw scene` 命令、对应的通用 MCP 工具。
- 平台 skill 体系与 `lw skills install`（§10）。
- Web：新导航、素材库页、场景库页、场景详情页。
- `fr3-lab` 的导入演示。

### 2.3 不包含

- 场景搭建与建模工具：Web 内编辑、agent 在平台内放置物体、Blender 导出插件。素材和场景由平台外的**素材工厂**制作，以场景包或素材包格式导入（§11.3）。
- 视觉检查（自动渲染评审）与 Isaac Sim `omni.asset_validator` 第二意见。
- 场景变体与初始条件预设、域随机化（训练规格处理）。
- Synthetic 源运行 USD 场景。
- 远程库存储、多用户、权限。

## 3. 与已有工作的关系

| 已有内容 | 本规格中的处理 |
|---|---|
| `session_manager.py` 硬编码 `SCENES` | 删除，改为库发现（§6.1） |
| `packages/sim_runtime/scenes.py` 的 `SCENE_CONFIGS` | 保留为 Python builder 注册表，由内置场景包的 `stage: python:<id>` 引用 |
| `devices/scenes/*.yaml`、`*.locations.yaml` | 迁入对应内置场景包的 `devices.yaml`、`locations.yaml` |
| `devices/types/*.yaml` | 不变；EntityType 仍放在 `devices/types/` |
| `assets/registry.yaml`、`/api/v1/assets/{id}` | 保留给内置 Web 资源；库素材经新路由提供（§9） |
| `assets/robots/franka_emika_panda/` | 包装为内置素材 `robot/fr3@1` |
| 信息规格 §2.2 "Asset / Equipment Model → EntityType" | 改为 "Asset → Asset（形体）；Equipment Model → EntityType（行为）" |
| 信息规格 §1.3 "Blender/UE5 导出" 不包含 | 仍不包含；本规格只定义导入格式 |
| 录制 `formatVersion: 1` | 升为 2，读取器继续支持 1（§8） |

## 4. 库

### 4.1 两个根

| 根 | 位置 | 可写 | 内容 |
|---|---|---|---|
| 内置库 | 仓库 `library/` | 否（随代码发布） | 4 个内置场景、`robot/fr3@1` 等小型素材；CI 夹具 |
| 用户库 | `LW_LIBRARY`，默认 `~/.spatial-sync/library` | 是 | 导入的素材与场景，以及训练规格的任务、作业产物、策略 |

同一 ID 在两个根中同时出现时，网关启动失败并报冲突。大文件（`.blend`、高模、贴图）只进用户库，不进 git。

### 4.2 目录布局

```text
<root>/
  assets/<kind>/<name>/<rev>/      素材包（§5）
  scenes/<name>/<rev>/             场景包（§6）
  tasks/ policies/ jobs/           训练规格定义
```

### 4.3 身份与版本

- 素材 ID：`<kind>/<name>@<rev>`，如 `device/labspin-centrifuge@1`。`kind ∈ robot / device / labware / environment`。
- 场景版本 ID：`<name>@<rev>`，如 `fr3-lab@2`。
- `name` 用小写字母、数字和连字符；`rev` 是从 1 开始的整数，由导入分配。
- 每个版本记录 `contentHash`：对包内全部文件按相对路径排序后的 SHA-256 树哈希。重复导入内容相同的包，返回已有版本，不新建版本。
- 版本不可变。修改即导入新版本。删除版本是人工关口（§10.4）；被场景版本、录制或训练产物引用的版本不能删除。
- 省略 `@rev` 时，读取类命令解析为最新版本；`launch` 解析为最新**可启动**版本。

## 5. 素材包

### 5.1 内容

```text
assets/device/labspin-centrifuge/1/
  asset.yaml           元数据、物理、绑定模板
  asset.usda           形体与物理（UsdPhysics），根 prim 为 /Asset
  visual.glb           PBR 视觉（Z-up，米制）；可由 USD 派生
  collision/           可选：人工制作的碰撞几何
  provenance.json      来源：上游仓库与提交、制作工具与版本、文件哈希
  LICENSE  NOTICE      许可原文
  validation.json      验证报告（导入时生成）
  previews/            缩略图（导入时生成或随包提供）
```

### 5.2 `asset.yaml`

```yaml
id: device/labspin-centrifuge
kind: device
title: LabSpin 台式离心机
description: 四孔位台式离心机，开盖组件可独立运动
stage: asset.usda
visual: visual.glb
dimensionsM: [0.671, 0.548, 0.382]      # 包围盒，校验用
physics:
  massKg: 18.0
  massSource: estimated                  # measured | datasheet | estimated
  collision: auto                        # auto：导入器拟合基本几何体或凸包；authored：使用 collision/
implements:                              # 绑定模板，可为空
  - type: centrifuge/v1
    programs: {newton: centrifuge.newton, synthetic: centrifuge.virtual}
    config:                              # 路径相对 /Asset
      lidJoint: LidHinge
      rotorJoint: Spindle
      slotSites: [Rotor/Slot1, Rotor/Slot2, Rotor/Slot3, Rotor/Slot4]
      visualRpmCap: 60
dressing: false                          # true：整个素材只作布景
```

- 绑定模板里的路径都相对素材根 prim。放进场景后，路径前缀换成该实体的 `entityKey`。
- 一个素材可以实现多个类型，但一个场景实体只绑定一个。
- `environment` 类素材通常 `dressing: true`，比如房间外壳、百叶窗。

## 6. 场景包

### 6.1 内容

```text
scenes/fr3-lab/2/
  scene.yaml          元数据与仿真设置
  stage.usda          组合层：引用素材版本并给出位姿
  devices.yaml        设备绑定
  locations.yaml      示教位置
  validation.json     验证报告
  previews/           缩略图与多角度图
```

### 6.2 `scene.yaml`

```yaml
name: fr3-lab
title: 阳光下的细胞培养实验室
description: FR3 机械臂、离心机、生物安全柜与 20 支试管
sources: [newton]                    # USD 场景只能是 newton
stage: stage.usda                    # 内置场景可写 python:<SCENE_CONFIGS id>
simulation: {solver: mujoco, warmupSteps: 120}
derivedFrom: {factory: blender-scene-director, source: laboratory_final.blend, tool: Blender 4.3.2}
```

### 6.3 `stage.usda` 约定

- `metersPerUnit = 1`，`upAxis = "Z"`。
- `/World/Lab/**` 下是**实体**：每个实体 prim 用 USD reference 引用一个素材版本的 `asset.usda`，并带变换。
- `/World/Dressing/**` 下是**布景**：只渲染，不进 Newton 模型，没有设备语义。
- prim 路径即 `entityKey`。命名规则沿用 v1.2：ASCII、以 `/World/` 开头，同一场景内唯一。
- 引用只能指向库里存在的素材版本，写成 `@library:assets/device/labspin-centrifuge/1/asset.usda@`，由加载器解析。

### 6.4 `devices.yaml`

格式沿用信息规格 §4.2，新增 `template` 简写：

```yaml
scene: fr3-lab
entities:
  - key: /World/Lab/Centrifuge
    template: device/labspin-centrifuge@1   # 展开该素材的绑定模板
    interlocks:
      closeLid:
        - {entity: /World/Lab/RobotA, property: zone, notEquals: centrifuge,
           message: 机械臂仍在离心机区域内；先把它移出}
  - key: /World/Lab/RobotA
    template: robot/fr3@1
    config: {speedScale: 0.5, graspMode: weld}  # 覆盖模板中的同名键
  - match: /World/Lab/Tubes/*
    template: labware/conical-tube-15ml@2
```

- 加载器先展开模板，再应用 `config` 覆盖，最后按信息规格 §4.2 的规则校验。
- 场景中有物理但未绑定类型的实体可以不写，Catalog 中显示为"无类型实体"（§7.3）。
- 跨实体互锁只能写在这里。

### 6.5 `locations.yaml`

格式与现有 `robot-and-tubes.locations.yaml` 相同。示教位置依赖机械臂摆在哪里，所以属于场景，不属于素材。场景布局变化时，示教位置要重新示教（`tools/teach.py`），并由物理检查 P6 验证。

### 6.5.1 管上抓取高度（ADR-0002）

Newton / Virtual arm Config 可声明 `graspHeightM`，严格有限 number、非负、默认 0；同一 arm 对全部配置管使用一个共同高度。它是距管底 body 原点沿管局部 +Z 的抓取点，不是新 Site、TCP 或位置文档字段。真实手的 TCP 校准及原管底 Site 不变。实际抓取点随 body 旋转；示教的名义竖直目标是 `bottomSiteWorld + [0,0,h]`，使用世界 +Z，不能把手的旋转或倾斜 Site 的轴当作名义管轴。

非零模式先由 S5 核对每根实际配置管的 body、可检查的碰撞几何局部轴向范围及底部原点约定；未知或超界拒绝，不能猜一个最大高度。同管若被多个 arm 配置为不同高度且至少一个非零，也拒绝。h 本身是标量，不做路径 rebase 或 glob 展开；既有 plain-Config glob 行为不因此扩展。

非零 Newton 模式还必须有精确固定版本/内容哈希的机器人 Binding Template。模板展开后，实际 `tcpSite`、`handBody` 与模板 rebase 结果一致，Site 归真实手 body 所有，组合后的 hand→TCP 校准、指关节轴/锚点及夹爪碰撞几何与固定资产一致。场景只允许已有的整机 base pose、初始关节和驱动意见影响这些运动；不能用强意见挪 TCP、手或指垫来保住零距离。无法核实校准的非模板非零模式为 S5 fail。旧 h=0 的模板/非模板包继续原有检查，不追写旧资产。

第一次采用 h 或更改 h 都要导入显式新 Scene Version，重新示教并运行新的 S/P 报告；旧版本、旧向量、原素材和旧报告不覆盖。工具从所选版本/机器人读取有效 h、真实模型和 base，输出 bottom/height/nominal target/physical FK TCP/误差及输入版本；不能用硬编码旧 base 的教点工具声称支持新布局。新版本能力依赖运行时支持该字段；旧运行时应拒绝未知 Config，不能静默按 0 执行。

### 6.5.2 离线接近位置

`tools/teach.py` 的离线生成保留默认 150 mm 接近高度。其它场景可显式指定
`--approach-height-m`，单位为米，必须有限且大于 0；它表示名义抓取点上方的世界 Z
距离，独立于 `graspHeightM`。例如：

```powershell
uv run --no-sync python tools/teach.py --scene-version fr3-lab@1 --approach-height-m 0.10 --output .scratch/locations.yaml
```

输出的每位置证据包含接近高度、目标与离线 FK 误差，并明确
`validationScope: offline-urdf-fk`、`newtonP6Verified: false`。该选项不改变真实 TCP、
管底 Site、原位置名或 grasp 姿态方向；不能用于 `--capture`。新示教仍须按 §6.5.1
导入新 Scene Version，不能覆盖已发布包。

求解继续使用原严格数值门槛：位置误差（米）与旋转向量误差乘以 0.2 后的联合范数
≤ 0.0001。失败报告位置/角度残差、求解状态及各关节距限位的余量；一次有界局部求解
失败不等于证明全局不可达。全部位置生成成功后才写输出文件，失败不发布部分示教。
接近高度必须结合管件抽出、周围物体和真实手的净空选定；数值通过不证明实际关节空间
轨迹、碰撞或负载安全，物理 P6 与 A3 完整流程验收仍单独执行。

### 6.6 内置场景迁移

| 场景版本 | `stage` | `sources` |
|---|---|---|
| `lab-demo@1` | `python:lab-demo` | synthetic |
| `falling-boxes@1` | `python:falling-boxes` | newton |
| `robot-and-tubes@1` | `python:robot-and-tubes` | newton |
| `usd-lab@1` | `python:usd-lab` | newton |

Python builder 场景跳过 USD 相关的结构检查（S2、S3、S7），其余检查照常执行。

## 7. 验证报告

### 7.1 何时运行

- 导入时自动运行。`lw asset validate` / `lw scene validate` 可手动重跑，结果写入新的报告文件，版本本身不变。
- 网关启动时不重跑，只读已有报告。报告的 `toolVersions` 与当前环境不一致时，标记为"过期"，仍按原结论判定可否启动。
- 物理检查在独立子进程中用 Newton CPU 设备运行，网关不 import Newton。

### 7.2 检查项

**结构检查（S，不跑仿真，CI 可测）**

| ID | 对象 | 内容 |
|---|---|---|
| S1 | 素材、场景 | `asset.yaml` / `scene.yaml` / `devices.yaml` / `locations.yaml` 符合 schema |
| S2 | 素材、场景 | `metersPerUnit = 1`，`upAxis = Z` |
| S3 | 场景 | 实体 prim 路径符合 `entityKey` 规则；实体与布景不交叉 |
| S4 | 素材 | `provenance.json` 与许可文件存在；provenance 中列出的文件哈希一致 |
| S5 | 素材、场景 | 绑定模板引用的类型存在，模板中的关节、站点在 stage 中存在 |
| S6 | 素材 | 视觉预算：单个素材 ≤ 500k 三角形、贴图 ≤ 4096²；布景合并后 ≤ 64 个 GLB |
| S7 | 场景 | 所有 USD 引用都能解析到库中的素材版本 |
| S8 | 场景 | 示教位置的关节数与机械臂一致，且都在关节限位内 |

**物理检查（P，无头 Newton，CPU）**

| ID | 对象 | 内容 |
|---|---|---|
| P1 | 素材、场景 | 能加载为 Newton 模型（素材放在地面上加载），无 NaN |
| P2 | 素材、场景 | 仿真 2 s 沉降：每个自由刚体位移 ≤ 5 mm |
| P3 | 素材、场景 | 静止时穿插深度 ≤ 2 mm |
| P4 | 素材 | 质量 > 0；由碰撞体积估算的密度在 50–20,000 kg/m³ 之间 |
| P5 | 场景 | 初始状态下所有关节都在限位内 |
| P6 | 场景 | 每个 `grasp` 构型的真实物理 TCP FK 与对应管底 Site 上方 `graspHeightM` 名义点的距离 ≤ 10 mm；旧零高度等于原站点规则，见 §7.2.1 |

每项结论为 `pass / warn / fail`。`massSource: estimated` 之类的情况给 `warn`。

### 7.2.1 P6 的真实物理端点与名义抓取点

程序声明中以恰好一个 Site 字段的 `postureEndpoint` 元数据选择物理端点；arm 的 `tcpSite` 声明：

```yaml
spatialReference: site
postureEndpoint:
  offsetField: graspHeightM
  offsetAxis: worldZ
```

这是程序注册的字段元数据，不是用户可随意填入 Config 的新字段。其它普通 Site 不会争抢显式端点；重复端点、未知 offsetField、非标量/非有限/负的偏移或非 `worldZ` 轴为 S5 fail，直接调用 P6 时也必须 fail，不能记为不适用。offsetField 要指向真实已定义的 Config 标量，消费者必须转发并校验同一声明。

旧程序/SDK fixture 没有该角色时，只允许缺省或零高度且恰好一个 Site 的原回退规则；多个未标记 Site 仍有歧义，非零高度没有显式角色必须失败。不能悄悄忽略 h。

P6 不仅相信元数据名字。非零模式必须先通过 §6.5.1 的固定资产校准；测量时还核实模型中实际端点的手 body owner 和 hand→TCP 局部 pose。校准未知或不符时，即便某虚拟点到目标的距离为 0，也报告失败，不能伪造测量值。

令 `target = bottomSiteWorld + [0,0,h]`，测量真实模型 FK 的 `physicalTcpWorld`，误差仍为两点欧氏距离，门槛仍 10 mm。每条报告保留原 `bottomSiteWorld`、`graspHeightM`、`graspTargetWorld`、`physicalTcpWorld`、误差和 `targetDefinition: tube-grasp-height/1`；旧零高度的数值和含义不变，扩展放在已有 check measurements 中。所有原示教位置逐一测量，不删失败位置。沿用旧底部向量却声明 h=80 mm，应得到约 80 mm 误差。

该 P6 检查假定管名义竖直落座，只检验点的位置可达，不增加方向通过条件，不代表实际管姿态、接触、整段轨迹或完整负载通过。动作预览同样必须保持真实 physical TCP 与实际目标 body pose：非零 h 时，真实 TCP/名义抓取点可高于原底部目标；下游不能沿用二者应重合的零高度假设，更不能把预览点改成虚拟 TCP 来显示绿灯。

此新增语义仍使用已有 Config 与测量扩展容器，不改空间协议或录制段布局。v1/v2 录制按原 Catalog/pose/属性回放，不通过新程序重新模拟、补写 h 或重新解释旧 `tcpPose`。采用非零模式的新录制照常固定新场景版本/内容哈希；旧录制必须继续可读。

### 7.3 报告格式与判定

```json
{
  "schema": "lw.validation/1",
  "subject": "scene:fr3-lab@1",
  "ranAt": "2026-10-18T09:12:00Z",
  "toolVersions": {"newton": "1.7.0", "warp": "1.18.0", "usd-core": "26.8"},
  "checks": [
    {"id": "P6", "tier": "physics", "status": "fail",
     "entityKey": "/World/Lab/RobotA", "location": "centrifuge.slot3",
     "message": "示教位置 centrifuge.slot3 不可达：TCP 偏差 38 mm"}
  ],
  "launchable": false
}
```

- **可启动**：所有 S 与 P 检查都没有 `fail`。不可启动的场景版本在场景库中照常列出，标红并列出原因。
- **素材完整度**（素材库徽章）由报告和 `asset.yaml` 推导：碰撞体、质量与惯量、PBR 视觉、许可证、关节与站点命名、可实现类型，各为 有 / 警告 / 缺。
- **场景缺口**：失败与警告的检查，加上"无类型实体"列表（例如生物安全柜只是障碍物）。

## 8. 运行时改动

### 8.1 场景发现与会话

- 网关启动时扫描两个库根，建立场景版本和素材版本的只读索引；`POST /library/imports` 成功后增量更新索引。
- `POST /sessions` 接受 `{sceneVersion: "fr3-lab@2", source: "newton"}`。旧参数 `sceneId` 继续接受，解析为该名称的最新可启动版本。
- 不可启动的版本返回 `409 scene_not_launchable`，并附报告中的失败项。
- 单活跃会话规则（D-12）不变。

### 8.2 Runner 加载 USD 场景

- Runner 解析 `@library:` 引用后，用现有 `loaders.import_usd` 导入 `/World/Lab/**`；`/World/Dressing/**` 不进入 Newton 模型。
- 布景以 `StaticNode` + `Glb` 视觉写入 Manifest，实体的 PBR 视觉以 `BodyNode.visuals` 中的 `Glb` 写入，碰撞体照旧写进 `colliders`。**协议不变**，无需升版本。
- `visual.glb` 由导入器从 USD 派生，或随包提供；经新路由提供给浏览器（§9）。

### 8.3 录制格式 v2

- `recording.json` 新增 `sceneVersion`（如 `"fr3-lab@2"`）和 `sceneContentHash`，`formatVersion` 升为 2。
- 读取器继续支持 v1：v1 录制的 `sceneVersion` 视为 `<sceneId>@1`。
- `tests/fixtures/recordings/v2/` 新增一份夹具，v1 夹具保留，E2E 两份都要能播放。
- 段文件格式不变。

## 9. HTTP API

路径前缀 `/api/v1`。

| Method | Path | 结果 |
|---|---|---|
| GET | `/library/assets?kind=&q=&gaps=` | 素材版本列表：ID、标题、kind、缩略图 URL、完整度、来源根 |
| GET | `/library/assets/{kind}/{name}/{rev}` | `asset.yaml`、可实现类型的完整定义、验证报告、provenance、引用它的场景 |
| GET | `/library/scenes?q=&launchable=` | 场景版本列表：ID、标题、可用源、实体与设备数、可启动、缺口数 |
| GET | `/library/scenes/{name}/{rev}` | 元数据、实体树（实体与布景分开）、设备与动作、示教位置、引用的素材、验证报告、相关录制 |
| GET | `/library/files/{root}/{path}` | 库中的 GLB、预览图等静态文件；只允许索引中登记过的文件 |
| POST | `/library/imports` | 上传 tar 包（素材包或场景包），返回导入作业；完成后返回新版本 ID 与验证结论 |
| POST | `/library/{assets|scenes}/.../validate` | 重跑验证 |
| DELETE | `/library/{assets|scenes}/...` | 删除版本；产生审批请求（§10.4） |

- 导入接收上传的包，不接收网关主机上的路径。这延续了"API 不接受任意路径"的规则，也为以后的远程库留出余地。
- 现有 `/scenes` 保留，返回场景版本列表的精简形式。

## 10. Agent 接口（与训练规格共用）

### 10.1 CLI 优先

- 平台的每项能力都先是一条 `lw` 命令：支持 `--json`，`--help` 写明参数与用途，退出码沿用信息规格 §9.1。
- 本规格新增：

```text
lw asset ls [--kind K] [--gaps] [-q TEXT]
lw asset show <id>
lw asset import <dir>                 打包目录并上传
lw asset validate <id>
lw scene ls [--launchable]
lw scene show <name[@rev]>
lw scene import <dir>
lw scene validate <name@rev>
lw scene launch <name[@rev]> [--source newton]
lw skills install [--target DIR]      安装平台 skill
lw skills ls
```

### 10.2 MCP 镜像 CLI

- MCP 工具由与 CLI 相同的命令表生成；不存在没有 CLI 对应的 MCP 工具。
- 工具保持通用，不按设备或素材生成专用工具。本规格新增 `library_search`（素材和场景）、`library_describe`、`library_import`、`library_validate`、`scene_launch`。
- 不提供审批工具。

#### 10.2.1 有界库详情（`lw.library-description/1`）

`lw library describe <id>` 与 `library_describe` 共享参数 `id`、`view`
（`summary | page | full`，默认 `summary`）、`section`（默认 null）、
`offset`（默认 0）和 `documentDigest`（默认 null）。CLI 对应
`--view`、`--section`、`--offset`、`--document-digest`。这会改变原先
CLI-library/MCP 默认返回完整详情的语义；需要旧结果的调用者显式使用
`view=full`。`lw scene show`、`lw asset show` 保持默认 `full`。
两个 HTTP 详情路由共享相同查询参数，省略 `view` 时仍返回原来的完整对象，
Web 与旧 HTTP 调用不需要迁移。`full` 不加 envelope，也不删减字段。

`summary` 与 `page` 的完整 JSON 各不超过 4096 个 UTF-8 字节；经官方
MCP SDK 返回的整个 CallToolResult（包括 text 和 structuredContent）不超过
16384 个 UTF-8 字节。这是项目传输预算，不是外部模型的 token 上限。
详情读取不启动会话或物理源，不增加 MCP 专用适配器或工具。

`summary` 返回 `schema`、`view`、`resource`（`scene | asset`）、解析后的精确
`id`、`title`、`description`、`contentHash`、`documentDigest`、`launchable`、
`sources`、`launchReason`、`countsReason`、`validation`（status/reportId/gapCount）、
`counts`（entities/devices/types/locations/assets/files）、`typesPreview`
（type/actions 名称）、`devicesPreview`（entityKey/type）、`locationsPreview`、
`gapsPreview`、`sections`、`previewTruncated` 和 `details`。
`details` 给出 `{view: page, id, documentDigest, section: null, offset: 0}`。
不适用或未知的物理实体/设备数保持 null 和原因，不能用 authored bindings
数量冒充观测数量。预览和说明可确定性缩减以满足预算，并设置
`previewTruncated=true`；完整标识符和动作名称只能保留或省略，不能截成另一个
可操作标识符。启动失败、计数未知和验证缺口的说明必须可发现，完整内容经分页访问。

`page` 要求调用者使用精确 `@rev` 及 summary 提供的 `documentDigest`。
digest 是整个完整详情的规范 JSON 的 SHA-256，包括 preview、验证报告、引用检查
和相关录制；它不是不可变包的 `contentHash`。规范 JSON 使用排序键、紧凑分隔符、
原样 Unicode 和拒绝非有限数值（Python `sort_keys=True, separators=(',', ':'),
ensure_ascii=False, allow_nan=False`）。同一版本的报告、录制或引用状态变化也会改变
digest；服务端不缓存快照。过期 digest 返回 HTTP 409 `description_changed`，
调用者在元数据稳定后重新获取 summary 并从第一页开始。

`section` 仅可选完整对象的一个顶层字段，null 选择整个完整对象。
`page` 返回 `{schema, view, resource, id, documentDigest, section, offset,
nextOffset, totalChars, content}`。`content` 是所选规范 JSON 的连续子串；
offset/nextOffset 按 Unicode 码点计数，服务端选满足完整 envelope 字节预算的
最长前缀。顺序拼接所有 content 必须逐字重建规范 JSON，包含大字符串和空容器。
末页 `nextOffset=null`；offset 等于 totalChars 时返回空 content 的末页。
每页 content 是文本片段，可跨规范 JSON 的转义序列；顺序拼接全部片段后再解析。
每个响应的 content 自身是合法 JSON 字符串，切分保留完整 Unicode 码点。

无精确版本、缺 digest、未知 section、负数/超出末尾的 offset、错误 view，
以及把 section/offset/digest 用于非 page，均返回有界 HTTP 400
`invalid_description_view`。错误不回显无界用户输入。现有资源不存在错误不变。
这些发现接口不改变空间协议、entityKey、设备动作管线或录制格式。

### 10.3 平台 skill

平台 skill 教 agent 如何通过 `lw` 操作实验室。它们放在仓库 `skills/lab-*/SKILL.md`，与 `.claude/skills/` 下的开发 skill 分开，随平台版本发布，用 `lw skills install` 复制到使用者的 agent 目录。

| Skill | 所属规格 | 作用 |
|---|---|---|
| `lab` | 本规格 | 入口：平台是什么、RSI 闭环、何时用哪个 skill、人工关口 |
| `lab-operate` | 本规格 | 查看实验室、调用动作、读懂拒绝原因并自行修正 |
| `lab-library` | 本规格 | 找素材和场景、读验证报告和缺口、导入、校验、启动场景 |
| `lab-training-plan` | 训练规格 | grilling 式访谈后起草并提交训练计划 |
| `lab-train-evaluate` | 训练规格 | 在批准的计划内运行作业、评估、申请提升策略 |
| `lab-eval-build` | RSI 规格 | Builder：设计、校准评估集，记录正式基线 |
| `lab-harness-optimize` | RSI 规格 | Agent 层 Optimizer：改 Harness 并按接受规则保留或回滚 |

`lab-operate` 同时承担实时操作规格的内容：每轮先查 `lw inbox`、用 `lw history` 诊断、先提交操作计划再按步骤执行（实时操作规格 §7、§8）。

- skill 按 `writing-for-agents` 的规范撰写。
- CI 检查：解析平台 skill 中每条 `lw ...` 命令，确认它存在于 `lw --help` 的命令树中。

### 10.4 人工关口

| 关口 | 触发 |
|---|---|
| 训练计划审批 | `lw plan submit`（训练规格） |
| 操作计划审批 | 含 `/Real/...` 实体或 `risk: high` 动作的 `lw plan create`（实时操作规格 §8.3） |
| 真实设备高风险动作 | 计划外对 `/Real/...` 的 `risk: high` 调用（实时操作规格 §4.3） |
| 策略提升为默认程序 | `lw policy promote`（训练规格） |
| 超预算作业 | 作业预计 GPU 小时超出所属计划的剩余预算（训练规格） |
| 删除库版本 | `lw asset rm` / `lw scene rm` 等 |

- 审批只在 Web 的"待审批"页或交互式 `lw approve` 中进行。`lw approve` 要求终端中的人工确认；带 `--json`、非 TTY 或经 MCP 调用时拒绝。
- 平台 skill 明确禁止 agent 执行审批。
- 每次审批记录操作者（`user / cli / agent`）与时间。本地单用户模式下这是约定加审计，不是安全保证；有了账户后再换成权限控制。
- 审批请求复用信息规格 §13 的审批机制与 `lw approve` 命令，增加 `subject` 类型：`invocation / plan / policy / job / deletion`。

## 11. 前端

### 11.1 导航

顶层导航（可折叠侧栏）：**概览 | 实况 | 素材库 | 场景库 | 计划 | 训练 | 评估 | 成本 | 资源 | 待审批 | 会话 | 录制**。会话从场景详情页的"启动会话"按钮创建，首页下拉框删除。"实况""计划"由实时操作规格定义，"训练"由训练规格定义，"评估""成本""资源"由 RSI 规格定义；各自落地前隐藏。

### 11.2 页面

- **素材库**：按 kind 筛选、搜索、"只看有缺口"；每项显示缩略图、`id@rev`、尺寸、完整度徽章。详情页：3D 预览、完整度清单、可实现类型及其属性和动作说明、来源与许可、引用它的场景。
- **场景库**：每项显示缩略图、`name@rev`、可用源、实体与设备数、可启动状态、缺口数。详情页：3D 预览、实体树（实体与布景分开）、设备与动作、示教位置、引用的素材、验证报告、相关录制、"启动会话"。
- **待审批**：本规格只产生删除请求；页面结构由训练规格补全。

### 11.3 视觉方向

依据 UI 原型（`apps/web/prototypes/platform-redesign.prototype.html` v3）选定，2026-10-18：

- **视觉**：参照 deeprsi.com 的克制质感。深色石墨为主题，浅色为暖调纸面；单一靛蓝强调色只标"运行中、已选中、agent 提出"；语义色平时只是小圆点。标题与大数字用 Noto Serif SC，界面用 IBM Plex Sans，ID 与数值用 IBM Plex Mono。实验室视口用 Three.js 正交等距渲染（柔光、接触阴影、细描边、剖切墙），读数、告警点、TCP 路径以 HTML/SVG 叠加。
- **框架以原型 A（仪器控制台）为主**：左侧可折叠的图标侧栏，模块化面板，紧凑。
- **按页面取用**：

| 页面 | 采用 |
|---|---|
| 概览（视口、近期活动） | B：图优先的视口、读数浮标、点选设备打开抽屉 |
| 实况 | B |
| 素材库、场景库 | B 的卡片网格，可切换为带小预览图的列表视图 |
| 计划、训练、待审批 | C：阶段条、收件箱式审批卡、RSI 看板 |
| 其余（会话、录制、详情页等） | A |

### 11.4 FR3 实验室的导入（A3 前置，平台外完成）

把 `laboratory_final.blend` 转换为场景包是一次性的素材工厂工作，由 agent 在平台外用 bpy 完成，不进平台代码：

- 按顶层 Collection 拆出素材：实验台 ×2、生物安全柜、离心机、试管架、试管、培养瓶；其余合并为 `environment/lab-room-dressing@1`。
- FR3 不从 Blender 导出，直接引用内置素材 `robot/fr3@1`。
- 碰撞体先用 `collision: auto`；离心机盖与转子需要人工碰撞体时放进 `collision/`。
- 产物通过 `lw scene import` 进入用户库，原始 `.blend` 留在素材工厂一侧，不进库。

## 12. 里程碑

```text
A1 库 + 场景包 + 场景库页 ──► A2 素材包 + 验证 + 素材库页 ──► A3 fr3-lab 导入演示
```

**A1 库与场景包**

内容：库根与索引、场景包格式、内置 4 个场景迁移、按版本启动会话、录制格式 v2、`lw scene ls/show/launch`、`lab` 与 `lab-operate` skill、`lw skills install`、新导航与场景库页（布局按原型结论）。

验收：

1. 删除硬编码 `SCENES` 后，场景库列出 4 个内置场景版本，每个都能从详情页启动。
2. `lw scene show robot-and-tubes --json` 返回实体、设备、动作与示教位置。
3. 新录制带 `sceneVersion`；v1 与 v2 夹具都能回放。
4. skill 命令检查在 CI 中通过，并能发现一条故意写错的命令。

**A2 素材包与验证**

内容：素材包格式、绑定模板展开、导入（tar 上传）、结构检查与物理检查、验证报告、完整度与缺口、`lw asset *`、`lw scene import/validate`、`lab-library` skill、素材库页、删除审批。

验收：

1. 内置 `robot/fr3@1` 的完整度全部为"有"。
2. 一个故意缺许可证、缺碰撞体的夹具素材，导入后完整度显示对应缺口。
3. 一个示教位置被故意改坏的夹具场景，导入后不可启动，P6 给出具体偏差；`lw scene launch` 返回 `scene_not_launchable`。
4. 重复导入同一个包，返回同一版本。

**A3 `fr3-lab` 演示**

验收：

1. `fr3-lab` 以场景包导入后可启动；浏览器中显示 PBR 布景与实体。
2. 素材库中能看到拆出的素材及其缺口（例如生物安全柜无类型）。
3. 通过 CLI 在 `fr3-lab` 中完成信息规格 I3 的抓放与离心流程。
4. agent 只依靠 `lab` 与 `lab-library` skill，从 `lw scene ls` 开始找到并启动 `fr3-lab`，并说出它有哪些设备和缺口。

## 13. 测试

| 类别 | 内容 |
|---|---|
| 单元 | 库索引与 ID 解析；内容哈希；模板展开与覆盖；S1–S8 结构检查（合法与非法夹具） |
| 契约 | 验证报告 schema；录制 v1 / v2 读取 |
| 网关 | 导入、索引增量更新、按版本启动、`scene_not_launchable`、删除审批 |
| CLI / MCP | 新命令的输出与退出码；MCP 镜像工具逐个调用；`lw approve` 在非 TTY 时拒绝 |
| Skill | 平台 skill 命令存在性检查 |
| E2E | 场景库 → 详情 → 启动会话；素材详情显示完整度 |

物理检查 P1–P6 用小型夹具素材在 CI 中以 CPU 运行；若总时长威胁 5 分钟门禁，则把 P 检查移到手动 smoke，CI 只测判定函数。

## 14. 目录结构

```text
library/                         内置库（只读）
  assets/robot/fr3/1/
  scenes/{lab-demo,falling-boxes,robot-and-tubes,usd-lab}/1/
skills/                          平台 skill
  lab/  lab-operate/  lab-library/
packages/
  library/                       与 Newton 无关
    index.py                     库根扫描、ID 解析、内容哈希
    packages.py                  素材包与场景包 schema
    templates.py                 绑定模板展开
    structural.py                S1–S8
  sim_runtime/
    validation.py                P1–P6（子进程入口）
    stage_loader.py              @library: 解析、实体与布景拆分
apps/
  api/app/library_routes.py
  cli/lw/{asset.py, scene.py, skills.py}
  web/src/routes/{assets.*, scenes.*}
```

## 15. 待决策清单

| ID | 决策 | 默认值 | 状态 |
|---|---|---|---|
| A-01 | 规范格式 | USD（ADR 0001） | 已定 |
| A-02 | 用户库位置 | `LW_LIBRARY`，默认 `~/.spatial-sync/library` | 已定 |
| A-03 | 版本号 | 整数 rev + 内容哈希 | 已定 |
| A-04 | 沉降与穿插阈值 | 5 mm / 2 mm | 默认生效 |
| A-05 | 示教位置容差 | 10 mm | 默认生效 |
| A-06 | 视觉预算 | 500k 三角形、4096² 贴图、布景 ≤ 64 GLB | 默认生效 |
| A-07 | Web 布局 | A 为框架；概览、实况、素材库、场景库取 B；计划、训练、待审批取 C（§11.3） | 已定 |
| A-08 | 视觉检查与 Isaac Sim 校验器 | 不纳入 v1 | 已定 |
| A-09 | 平台内场景搭建 | 不纳入 v1；外部工厂 + 导入 | 已定 |
