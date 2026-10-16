# Newton Spatial Sync

[English](README.md) | 简体中文

Newton Spatial Sync 是一个空间状态同步网关和浏览器工作台，用于查看物体、机器人关节和 Site 的世界位姿，并控制共享仿真。

- **Synthetic 模式**：不需要 Newton、Warp、CUDA 或 GPU，可直接运行演示和基础测试。
- **Newton 模式**：独立 Runner 进程负责物理仿真，FastAPI 负责 HTTP、WebSocket 和会话管理。
- **浏览器工作台**：Three.js 渲染场景，提供对象树、坐标检查器、控制按钮和诊断指标。
- **远程 Runner**：可通过经过认证的 TLS 控制与帧通道连接另一台机器上的 Runner。

Phase 0、Phase 1 和 Phase 2 A–D 已完成本地集成；Phase 3 预留扩展接口。完整规范见 [v1.2 技术规范](docs/spec/NEWTON_SPATIAL_SYNC_SPEC_V1.2.md)。

## 环境要求

| 工具 | 要求 |
| --- | --- |
| Python | 3.13，项目暂不支持 3.14 |
| Python 包管理 | [uv](https://docs.astral.sh/uv/) |
| Node.js | 22 或以上 |
| pnpm | 10.32.1，与 `packageManager` 声明一致 |
| GPU | Synthetic 和 Newton CPU 模式不需要 GPU；CUDA 模式需要兼容的 NVIDIA GPU 和驱动 |

以下命令在仓库根目录执行，以 Windows PowerShell 为例。

## 快速启动：无需 GPU

安装锁定的基础依赖：

```powershell
uv sync --frozen --group dev
pnpm install --frozen-lockfile
```

如果准备使用 Newton，可直接改用下一节的完整依赖安装。

终端一启动 API：

```powershell
uv run --no-sync python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```

终端二启动前端：

```powershell
pnpm dev
```

打开 **http://127.0.0.1:5173**，选择 **Synthetic Laboratory** 和 **synthetic**，点击 **Launch workspace**。

| 地址 | 用途 |
| --- | --- |
| http://127.0.0.1:5173 | 开发模式工作台 |
| http://127.0.0.1:8000/api/v1/health | API 与会话健康状态 |
| http://127.0.0.1:8000/docs | HTTP API 文档 |

## 启动 Newton 场景

先停止使用当前 Python 环境的服务，再安装仿真和 USD 扩展：

```powershell
uv sync --frozen --group dev --extra simulation --extra usd
pnpm install --frozen-lockfile
```

随后按照上节分别启动 API 和前端，仍使用 `uv run --no-sync`。也可以在启动命令中显式保留扩展：

```powershell
uv run --frozen --extra simulation --extra usd python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```

**已安装扩展后，不带 extras 的普通 `uv run` / `uv sync` 可能移除可选包。** 服务运行期间使用 `--no-sync`；需要重新安装时，先关闭服务，再明确选择所需扩展。

| 场景 ID | 界面名称 | 内容 |
| --- | --- | --- |
| `lab-demo` | Synthetic Laboratory | 双连杆机器人、20 支移动试管、离心机和资源回退演示 |
| `falling-boxes` | Newton Falling Boxes | Newton 下落与关节基础场景 |
| `robot-and-tubes` | Newton Robot and Tubes | Franka FR3 机器人、试管和实验台 |
| `usd-lab` | Newton USD Laboratory | USD 导入、轴向转换、网格和兼容性演示 |
| `stress-100` 等 | Synthetic stress | 100 / 500 / 2,000 / 10,000 个运动 Body，以及运动、静态物体混合场景 |

场景和 Franka 资源已包含在仓库中。一个会话只使用一个权威 Runner；API 使用单 worker，多个浏览器共享该 Runner。

## 打开 Newton 自带的参考 GUI

项目提供 Newton 的 **ViewerViser** 浏览器参考界面。安装 `simulation` 扩展后，在另一个终端运行：

```powershell
uv run --no-sync python tools/viser_reference.py robot-and-tubes --device cpu --port 8080
```

打开 **http://127.0.0.1:8080**，保持终端运行。按 `Ctrl+C` 关闭。

可将场景改为 `falling-boxes` 或 `usd-lab`；USD 场景需要 `usd` 扩展。使用 CUDA 时，将 `--device cpu` 改为 `--device cuda:0`。

**ViewerViser 会新建同场景的独立仿真。** 它用于对照 Newton 的原始显示，不与工作台中的现有会话共享 Runner、暂停状态或时间。工作台连接的 Runner 以无界面模式运行；查看同一会话的实时状态，请连接该会话的工作台地址。

## 工作台操作

1. 选择场景和数据源，点击 **Launch workspace**。
2. 在场景或 **Scene explorer** 中选择 Body、Site 或 Joint。
3. 在 **Inspector** 查看世界 XYZ、四元数、`seq`、`simTime` 和 `epoch`。
4. 使用 **Pause / Resume / Step / Reset** 控制共享仿真。`Step` 仅在暂停时可用；重置使 `epoch` 增加并清空插值历史。
5. 打开 **Diagnostics** 查看物理步频、RTF、读回、发布、渲染及心跳指标。Synthetic 未测量的 Newton 指标显示为空。

Site 的世界位姿由 Body 世界位姿与 Site 局部位姿组合得到，不能把关节锚点当成 Body 原点。机器人 TCP 示例是 `/World/Lab/RobotA/fr3/fr3_hand/tcp`。

世界坐标以米为单位、Z 轴向上，四元数顺序为 `[x, y, z, w]`。对象树中的逻辑父子关系不表示渲染时重复叠加 Body 世界变换。

渲染画面可以插值，Inspector 显示权威采样。核对坐标时先暂停，再比较同一 `seq` / `simTime` 的数据。

第二个浏览器可以打开相同会话 URL，或在首页输入 Session ID。控制结果对所有观察者生效。切换到另一场景前，先点击 **Close session** 释放当前会话。

### 订阅与恢复

支持全部对象、选中实体、设备分组和相机视口订阅，以及 JSON / Binary 帧、稀疏增量和完整关键帧。

固定范围订阅的 Body 并集会传到 Newton Runner，在 GPU gather 后按目标数量读回。视口订阅保守读取全部 Body，以发现进入视口的物体。订阅切换、重连和命令仍保留完整采样及可靠顺序，不会把不同时间的行拼成一份新快照。

普通 HTTP `/pose` 查询只读取已发布的完整缓存，不触发源端采集；返回的 `seq`、`simTime` 和 `staleMs` 属于该份缓存。显式 `/snapshot` 和订阅恢复可请求完整采样。

## 使用生产构建

以下命令可重新打开交付时使用的 **8009 / 5189** 地址。项目关闭后需要重新运行，端口不会常驻。

终端一：

```powershell
$env:SPATIAL_ALLOWED_ORIGINS='http://127.0.0.1:5189'
uv run --no-sync python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8009
```

终端二：

```powershell
$env:SPATIAL_API_URL='http://127.0.0.1:8009'
pnpm build
pnpm --filter @spatial-sync/web exec vite preview --host 127.0.0.1 --port 5189 --strictPort
```

打开 **http://127.0.0.1:5189**。更换前端端口时，同步配置 API 的 `SPATIAL_ALLOWED_ORIGINS`；`SPATIAL_API_URL` 在启动前端的终端设置。

## 关闭项目

1. 在工作台点击 **Close session**，释放该会话及其 Newton Runner。
2. 在 API、前端和 ViewerViser 各自的终端按 `Ctrl+C`。
3. 重新启动后创建或连接有效会话；已关闭的演示会话不会自动恢复。

只关闭自己创建的进程和会话。会话中的 **Keep running without observers** 决定最后一个订阅者离开后是否继续仿真。

## 测试与验证

基础门禁不依赖 Newton 或 GPU。准备好基础环境后运行：

```powershell
uv run --no-sync python -m pytest tests/contract tests/unit tests/gateway -q
uv run --no-sync ruff check .
pnpm protocol:check
pnpm test:protocol:ts
pnpm test:web
pnpm build
```

浏览器测试需要先在 8000 启动一个没有手动活动会话的 API：

```powershell
pnpm --filter @spatial-sync/web exec playwright install chromium
pnpm test:e2e
```

Playwright 默认自行启动 5184 前端，使用 Chromium 软件 WebGL。设置 `SPATIAL_API_URL` 可指定其他 API；设置 `SPATIAL_WEB_URL` 可复用已经启动的前端。Linux CI 安装浏览器时使用 `playwright install --with-deps chromium`。

安装仿真、USD 扩展后，可在隔离的测试 API 上开启全部可选浏览器场景：

```powershell
$env:SPATIAL_NEWTON_E2E='1'
$env:SPATIAL_TEST_NEWTON='1'
pnpm test:e2e
```

这些测试包含主动结束测试所创建 Runner 的恢复验证，不要与正在使用的手动会话共用 API。CPU / CUDA / 导入专项测试位于 `tests/sim_runtime`。

本次集成的结果为：基础 Python **204 通过、2 项可选跳过**，前端 **64 通过**，TypeScript 协议 **6 通过**，完整浏览器 **15 通过**；另有真实 CPU/CUDA 与 Newton 经 TLS 的验证。详见 [最终验收记录](docs/development/v1.2/FINAL-VALIDATION.md) 和 [可复用验证指南](docs/development/VERIFICATION.md)。这些是已记录结果，不代表外部 CI 已运行。

## 主要 API

所有路径均以 `/api/v1` 开头，完整交互文档位于运行中的 API `/docs`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health`、`/scenes` | 健康状态、注册场景 |
| POST | `/sessions` | 创建或复用会话 |
| GET | `/sessions/{id}`、`/sessions/{id}/scene` | 会话状态、Manifest |
| GET | `/sessions/{id}/snapshot` | 完整 Transform Snapshot |
| WS | `/sessions/{id}/ws` | 订阅、帧、命令、状态及心跳 |
| GET | `/sessions/{id}/pose?key=...` | Body / Site 缓存世界位姿与采样元数据 |
| GET | `/sessions/{id}/joints/state?key=...` | 按需查询关节坐标，默认超时 200 ms |
| GET | `/sessions/{id}/stats` | 来源、分发、丢帧和队列指标 |
| GET | `/sessions/{id}/meshes/{meshId}`、`/assets/{assetId}` | 已注册的网格和静态资源 |
| POST | `/sessions/{id}/reload`、`/sessions/{id}/restart` | 重载；重启 FAILED 会话 |
| DELETE | `/sessions/{id}` | 关闭会话及来源资源 |

`entityKey` 带有 `/`，作为查询参数传递时要 URL encode。命令串行执行，并缓存最近 256 个请求的确认以实现幂等；可靠帧、确认和状态按顺序发送。暂停停止周期帧，心跳继续。

## 远程 Runner 与远程访问

使用临时证书和独立进程执行一次本地 TLS 演示：

```powershell
uv run --no-sync python tools/smoke_remote_runner.py
```

此命令是自动验证工具，完成后清理其进程和临时凭据。添加 `--browser` 可运行浏览器旅程；`--source newton` 使用真实 CPU 引擎，需要 `simulation` 扩展。

手动部署 Runner 时，服务端使用 `tools/run_remote_runner.py`，通过 `--cert`、`--key` 提供证书和私钥。两端使用受保护、至少 32 字符的 `SPATIAL_RUNNER_TOKEN`；Gateway 配置如下：

| 配置 | 含义 |
| --- | --- |
| `SPATIAL_RUNNER_CA_FILE` | 信任的 CA 文件 |
| `SPATIAL_RUNNER_HOST` / `SPATIAL_RUNNER_PORT` | Runner 地址，默认 `127.0.0.1:8766` |
| `SPATIAL_RUNNER_SERVER_NAME` | 证书身份，默认 `localhost` |
| `SPATIAL_RUNNER_KIND` | 远程来源类型，默认 `newton` |

未设置 Runner 配置时使用本地模式；部分配置缺失会明确失败。TLS 证书校验必须保留，网络上传输有界的类型化消息和帧，不使用 pickle。

Gateway 对外访问另需 TLS 反向代理、`SPATIAL_REMOTE=1`、`SPATIAL_OPERATOR_TOKEN` 和明确的 `SPATIAL_ALLOWED_ORIGINS`；可选 `SPATIAL_VIEWER_TOKEN` 仅允许观察。令牌通过 Authorization Header 传递，不放在 URL。完整命令见 [英文远程部署说明](README.md#remote-runner-over-tls)。

## 项目结构与资源

| 目录 | 内容 |
| --- | --- |
| `apps/api` | FastAPI、会话、订阅、数据源适配器 |
| `apps/web` | Three.js 工作台、Inspector、诊断界面 |
| `packages/protocol` | Pydantic 协议、生成的 TypeScript / JSON Schema |
| `packages/sim_runtime` | Newton 场景、步进、Manifest、变换提取 |
| `packages/transport` | 本地 IPC、帧编码、TLS 与来源缓存屏障 |
| `examples` | 下落、机器人和 USD 示例 |
| `assets` | 注册资源、场景绑定、机器人文件及许可证 |
| `tests`、`tools` | 契约、单元、集成、浏览器测试和参考工具 |

静态资源通过 `assets/registry.yaml` 注册，实体绑定位于 `assets/bindings/{sceneId}.yaml`。资源缺失时按主视觉、Newton 原生视觉、碰撞体、占位体的层级回退，不同时叠加多个视觉组。USD 场景在导入时完成 Y-up 到 Z-up 转换，不逐帧同步 USD 或静态网格。

## 已知限制与排查

- **端口变化后出现 403**：确认 API 启动时允许了实际前端 Origin；只修改前端代理地址不足以修改 API 的 Origin 配置。
- **提示缺少 Newton / USD**：检查运行 API 的环境是否安装了对应 extras，避免基础环境同步移除可选包。
- **无法新建另一场景**：当前只允许一个活动会话，先关闭原会话。
- **Pose 数据较旧**：检查 `simTime`、`seq` 和 `staleMs`。普通 Pose 是只读缓存；暂停时采样时间不会持续刷新。
- **大场景性能**：已测软件渲染环境中，10k 场景两次有效运行约 4.03 FPS，未达到建议的 45 FPS；减少读回字节不等于已证明整体加速。
- **求解器兼容性**：当前机器人完整接触场景使用 MuJoCo，Featherstone 的接触兼容性限制已记录。
- **验证范围**：远程传输验证以本机 TLS 为主，未证明 WAN 或跨硬件性能；仓库未配置 Git remote，未运行外部 CI。

更多结果见 [开发与验收追踪](docs/development/v1.2/TRACKER.md)、[机器人说明](examples/robot-and-tubes/README.md) 和 [USD 兼容性说明](examples/usd-lab/README.md)。
