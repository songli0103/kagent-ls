# agent-runtime

kagent-ls 项目的 Python runtime：**长连接 chat 服务**，由 `kagent-ls` Operator 通过 Agent CR 物化成 Deployment。每个 Agent CR 起一个 runtime 容器，暴露 OpenAI 兼容的 `POST /chat/completions` 端点，背后是一个 langchain 0.3 的 tool-calling agent。

跟 [项目根 README](../README.md) 配合阅读——那里讲 Operator 和 CRD，这里讲 runtime 容器本身。

## 它做什么

- 起一个 FastAPI 服务（`uvicorn`），监听 8080
- 容器启动时构造 `ChatOpenAI`（指向 MiniMax 的 OpenAI 兼容端点）+ 两个 agent
- `/chat/completions` 收到请求 → 按 `agent` 字段选 `cluster` 或 `ops` agent → 调 langchain `AgentExecutor` → 拿最终答案 + 工具调用轨迹
- 工具调用走 `kubernetes` Python client，**在 pod 内用 ServiceAccount 凭据**，本地跑用 `~/.kube/config`
- 可选：把每条 turn 持久化到挂载的 PVC（`RUNTIME_HISTORY_DIR`），跨 pod 重启保留会话

## 在系统里的位置

```
┌──────────────────────────────────────────┐
│ Agent CR (由 Operator 调和)              │
│  spec.runtime.image = <this image>       │
│  spec.credentialsSecret → envFrom        │
│  spec.history.enabled → PVC mount        │
└──────────────────┬───────────────────────┘
                   ▼
        ┌──────────────────────┐
        │ Deployment           │
        │   1 Pod, 8080        │
        │   ServiceAccount:     │
        │     <name>-runtime   │
        └──────────┬───────────┘
                   ▼
        ┌──────────────────────────────────────┐
        │ runtime container (本项目)           │
        │  ┌────────────────────────────────┐  │
        │  │ FastAPI app (server.py)        │  │
        │  │  /chat/completions             │  │
        │  │  /tools                        │  │
        │  │  /classify                     │  │
        │  │  /sessions/{id}                │  │
        │  │  /health, /ready               │  │
        │  │  /  (单页 webui)               │  │
        │  └────────────────────────────────┘  │
        │  ClusterAgent (8 读工具)              │
        │  OpsAgent (4 读 + 3 写)               │
        │  HistoryStore (PVC-backed)            │
        └──────────────────────────────────────┘
```

## 双 agent 模型

`server.py` 启动时同时构造两个 agent，存在 `app.state.agents` 字典里，按请求 `agent` 字段查表分发。

| Agent | name | 工具数 | 工具 | 配套 RBAC |
|---|---|---|---|---|
| `ClusterAgent` | `cluster` | 8 | 全部只读 | `agent-runtime`（读） |
| `OpsAgent` | `ops` | 7 | 4 读 + 3 写 | `agent-runtime`（读） + `agent-runtime-write`（写） |

请求体：

```json
{
  "model": "MiniMax-M3",
  "agent": "cluster",          // 或 "ops"；默认 cluster
  "messages": [
    {"role": "user", "content": "default ns 的 pod 都有哪些"}
  ]
}
```

`/tools?agent=cluster|ops` 列出对应 agent 的工具描述。

## 工具清单（11 个）

### `ClusterAgent`（8 个只读工具）

| 工具 | 作用 | 资源 |
|---|---|---|
| `list_pods` | 列出 Pod（ns 可空，空=全集群） | `pods` |
| `list_deployments` | 列出 Deployment | `deployments` |
| `list_events` | 列出 Event | `events` |
| `describe_pod` | 单 Pod 详细 spec+status+相关 Event | `pods` |
| `get_pod_logs` | 读 Pod 最近 N 行日志（`tail_lines` 1..N） | `pods/log` |
| `get_deployment_status` | Deployment 副本数 + conditions | `deployments` |
| `list_nodes` | 列出所有 Node 状态 | `nodes` |
| `describe_node` | 单 Node 详细 conditions/allocatable/taints | `nodes` |

### `OpsAgent`（在 cluster 4 个之上 + 3 个写工具）

| 工具 | 作用 | 资源 + verbs |
|---|---|---|
| `list_pods` | 同上 | `pods:get,list` |
| `list_deployments` | 同上 | `deployments:get,list` |
| `describe_pod` | 同上 | `pods:get` |
| `get_deployment_status` | 同上 | `deployments:get` |
| **`restart_pod`** | 删 Pod 让 controller 重建 | `pods:delete` |
| **`restart_deployment`** | 给 pod template 打 `restartedAt` annotation 触发滚动重启 | `deployments:patch` |
| **`scale_deployment`** | 调 `/scale` 子资源改 replicas | `deployments/scale:patch,update` |

> `deployments/scale` 是独立子资源，RBAC 必须单独给规则——`deployments` 的 patch 权限不覆盖它。

## HTTP API

| Method | Path | 说明 |
|---|---|---|
| `GET` | `/` | 单页 chat webui（`agent_runtime/webui/index.html`） |
| `GET` | `/health` | liveness（永远 200 表示进程活着） |
| `GET` | `/ready` | readiness（agent 构造好才 200，否则 503） |
| `GET` | `/tools?agent=cluster\|ops` | 列出 agent 的工具描述 |
| `POST` | `/classify` | 轻量 LLM 意图分类，webui 用来自动选 agent（read/write） |
| `POST` | `/chat/completions` | 主端点，支持 `stream=true`（SSE） |
| `GET` | `/sessions/{session_id}` | 拉历史会话（PVC 模式才返回 200） |

### `POST /chat/completions` body 字段

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `model` | 否 | `LLM_MODEL` 环境变量 | LLM 模型名 |
| `agent` | 否 | `cluster` | `cluster` 或 `ops` |
| `messages` | 是 | - | OpenAI 风格 `[{role, content}, ...]` |
| `stream` | 否 | `false` | true 时 SSE 返回 |
| `session_id` | 否 | - | 启用会话持久化（需 PVC 挂载） |
| `temperature`, `top_p`, `n`, `user` | 否 | - | 接受但当前忽略（OpenAI 协议兼容） |

非流式返回：

```json
{
  "answer": "default ns 有 3 个 pod: ...",
  "tool_calls": [{"name": "list_pods", "args": {"namespace": "default"}}],
  "tool_results": [{"name": "list_pods", "result": [...]}],
  "model": "MiniMax-M3",
  "agent": "cluster"
}
```

流式返回 SSE 事件序列：

```
data: {"type": "tool_call",   "name": "list_pods", "args": {"namespace": "default"}}
data: {"type": "tool_result", "name": "list_pods", "result": [...]}
data: {"type": "done",        "answer": "..."}
data: [DONE]
```

## 配置（环境变量）

### 必填

| 变量 | 来源 | 说明 |
|---|---|---|
| `LLM_API_KEY` | Operator 注入（`envFrom: secretRef`） | LLM API key，缺了 `/ready` 返 503 |

### 可选

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | `https://api.minimaxi.com/v1` | OpenAI 兼容端点 |
| `LLM_MODEL` | `MiniMax-M3` | 模型名 |
| `RUNTIME_HOST` | `0.0.0.0` | uvicorn bind |
| `RUNTIME_PORT` | `8080` | uvicorn bind |
| `RUNTIME_LOG_LEVEL` | `info` | uvicorn 日志级别 |
| `RUNTIME_HISTORY_DIR` | （空） | 设了就启用会话持久化（operator 挂 PVC 时设） |

> **重要**：上一版 README 里的 `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` 已经**作废**。新名字是 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`，跟 `llm.py` 里的实际读取一致。

## 会话持久化

当 operator 在 Agent CR 里设了 `spec.history.enabled: true`，会建一个 PVC 并 mount 到容器内 `/var/lib/agent-runtime/history`，同时 `RUNTIME_HISTORY_DIR=/var/lib/agent-runtime/history` 注入。runtime 检测到该变量就启用 `HistoryStore`：

- 每个 session 一个 JSON 文件 `<dir>/<session_id>.json`
- 客户端发请求时带 `session_id` 字段，runtime 把 prior messages 加到 messages 前面再喂给 agent
- 完成后 best-effort 写回磁盘
- session_id 白名单：`[A-Za-z0-9._-]{1,128}`（防路径穿越）
- 单 session 上限 200 条消息（防无限增长）

`/sessions/{id}` 端点能拉历史（503 当 PVC 没挂），webui 用它在页面加载时恢复历史。

## 本地开发

### 前置

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) ≥ 0.4
- 一个能跑 LLM 的 API key

### 同步依赖 + 装包

```bash
cd runtime
uv sync                          # 创建 .venv 并装所有依赖
source .venv/bin/activate

# 或不用 venv 一次跑：
uv run python -c "from agent_runtime.llm import get_chat_model; print(get_chat_model().model_name)"
```

### 跑测试

```bash
uv run pytest                    # 全部
uv run pytest tests/test_tools.py::TestPodTool -v   # 单个文件
```

### 本地起服务

```bash
export LLM_API_KEY=<your-key>
# 可选：export LLM_BASE_URL=https://api.minimaxi.com/v1
# 可选：export LLM_MODEL=MiniMax-M3

uv run python -m agent_runtime.main
# 或：uv run agent-runtime
```

服务起来后：

```bash
# 健康检查
curl localhost:8080/health
curl localhost:8080/ready

# 列工具
curl 'localhost:8080/tools?agent=cluster'
curl 'localhost:8080/tools?agent=ops'

# 试一次 chat
curl -X POST localhost:8080/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "MiniMax-M3",
    "agent": "cluster",
    "messages": [{"role": "user", "content": "list pods in default namespace"}]
  }'

# 试一次 streaming
curl -N -X POST localhost:8080/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "MiniMax-M3",
    "agent": "cluster",
    "stream": true,
    "messages": [{"role": "user", "content": "list pods"}]
  }'

# 开 webui
open http://localhost:8080/
```

本地 K8s 工具调用走 `~/.kube/config`；要换 context 用 `KUBECONFIG=...` 或 `kubectl config use-context`。

## 构建容器镜像

```bash
cd runtime
docker build -t <your-registry>/cluster-agent:<tag> .
```

Dockerfile 关键点（见 `Dockerfile`）：

- Base：`python:3.11-slim`，装 `uv`
- 装依赖 + 装包分两个 layer 走 uv cache（加速 rebuild）
- 切到 `USER 1000:1000`（满足 K8s `runAsNonRoot: true`）
- `EXPOSE 8080`，`CMD ["python", "src/agent_runtime/main.py"]`

## 跟 Operator 的集成

runtime **不直接跟 Operator 通信**，只通过环境变量和文件：

| 通道 | 方向 | 内容 |
|---|---|---|
| `envFrom: secretRef: minimax-credentials` | Operator → runtime | `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` |
| `RUNTIME_HISTORY_DIR` | Operator → runtime | PVC 挂载路径，runtime 检测到即启用 `HistoryStore` |
| `RUNTIME_HOST` / `RUNTIME_PORT` / `RUNTIME_LOG_LEVEL` | Operator → runtime | 绑定参数（一般用默认即可） |
| `app.state.history` 文件 | runtime 写 | 每个 session 一个 JSON 文件 |
| stdout / stderr | runtime 输出 | JSON 结构化日志（stderr），web 响应（stdout） |

Operator 改 Secret → Deployment 滚动重启 → runtime 重读 env 拿到新凭证，无需 rebuild 镜像。

## 扩展：加新工具

1. 在 `src/agent_runtime/tools/` 下新建 `xxx_tool.py`，继承 `Tool` 基类，定义 `name: ClassVar[str]` + `execute()`
2. 在 `tools/__init__.py` 导出
3. 在 `src/agent_runtime/langchain_tools.py`：
   - 加一个 shim 函数（thin wrapper）
   - 加 `StructuredTool.from_function(...)` 条目
   - 决定加进 `build_tools()`（cluster）和/或 `build_ops_tools()`（ops）
4. 读工具不需要额外 RBAC；写工具要在 `deploy/agent-runtime-write-rbac.yaml` 加 `+kubebuilder:rbac:` 对应规则
5. rebuild 镜像 → push → 改 Agent CR `spec.runtime.image`

## 扩展：加新 agent

1. `src/agent_runtime/agent/` 下新建 `xxx_agent.py`，继承 `Agent`（基类） + `ToolCallingExecutor`（在 `_langchain_executor.py`）
2. 设 `name: ClassVar[str] = "xxx"`
3. 实现 `run(messages)` / `stream(messages)`（参考 `cluster_agent.py`）
4. `agent/__init__.py` 改 `AgentName` 联合类型加一项，重新导出类
5. `server.py` 的 `lifespan` 里 `app.state.agents` 加一行
6. `langchain_tools.py` 加 `build_xxx_tools()` 函数
7. rebuild 镜像 + 改 `agent_runtime.writeEnabled` 之类的 spec 字段（如果需要）走通

## 排错

| 现象 | 可能原因 | 排查 |
|---|---|---|
| `LLM_API_KEY is not set` | Secret 没注入 / `envFrom` 拼错 | `kubectl describe deploy <agent-deploy>` 看 envFrom |
| `/ready` 一直 503 | agent 构造失败（LLM 不可达 / 工具 import 失败） | `kubectl logs <pod>` 看 startup_error 字段 |
| 工具调用 `forbidden` | SA 没绑对应 ClusterRole | `kubectl get clusterrolebinding | grep agent-runtime` |
| webui 自动路由总是选 read | `/classify` LLM 调用失败，回退到 read（safe default） | `kubectl logs <pod>` 看 "classify failed" |
| chat 后 `answer` 字段是空 | langchain `AgentExecutor` 异常退出 | `kubectl logs <pod>` 看 "agent invocation failed" |
| `get_pod_logs` 返回空 | Pod 还没起 / `tail_lines` 太大被截断（上限 `POD_LOG_MAX_TAIL_LINES`） | `kubectl describe pod <pod>` 看 Events |
| 会话不持久化 | PVC 没挂 / `RUNTIME_HISTORY_DIR` 没设 | `kubectl describe pod` 看 volumeMounts |
| 写工具 401 | 镜像里 `ops_agent.py` 没装好 / 旧版本 | 看 pod 里 `python -c "import agent_runtime.agent.ops_agent"` |
| Operator 滚动重启后历史丢 | PVC 没用 `ReadWriteOnce` 持久模式 | `kubectl get pvc <agent>-history` 看 status |

## 项目结构

```
runtime/
├── pyproject.toml              # uv / hatchling 项目定义
├── uv.lock
├── Dockerfile                  # multi-stage with uv cache mounts
├── README.md                   # 本文件
├── src/agent_runtime/
│   ├── __init__.py
│   ├── main.py                 # uvicorn 入口
│   ├── server.py               # FastAPI app + 所有端点
│   ├── llm.py                  # ChatOpenAI 工厂
│   ├── langchain_tools.py      # 11 个 StructuredTool 包装
│   ├── history.py              # PVC-backed HistoryStore
│   ├── logging_config.py
│   ├── agent/                  # Agent 抽象 + 两个实现
│   │   ├── __init__.py         # AgentName Literal
│   │   ├── base.py             # Agent ABC
│   │   ├── _langchain_executor.py  # 共享 langchain 调用逻辑
│   │   ├── cluster_agent.py    # 8 只读工具
│   │   └── ops_agent.py        # 4 读 + 3 写
│   ├── tools/                  # 11 个 Tool 实现
│   │   ├── base.py             # Tool ABC
│   │   ├── kube.py             # get_api(cls) 工厂
│   │   ├── _k8s_call.py        # try / log / reraise
│   │   ├── constants.py        # POD_LOG_MAX_TAIL_LINES 等
│   │   ├── pod_tool.py
│   │   ├── pod_describe_tool.py
│   │   ├── pod_log_tool.py
│   │   ├── deployment_tool.py
│   │   ├── deployment_status_tool.py
│   │   ├── event_tool.py
│   │   ├── node_tool.py
│   │   ├── restart_pod_tool.py
│   │   ├── restart_deployment_tool.py
│   │   └── scale_deployment_tool.py
│   └── webui/                  # 内嵌单页 chat 前端
│       └── index.html
└── tests/                      # pytest 测试
```

## 许可

Apache-2.0
