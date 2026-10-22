# SqurveBridge Interactive Demo

本地交互工作台：React 前端（`demo-app/`）+ Flask API（`demo/api_server.py`）+ 内嵌 Pi Agent（npm SDK `@earendil-works/pi-coding-agent`，版本锁定在 `demo/package.json`）。
它用于运行 Text-to-SQL 工作流、查看实验结果和检查持久化证据。

## 一键启动 / 关闭

在**仓库根目录**执行：

```bash
# 启动 API (:7861) + 前端 (:5173)
./demo/start.sh

# 关闭上述进程并释放端口
./demo/stop.sh
```

启动成功后打开：

- 工作台：<http://127.0.0.1:5173>
- API 健康检查：<http://127.0.0.1:7861/api/health>

运行时 PID / 日志目录：`workspace/sessions/runtime/`（已被 gitignore；可用 `SQURVE_WORKSPACE_DIR` 覆盖）。

| 文件 | 说明 |
|------|------|
| `api.pid` / `web.pid` | 进程号 |
| `api.log` / `web.log` | 标准输出日志 |
| `demo.env` | 本次启动的 host/port 记录 |

### 可选环境变量

| 变量 | 默认 | 含义 |
|------|------|------|
| `SQURVE_DEMO_API_HOST` | `127.0.0.1` | API 监听地址 |
| `SQURVE_DEMO_API_PORT` | `7861` | API 端口 |
| `SQURVE_DEMO_WEB_HOST` | `127.0.0.1` | Vite 监听地址 |
| `SQURVE_DEMO_WEB_PORT` | `5173` | 前端端口 |
| `PI_AGENT_PROVIDER` | 跟随 `SQURVE_LLM_PROVIDER` | Pi 使用的模型服务 |
| `PI_AGENT_MODEL` | 跟随 `SQURVE_LLM_MODEL` | Pi 使用的模型 |
| `SQURVE_DEMO_AGENT` | `on` | 设为 `off` 时为无 Agent 的展示版：服务端拒绝所有 `/api/agent*` 路由，页面不显示 Agent 面板 |

示例：

```bash
SQURVE_DEMO_WEB_PORT=5174 ./demo/start.sh
```

### 展示版（无 Agent）

```bash
SQURVE_DEMO_AGENT=off ./demo/start.sh
```

不需要 Node 的 Pi SDK 即可展示已接入的方法和 benchmark、横向比较，以及在编排页把某个阶段换成同类型的其他 Actor 形成**变体**，与原方法在同一实验中用相同样本运行并对比。变体只改该次运行的配置，`reproduce/configs/` 不会被修改。

## 前置依赖

1. 仓库根目录已有可用的 Python 虚拟环境 `.venv/`（含 demo API 依赖）
2. 已安装 Node.js 22.19+ / npm
3. 首次启动会按需安装内嵌 Pi SDK（`npm ci --prefix demo`），并在缺少 `demo-app/node_modules` 时执行 `npm ci`
4. 本地 LLM 凭据：复制 `.env.example` → `.env`，或在页面中配置 provider

```bash
# 若尚未创建虚拟环境，在仓库根目录安装锁定依赖
uv sync --python 3.11
```

## 页面导览

主界面为「五步流程 + 右侧常驻 Pi Agent」分栏工作台（深链 `#configure`、`#compose`、`#query`、`#board`、`#evidence`）：

| 步骤 | 作用 |
|------|------|
| **Studio 工作室** | 浏览已集成方法与数据库的 flashcard 目录 |
| **Compose 编排** | Method × Database 连线矩阵；查看每对连接的 Actor 工作流与配置来源 |
| **Query 查询** | 交互式 NL→SQL 工作台：Schema 树（表/列/主外键、搜索、SQL 命中高亮）＋「问题 → 管线阶段 → SQL 编辑器 → 结果表」；SQL 可编辑重跑、导出 CSV；查询上下文可发给 Pi 分析，Pi 回答中的 SQL 也可送回工作台 |
| **Run 运行** | 设置采样参数，启动/暂停/恢复 config 评估任务并监控进度（本地限定） |
| **History 历史** | 浏览归档 run、展开图表对比（雷达、Formal tables、错误/特征、成本） |

Query 步骤依赖的只读 schema 端点为 `GET /api/databases/<id>/schema`；SQL 执行为只读 SELECT、单语句、5 秒超时、最多 500 行。

## Hugging Face Space 凭据

公开 Space 不配置维护者共享模型 Key。每位访问者分别使用两个入口：

- **Configure SQL API**：配置 Squrve SQL 生成所用的 provider、model 和 Key；
  凭据只保存在当前浏览器会话对应的服务端内存中，空闲最多 30 分钟。
- **Login to Pi**：直接使用 Pi 原生 provider 登录与模型选择；凭据仅存在于该
  Pi 子进程内，结束 Agent session 即清除，与 SQL Key 完全分离。

凭据不会写入浏览器存储、`.env`、文件、日志或 API 响应。页面刷新、会话过期或
Space 重启后可能需要重新输入。公开演示建议使用限额、可撤销的临时 Key；托管 Pi
始终使用 `hosted-readonly` 工具配置。

## 手动启动（调试用）

```bash
# Terminal A
.venv/bin/python demo/api_server.py --host 127.0.0.1 --port 7861

# Terminal B
cd demo-app && npm run dev
```

Vite 将 `/api` 代理到 `http://127.0.0.1:7861`。

## 常见问题

**端口占用**

```bash
./demo/stop.sh
# 或查看占用
lsof -nP -iTCP:7861 -sTCP:LISTEN
lsof -nP -iTCP:5173 -sTCP:LISTEN
```

**前端 Bad Gateway**

通常是 API 未起来。查看 `workspace/sessions/runtime/api.log`，确认 `/api/health` 可访问后再刷新页面。

**重复启动**

`start.sh` 若检测到已有 PID 或端口占用会直接退出，先 `./demo/stop.sh`。

## 安全边界

API 默认只监听 `127.0.0.1`。本地 Pi 会话可使用读写和命令工具，因此不应把本地服务暴露到不受信任的网络。Hugging Face 部署自动切换为 `hosted-readonly`，只向 Pi 开放 `read`、`grep`、`find` 和 `ls`。
