# 快速开始

Synthetic 模式不需要 Newton、Warp、CUDA 或 GPU，几分钟内就能在本机看到一座可以操作的仿真实验室。

## 准备

需要 Python 3.13、[uv](https://docs.astral.sh/uv/)、Node.js 22 及以上版本和 pnpm。

在项目仓库的根目录安装依赖：

```sh
uv sync --frozen --group dev
pnpm install --frozen-lockfile
```

## 启动

先启动 API：

```sh
uv run --frozen python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```

另开一个终端启动 Web：

```sh
pnpm dev
```

打开 <http://127.0.0.1:5173>，进入 **会话**，选择 **Synthetic Laboratory**。健康检查在 `/api/v1/health`，API 文档在 `/docs`。

## 让 agent 接入

Web、`lw` CLI 和 MCP 走同一条动作管线。先看实验室里有什么，再动手：

```sh
uv run lw ls
uv run lw show /World/Lab/Centrifuge
uv run lw do /World/Lab/Centrifuge closeLid
uv run lw check /World/Lab/Centrifuge lidOpen equals false
```

`uv run lw mcp` 通过 stdio 提供通用 MCP 工具。平台 skill 教 agent 怎么用这些命令：用 `lw skills ls` 列出，用 `lw skills install --target DIR` 安装到指定的 agent 目录。

## Newton 场景

需要物理仿真时，安装可选依赖后再启动 API：

```sh
uv sync --frozen --group dev --extra simulation --extra usd
uv run --frozen --extra simulation --extra usd python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```

内置场景：

| 场景 | 物理源 | 用途 |
| --- | --- | --- |
| `lab-demo` | synthetic | 不依赖物理引擎的确定性实验室 |
| `falling-boxes` | newton | 刚体、Body/Site 选择与控制 |
| `robot-and-tubes` | newton | 官方 Franka FR3、试管和离心机 |
| `usd-lab` | newton | Y-up USD 导入与视觉回退 |

同一时间只能有一个活跃会话；切换场景前先关闭当前会话。
