---
title: 从搭场景到 Agent 实操：一座细胞传代实验室的构建与轨迹预演
description: 从一句自然语言搭起含 38 件视觉素材与 2993 个功能面碰撞体的细胞传代实验室，到导入 Lab World 完成多设备协同，再到 14 步完整传代轨迹的仿真与渲染。
date: 2026-11-08
author: Lab World 团队
cover: /images/blog/world-building-experiment/cover.webp
---

<script setup lang="ts">
import { withBase } from 'vitepress'
import { EXPERIMENT_FILM, PASSAGE_FILM } from '../.vitepress/theme/films'
import './physical-ai-diagrams.css'
</script>

# 从搭场景到 Agent 实操：一座细胞传代实验室的构建与轨迹预演

要让智能体在实验室里真正动手，先要跨过两道门槛：**世界从哪里来**，以及**动作靠什么检验**。

过去几周，我们围绕同一座全自动细胞传代黑灯实验室（`panda-cell-passaging-lab`），把这条链路完整走通了一遍：先用 **World Building** 从一句自然语言搭起同时具备高清视觉网格与功能面碰撞体的实验室场景；再把场景导入 **Lab World**，让 Agent 通过设备目录（Catalog）、安全互锁（Interlocks）与操作计划（Operation Plan）完成第 1 步的 23 个多设备协同动作；同时，在 MuJoCo 与 Blender 管线中完成了覆盖完整 14 步细胞传代规程的 `911.9 s` 连续轨迹预演与逐帧渲染。

这篇文章把两支最新实录与仿真短片、可复现的命令与计划摘录，以及当前的真实能力边界放在一起做一次完整拆解。

## 第一支短片：从搭场景到 Agent 实操

<SiteVideo
  :film="EXPERIMENT_FILM"
  caption="《Lab World · 让 Agent 动手做实验》（02:26 / 中文烧录字幕）—— 从 World Building 搭建细胞传代实验室，到导入 Lab World 由 Agent 完成计划核对修订与多设备协同。"
/>

这支短片记录了一条从零搭建到实际运行的四阶段闭环：

<figure class="pai-diagram" aria-label="从自然语言需求到 Agent 实操的四阶段链路">
  <div class="pai-diagram-body">
    <p class="pai-eyebrow">一源双模与实操闭环 · 4 个阶段</p>
    <ol class="pai-steps">
      <li>
        <span class="pai-step-number">1</span>
        <strong>World Building<br>搭场景</strong>
        <p>自然语言生成 <code>design.json</code>，同步构建视觉素材与功能面碰撞体。</p>
      </li>
      <li>
        <span class="pai-step-number">2</span>
        <strong>一源双模<br>导出对齐</strong>
        <p>导出 38 件视觉网格与 59 刚体 / 2993 碰撞体，由 <code>mapping.json</code> 绑定。</p>
      </li>
      <li>
        <span class="pai-step-number">3</span>
        <strong>Lab World<br>导入注册</strong>
        <p>生成 <code>panda-cell-passaging-lab@3</code>，注册 4 台可驱动设备与安全互锁。</p>
      </li>
      <li>
        <span class="pai-step-number">4</span>
        <strong>Agent 规划<br>核对执行</strong>
        <p>查询 Catalog、经历两轮计划核对修订后，23 步多设备协同一次跑通。</p>
      </li>
    </ol>
    <p class="pai-feedback"><span aria-hidden="true">↺</span>每一次后置核对（<code>expect</code>）未通过的实测读数，都会直接反馈给 Agent 修订计划，直到全部步骤通过。</p>
  </div>
  <figcaption>同一份场景真源既服务于空间可视与逐帧渲染，又服务于物理碰撞校验与设备动作管线。</figcaption>
</figure>

## 一源双模：为什么不能只靠“好看的 3D 模型”

如果只把视觉网格原封不动塞进物理引擎，凸包近似会把培养箱腔体、离心机转子槽位、移液架孔位以及 6 孔板井口全部“封死”——机械臂连门都伸不进去，更谈不上放板。

在 `world-building` 的七步管线（Research → Design → Source/Build → Assemble → Export MJCF → Simulate → Render）中，所有设备与耗材都由同一份 `design.json` 派生出两套几何表达：

- **视觉层（`VIS_`）**：服务于 Blender 渲染与 Lab World Web 工作台的 38 件高精度视觉素材，保留玻璃门折射、金属导轨材质与实验台细节。
- **物理碰撞层（`COL_`）**：服务于物理引擎的 59 个刚体与 2,993 个功能面原语（box / cylinder），确保培养箱腔体中空、门扇可旋转、6 孔板可落座、试管与吸头可插拔。
- **位姿桥接（`mapping.json`）**：将物理刚体、关节与视觉节点一一绑定，使任何一方的运动都能精确映射到另一方。

<figure class="pai-diagram" aria-label="同机位下的 Blender 视觉渲染与 MuJoCo 功能面碰撞模型对比">
  <div class="pai-diagram-body">
    <p class="pai-eyebrow">同机位对比 · 视觉网格（VIS_）与功能面碰撞体（COL_）</p>
    <div class="pai-perspectives">
      <div>
        <p class="pai-term">Blender 视觉场景</p>
        <p><img :src="withBase('/images/blog/world-building-experiment/render-wide.webp')" alt="Blender 渲染的全自动细胞传代实验室全景：线性导轨上的 Panda 机械臂、双层门培养箱、生物安全柜、离心机与显微观察站" width="1280" height="720" loading="lazy" decoding="async" /></p>
        <p class="pai-detail">38 件视觉素材按 4.2 m × 2.9 m 实验室空间装配，保留真实外观与光学材质。</p>
      </div>
      <div>
        <p class="pai-term">MuJoCo 功能面碰撞模型</p>
        <p><img :src="withBase('/images/blog/world-building-experiment/collision-wide.webp')" alt="同一机位下的 MuJoCo 功能面碰撞几何视图：展示培养箱门腔、导轨、机械臂与桌面仪器的精确碰撞原语" width="1280" height="720" loading="lazy" decoding="async" /></p>
        <p class="pai-detail">59 个刚体、2,993 个碰撞原语：腔体留空、抽屉可滑、孔位可插，专供物理求解与互锁检验。</p>
      </div>
    </div>
  </div>
  <figcaption>左：Blender 视觉渲染；右：同一机位下编译进物理引擎的 2,993 个功能面碰撞体。</figcaption>
</figure>

## 导入 Lab World：从静态素材到可操作的实验室

当这座实验室导入 Lab World 并发布为 `panda-cell-passaging-lab@3` 时，平台不仅加载了 38 件视觉素材、17 个动态刚体和完整保留的 76 条上游素材来源声明，还把实验室第 1 步涉及的运动机构映射为信息平面上的 **4 台可驱动设备**（共享统一的 `entityKey`）：

1. **双层门 CO2 培养箱**（`/World/Lab/incubator/incubator__cabinet`）：支持 `openOuter`、`openInner`、`closeInner`、`closeOuter`，带门扇顺序互锁（先开外门再开内门，先关内门再关外门）。
2. **2.95 m 线性导轨**（`/World/Lab/panda_rail/rail__rail_carriage`）：支持 `moveTo(positionM, durationSeconds)`，承载机械臂在培养箱、观察站与工作站之间横移。
3. **7 自由度 Panda 机械臂与宽行程夹爪**（`/World/Lab/panda_rail/panda__hand`）：支持 `moveJoints`、`setGripper(openingM, durationSeconds)` 与 `home`。
4. **显微观察站抽屉**（`/World/Lab/imager/imager__housing`）：支持 `extend` 与 `retract`，用于滑出托盘承接培养板并收回观察。

![Lab World 工作台中的细胞传代实验室场景：左侧为场景树与设备面板，中央为三维空间视图，右侧为计划与状态栏](/images/blog/world-building-experiment/workbench.webp)

在 Lab World 工作台里，人类操作员和 Agent 面对的是同一套 HTTP 动作管线与实时空间流。Agent 不需要猜测场景里有什么，而是直接读取场景的设备目录（Catalog）与实时状态（State）。

## 两轮核对修订：Agent 如何把 23 步计划跑通

在实操运行（`agent-run-001`）中，Agent 在隔离工作区中通过 `lw` CLI 连接会话，执行细胞传代第 1 步的设备联动任务：**打开培养箱外门与内门 → 导轨移至培养箱前（`2.6 m`）→ 机械臂经示教位探入并合拢夹爪（`0.19 m → 0.13 m`）→ 抬起并退出培养箱 → 导轨横移至观察站（`1.5 m`，确保机械臂离开门前）→ 按互锁顺序关闭培养箱内门与外门 → 推出观察站抽屉、完成放板动作、收回抽屉 → 机械臂回 `home`、导轨回 `1.2 m`**。

在实际执行过程中，计划 `plan-32c7a0ea85744180` 经历了两次后置核对（`expect`）中断与在线修订：

<figure class="pai-diagram" aria-label="计划 plan-32c7a0ea85744180 的三轮执行与修订过程">
  <div class="pai-diagram-body">
    <p class="pai-eyebrow">计划实操与修订记录 · plan-32c7a0ea85744180（rev 1 → rev 3）</p>
    <dl class="pai-roles">
      <div>
        <dt>修订 1（rev 1）</dt>
        <dd>
          <strong>第 3 步导轨后置核对 <code>positionM equals 2.6</code> 未通过</strong>
          <span>导轨动作本身成功到位（定位容差 <code>0.001 m</code>），但物理实测值停在 <code>2.5998795 m</code>，超出属性精确比较死区 <code>1e-4</code>。Agent 读取失败原因与设备状态后，将导轨三步的后置核对修订为 <code>phase equals idle</code>（提交 <code>rev 2</code>）。</span>
        </dd>
      </div>
      <div>
        <dt>修订 2（rev 2）</dt>
        <dd>
          <strong>第 5 步夹爪后置核对 <code>openingM equals 0.19</code> 未通过</strong>
          <span>夹爪张开动作完成，但实测 float32 读数为 <code>0.18999999821 m</code>，字面量精确相等比较不匹配。Agent 将夹爪三步的后置核对同样修订为 <code>phase equals idle</code>（提交 <code>rev 3</code>）。</span>
        </dd>
      </div>
      <div class="pai-focus">
        <dt>修订 3（rev 3）</dt>
        <dd>
          <strong>23 / 23 步全部完成（completed）</strong>
          <span>全程无超时或故障：培养箱双门闭合（<code>outerClosed=true, innerClosed=true</code>），导轨归位（<code>positionM=1.20002 m, phase=idle</code>），机械臂回到 <code>home</code>（<code>openingM≈0.19 m</code>），观察站抽屉收回（<code>retracted=true, extensionM≈0.0005 m</code>）。</span>
        </dd>
      </div>
    </dl>
    <p class="pai-note">当前边界说明：本次 Lab World 实操运行覆盖第 1 步多设备协同；当前基础驱动仅执行关节定位，尚不支持夹紧保持（母板不会随夹爪移动，<code>setGripper</code> 取 <code>0.13 m</code> 留出 2 mm 间隙避免碰撞超时）。真实接触支撑、夹持随动与完整 14 步设备扩展正在议题 <code>#252</code>、<code>#254</code>、<code>#255</code> 中推进。</p>
  </div>
  <figcaption>两次中断均来自连续物理量与字面量精确比较的差异：Agent 根据实时状态反馈修订计划，而不是绕过计划直接发裸命令。</figcaption>
</figure>

### 可复现命令与脱敏计划摘录

在启动本地服务并打开 `panda-cell-passaging-lab@3` 会话后，可通过以下 `lw` CLI 命令复现 Agent 的查询、计划创建、修订与状态核验流程：

```bash
# 1. 查询场景设备目录（动作、参数、前置互锁）与实时状态
lw catalog --json
lw state --json

# 2. 创建操作计划并运行（遇到后置核对中断时，读取原因并调用 lw plan revise 修订）
lw plan create plan.yaml --json
lw plan run plan-32c7a0ea85744180 --json
lw plan revise plan-32c7a0ea85744180 plan-rev2.yaml --json
lw plan revise plan-32c7a0ea85744180 plan-rev3.yaml --json
lw plan run plan-32c7a0ea85744180 --json

# 3. 核对最终设备状态
lw get /World/Lab/incubator/incubator__cabinet --json
lw get /World/Lab/panda_rail/rail__rail_carriage --json
lw get /World/Lab/panda_rail/panda__hand --json
lw get /World/Lab/imager/imager__housing --json
```

最终一次跑通的 `plan-rev3.yaml` 关键步骤摘录如下（完整 23 步包含示教位逼近、探入、抬起、退出与放板）：

```yaml
title: 取母板从培养箱送到观察站（设备联动预演）
target: session
steps:
  - id: 1
    do: {entity: /World/Lab/incubator/incubator__cabinet, action: openOuter, params: {durationSeconds: 3}}
    expect: {entity: /World/Lab/incubator/incubator__cabinet, property: outerOpen, equals: true}
  - id: 2
    do: {entity: /World/Lab/incubator/incubator__cabinet, action: openInner, params: {durationSeconds: 3}}
    expect: {entity: /World/Lab/incubator/incubator__cabinet, property: innerOpen, equals: true}
  - id: 3
    do: {entity: /World/Lab/panda_rail/rail__rail_carriage, action: moveTo, params: {positionM: 2.6, durationSeconds: 3}}
    expect: {entity: /World/Lab/panda_rail/rail__rail_carriage, property: phase, equals: idle}
  - id: 5
    do: {entity: /World/Lab/panda_rail/panda__hand, action: setGripper, params: {openingM: 0.19, durationSeconds: 1}}
    expect: {entity: /World/Lab/panda_rail/panda__hand, property: phase, equals: idle}
  # ... 步骤 6–12：探入培养箱、0.13 m 合爪、抬起退出至搬运位 ...
  - id: 13
    do: {entity: /World/Lab/panda_rail/rail__rail_carriage, action: moveTo, params: {positionM: 1.5, durationSeconds: 3}}
    expect: {entity: /World/Lab/panda_rail/rail__rail_carriage, property: phase, equals: idle}
  - id: 14
    do: {entity: /World/Lab/incubator/incubator__cabinet, action: closeInner, params: {durationSeconds: 2.5}}
    expect: {entity: /World/Lab/incubator/incubator__cabinet, property: innerClosed, equals: true}
  - id: 15
    do: {entity: /World/Lab/incubator/incubator__cabinet, action: closeOuter, params: {durationSeconds: 3}}
    expect: {entity: /World/Lab/incubator/incubator__cabinet, property: outerClosed, equals: true}
  - id: 16
    do: {entity: /World/Lab/imager/imager__housing, action: extend, params: {durationSeconds: 2.5}}
    expect: {entity: /World/Lab/imager/imager__housing, property: extended, equals: true}
  # ... 步骤 17–20：观察站放板位、0.19 m 张爪、退回安全位 ...
  - id: 21
    do: {entity: /World/Lab/imager/imager__housing, action: retract, params: {durationSeconds: 2.5}}
    expect: {entity: /World/Lab/imager/imager__housing, property: retracted, equals: true}
  - id: 22
    do: {entity: /World/Lab/panda_rail/panda__hand, action: home, params: {durationSeconds: 2}}
    expect: {entity: /World/Lab/panda_rail/panda__hand, property: phase, equals: idle}
  - id: 23
    do: {entity: /World/Lab/panda_rail/rail__rail_carriage, action: moveTo, params: {positionM: 1.2, durationSeconds: 3}}
    expect: {entity: /World/Lab/panda_rail/rail__rail_carriage, property: phase, equals: idle}
```

## 第二支短片：一次细胞传代 · 14 步轨迹预演 {#passage-14-steps}

在推进 Lab World 实时接触驱动的同时，我们在 `world-building` 的 MuJoCo + Blender 管线中，把这座实验室的**完整 14 步细胞传代流程**（六孔合并样本、一支样品管配一支配平管、一块子板）做了一次端到端轨迹预演。

<SiteVideo
  :film="PASSAGE_FILM"
  caption="《一次细胞传代 · 14 步》（01:55 / 中文烧录字幕）—— MuJoCo 连续计算 911.9 秒物理轨迹并通过 20 项门禁校验，再由 World Building 在 Blender 中逐帧渲染。"
/>

这支短片由 MuJoCo 连续运行 **911.9 秒**物理仿真（分为 `p1_prep`、`p2_dissoc`、`p3_pellet`、`p4_seed` 四个首尾衔接的阶段），通过全部 **20 / 20 项正向门禁校验**与 **10 / 10 项负例反证测试**后，将刚体轨迹导出给 Blender 逐帧渲染生成：

| 步骤 | 阶段名称 | 核心设备与耗材 | 轨迹动作与门禁验证 |
| :--- | :--- | :--- | :--- |
| **01** | 取出母板 | 培养箱、导轨、机械臂、带盖母板 | 机械臂沿导轨至培养箱，依次开外门与内门，取出带盖母板并关门 |
| **02** | 观察站初检 | 显微观察站、母板 | 母板放入观察站托盘，抽屉收回并观察细胞状态 |
| **03** | 送工作站去盖 | 工作站 A、母板盖、固废托盘 | 母板送至移液工作站 A，揭开板盖并丢入固体废物托盘 |
| **04** | 去旧液与 PBS 清洗 | 移液站、吸头、PBS、废液 | 移液头取吸头，吸除旧培养液，加 PBS 清洗孔位并弃吸头 |
| **05** | 加解离液并等待 | 解离液、工作站 A、母板 | 加入解离液并执行标明的教学等待（5 秒代表 5 分钟解离） |
| **06** | 开管并收集六孔样本 | 开盖器、离心管、管座 | 空离心管经开盖器开盖、送入管座，将六孔液体合并收集至管内 |
| **07** | 样品管封盖与配平装机 | 样品管、配平管、离心机转子 | 样品管盖好管盖，与配平管一同装入离心机转子相对装载位（2/6 位） |
| **08** | 教学离心与开盖 | 离心机 | 关盖执行教学离心（6 秒代表 5 分钟离心），待转子停稳后开盖 |
| **09** | 双管取出归位 | 样品管、配平管、工作站 A、管架 | 取出两支离心管，样品管开盖送回工作站 A，配平管放回管架 |
| **10** | 去上清与重悬混匀 | 移液站、新鲜培养基、样品管 | 吸除上清液，加入新鲜培养基，反复吹打把细胞重悬混匀 |
| **11** | 取样计数与母板弃置 | 计数板、观察站、母板、固废托盘 | 取样至计数板送观察站计数（模拟读数 `1.6 × 10^6 个/mL`，存活率 `94%`），母板入固废 |
| **12** | 子板出箱与分装接种 | 培养箱、子板、工作站 A | 新子板从培养箱取出送至工作站 A，夹开板盖，将细胞悬液分装接种 |
| **13** | 子板加盖回箱 | 子板、培养箱第三层 | 子板盖好板盖，送回培养箱第三层放稳，依次关好内门与外门 |
| **14** | 计数板弃置与全机复位 | 计数板、固废托盘、Panda 机械臂 | 计数板丢入固体废物托盘，Panda 机械臂回到停靠位，全部设备复位 |

> **关于 14 步轨迹预演的理想化说明**：为了让完整工艺在两分钟内清晰可辨，步骤 5（解离等待，5 秒代表 5 分钟）与步骤 8（离心沉降，6 秒代表 5 分钟）采用了标明的教学时长；步骤 11 的细胞密度与存活率读数为符号化模拟数据；此外，小管、管盖与吸头等细小部件在几何对准合格后采用约束连接完成转移。

## 下一步：把完整 14 步接触操作合入实时工作台

通过这两支视频可以看到，我们已经把**场景构建（World Building）**、**第 1 步多设备联动实操闭环（Lab World）**与**完整 14 步工艺轨迹预演（MuJoCo + Blender）**对齐到了同一座实验室场景上。

接下来的核心工程目标（跟踪于仓库议题 `#252`、`#254`、`#255`），是在 Lab World 的 USD / Newton 实时物理后端中落板、盖、门、抽屉与离心机盖的真实接触支撑，并将移液站、开盖器、离心机与物料状态扩展到完整 14 步 Operation Plan——让 Agent 在实时工作台里完成从取板、移液、离心到子板回箱与全机复位的完整实验。
