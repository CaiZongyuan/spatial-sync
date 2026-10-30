## Problem Statement

实验室平台已经能用 Newton 或 Synthetic 源跑仿真、把空间状态同步到浏览器，并让 Web、`lw` CLI 和 MCP 通过一条动作管线操作设备。但它还不能让 agent "管理实验室并训练机器人"：

- 不知道实验室里有什么：场景写死在代码里，素材无法管理，Blender 做好的 FR3 实验室无法进入平台。
- 看不到实验室此刻的状态：没有物联网设备、读数历史和告警，agent 不会被主动告知异常。
- 看不到 agent 要做什么：没有可见的计划和机械臂路径预览。
- 无法训练机器人：Isaac Lab 训练在平台外手工运行，结果回不到平台评估和部署；8 GB GPU 没有调度。
- 没有可度量的自我改进：无法比较 agent 或控制器的版本，没有接受规则、实验记录、成本和预算。
- 界面只是工程工作台，缺少管理与审批结构。

## Solution

把平台扩展为 RSI（Recursive Self-Improvement）实验室平台：人和 agent 通过同一套 HTTP API、`lw` CLI、MCP 与平台 skill 完成"素材 → 场景 → 计划 → 作业 → 评估 → 策略 → 部署为设备程序 → 观察失败 → 改进"的闭环，人只在关口审批。

- **库**：素材与场景是带版本、带验证报告的包；USD 为规范格式（ADR 0001）。
- **实况与告警**：仿真物联网设备 + Device Host/MQTT 实验室实况；读数历史、过期标记、告警规则、agent 收件箱。
- **计划先行**：操作计划与机械臂 TCP 预览；训练计划附 grilling 访谈记录与可测量假设。
- **训练与部署**：外部作业在计算节点上用 Isaac Lab（Newton MJWarp）训练，训练视图实时可见；平台评估集判定，满足接受规则后经审批替换设备程序。
- **两层改进**：同一套版本、评估、接受规则改进 Agent（改 Harness）与 Controller（改策略、程序、奖励、数据）；评估中心、成本中心、资源页。
- **新界面**：可折叠侧栏的仪器控制台框架，视觉参照 deeprsi.com；UI 原型 v4 为参照。

## 完整规格

用户故事（130 条）、实现决定、契约变更、HTTP API 总览、错误码、测试接缝、范围之外均在仓库中，以其为准：

- [Lab RSI Platform PRD](https://github.com/CaiZongyuan/spatial-sync/blob/main/docs/spec/LAB_RSI_PLATFORM_PRD_V1.md)（从这里开始）
- [Lab Web UI](https://github.com/CaiZongyuan/spatial-sync/blob/main/docs/spec/LAB_WEB_UI_SPEC_V1.md)（W0 与逐页契约）
- [Lab Assets and Scenes](https://github.com/CaiZongyuan/spatial-sync/blob/main/docs/spec/LAB_ASSETS_AND_SCENES_SPEC_V1.md)（A1–A3）
- [Lab Live Operations](https://github.com/CaiZongyuan/spatial-sync/blob/main/docs/spec/LAB_LIVE_OPERATIONS_SPEC_V1.md)（L1–L3）
- [Lab Training](https://github.com/CaiZongyuan/spatial-sync/blob/main/docs/spec/LAB_TRAINING_SPEC_V1.md)（T0–T3）
- [Lab RSI](https://github.com/CaiZongyuan/spatial-sync/blob/main/docs/spec/LAB_RSI_SPEC_V1.md)（R1–R4）
- 术语：GLOSSARY.md；决策：docs/adr/

## 交付顺序

```text
W0 设计系统与框架 ─┐
                   ├─► A1 库 + 场景包 ─► A2 素材包 + 验证 ─► A3 fr3-lab 导入
T0 FR3 接触抓取技术验证（平台外，与 W0/A1 并行）
A1 ─► L1 传感器 + 告警 + 历史 + 收件箱 ─► L3 实验室实况（MQTT）
A1 ─► L2 操作计划 + 路径预览
A3 ─► T1 作业 + 节点 + 训练视图 ─► T2 评估 + 策略 + 实验记录 ─► T3 脚本程序 + 示范
T0 ─► #35 (robot_arm.policy)
T2 ─► R1 评估中心 + 成本中心（GPU）
A1 + L2 ─► R2 Agent 运行器 + Agent 评估集 + LLM 成本 ─► R3 Agent 层一圈 RSI
A2 + T2 ─► R4 Embodiment / Controller / Robot Model
```

子议题就是可领取的 ticket，阻塞关系用 GitHub 原生依赖表示。每个 ticket 是一个垂直切片（API、CLI/MCP、UI、测试），完成即可演示或验证。