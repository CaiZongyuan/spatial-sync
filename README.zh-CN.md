# 实验室信息底座

[English](README.md) | 简体中文

本地优先的实验室工作台。Synthetic 模式不依赖 Newton、Warp 或 GPU；Newton
模式由独立 Runner 拥有物理状态，API 提供 HTTP 和 WebSocket。信息平面的
范围和验收以 [规格 v1](docs/spec/LAB_INFORMATION_FOUNDATION_SPEC_V1.md) 为准。

需要 Python 3.13、uv、Node.js 22 及 pnpm。

```powershell
uv sync --frozen --group dev
pnpm install --frozen-lockfile
uv run --frozen python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```

另开终端执行 `pnpm dev`，打开 http://127.0.0.1:5173。
选择 **Synthetic Laboratory** 可在没有物理引擎的环境中使用。
健康检查 `/api/v1/health`，API 文档 `/docs`。前端默认代理 API 到 8000，
`SPATIAL_API_URL` 可指定其他地址。

实际 Newton 场景需要显式安装并保留可选依赖：

```powershell
uv sync --frozen --group dev --extra simulation --extra usd
uv run --frozen --extra simulation --extra usd python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```

只运行一个 API worker、一个权威 Runner。环境使用期间，检查使用
`uv run --no-sync`，避免普通 `uv run` 移除仿真依赖。附带场景和资产支持离线。

| 场景 | 数据源 | 用途 |
| --- | --- | --- |
| `lab-demo` | synthetic | 无物理依赖的实验室 |
| `falling-boxes` | newton | 刚体、Body/Site 选择与会话控制 |
| `robot-and-tubes` | newton | Franka FR3、试管与离心机 |
| `usd-lab` | newton | Y-up USD 导入与视觉回退 |

选中 Body 或 Site 后可暂停、单步、重置和恢复。多个浏览器共享权威会话，
各自保持独立相机。切换场景前关闭当前会话。Inspector 展示权威采样，视口可插值。

空间协议进入 v2 精简流程：WebSocket 使用完整二进制位姿帧，HTTP 快照与位姿
仍返回 JSON。保留会话控制、可靠事件、心跳、重连与单连接唯一发送者。
接口和权限规则见 [英文说明](README.md)。

测试、端口隔离及清理步骤见 [验证指南](docs/VERIFICATION.md)。基础检查不依赖
Newton 或 GPU；实际物理另以 CPU/CUDA 检查验证。本地结果不代表外部 CI。

其他资料：[机器人](examples/robot-and-tubes/README.md)、
[USD 兼容性](examples/usd-lab/README.md)、[协议](packages/protocol/README.md)。
本规格不包含部署、真实设备驱动或多租户。
