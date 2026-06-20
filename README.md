# kagent-ls — Kubernetes-native AI Agent chat service

一个 Kubernetes 原生的 AI Agent 聊天服务框架：每个 Agent CR 物化成一个**长运行的 Deployment + Service**，runtime 容器暴露 OpenAI 兼容的 HTTP API。Agent 通过工具调用查询 / 描述 / 操作 K8s 资源（Pod、Deployment、Event、Node 等）。

每个 Agent 同时挂两个子 agent：

| Agent | 名字 | 能力 |
|---|---|---|
| `ClusterAgent` | `cluster` | 只读，8 个查询工具 |
| `OpsAgent` | `ops` | 读写，4 个查询 + 3 个写操作（重启 Pod/Deployment、扩缩容） |

请求体里的 `agent: cluster\|ops` 字段决定走哪个 agent（默认 `cluster`）。

## 架构

```
┌──────────────────────────────┐
│  kubectl apply -f agent.yaml │
└──────────────┬───────────────┘
               ▼
       Agent CR (CRD)
       agent.demo.io/v1alpha1
               │
               │ watch (controller-runtime)
               ▼
   Operator (kagent-ls-controller-manager)
   ┌────────────────────────────────────────────┐
   │ Reconcile:                                  │
   │  1. SA + read CRB（始终）                   │
   │  2. write CRB（仅 spec.writeEnabled=true）  │
   │  3. PVC（仅 spec.history.enabled=true）     │
   │  4. Deployment + Service                   │
   │  5. Watch Secret → resourceVersion 变化时   │
   │     在 pod template 打 annotation 触发滚动  │
   └────────────┬───────────────────────────────┘
                ▼
       Deployment (replicas: 1)
       Service (ClusterIP :8080)
       ┌────────────────────────────────┐
       │ Runtime container               │
       │  FastAPI /chat/completions      │
       │  ChatOpenAI (MiniMax M3)        │
       │  Langchain 0.3                  │
       │    ClusterAgent (8 读)          │
       │    OpsAgent (4 读 + 3 写)       │
       │  /tools, /sessions/{id}         │
       │  webui/ (内嵌单页 chat)         │
       └────────────────────────────────┘
                ▲
                │ HTTP
                │
       ┌────────┴─────────┐
       │  kubectl / curl   │
       │  /chat/completions│
       │  /tools           │
       │  /sessions/{id}   │
       │  webui/           │
       └───────────────────┘
```

## 特性

- **长连接 chat service**：每个 Agent CR 是常驻 Deployment（不是一次性 Job）
- **双 agent 模式**：`cluster`（只读 8 工具）和 `ops`（读写 7 工具），按请求 `agent` 字段分发
- **OpenAI 兼容 API**：`POST /chat/completions`，body 形如 `{model, agent, messages}`
- **凭证自动轮换**：改 Secret（`kubectl edit secret`）operator 监听到 `resourceVersion` 变化，在 pod template 打 annotation 触发滚动重启
- **可选 chat history**：PVC 挂到 `/var/lib/agent-runtime/history`，每个 session 一个 JSON 文件，跨 pod 重启保留
- **per-Agent RBAC 隔离**：每个 Agent 独立的 SA，只绑它需要的角色
- **写权限按需开启**：`spec.writeEnabled=true` 才创建 write ClusterRoleBinding；不开启时整个 runtime 完全没有写权限
- **WebUI**：runtime 容器内置一个单页 chat 前端

## 仓库结构

```
kagent-ls/
├── operator/                          # Kubebuilder v3 Go 项目
│   ├── api/v1alpha1/
│   │   ├── agent_types.go             # AgentSpec / AgentStatus 定义 + printcolumn
│   │   ├── groupversion_info.go
│   │   └── zz_generated.deepcopy.go
│   ├── internal/
│   │   ├── controller/
│   │   │   └── agent_controller.go     # Reconcile 状态机
│   │   └── services/
│   │       ├── rbac_service.go        # SA + ClusterRoleBinding
│   │       ├── storage_service.go     # PVC
│   │       └── (deployment / status 逻辑在 controller 内)
│   ├── cmd/main.go
│   ├── config/                        # kustomize 基础 manifest
│   └── Makefile
├── runtime/                           # Python Agent 运行时（uv）
│   ├── src/agent_runtime/
│   │   ├── main.py                    # 入口
│   │   ├── server.py                  # FastAPI app + /chat/completions
│   │   ├── llm.py                     # ChatOpenAI 配置
│   │   ├── langchain_tools.py         # 11 个 StructuredTool 定义
│   │   ├── history.py                 # PVC-backed 会话持久化
│   │   ├── logging_config.py
│   │   ├── agent/                     # Agent 抽象 + 两个实现
│   │   │   ├── base.py
│   │   │   ├── _langchain_executor.py # 共享 langchain 0.3 调用逻辑
│   │   │   ├── cluster_agent.py       # name="cluster"
│   │   │   └── ops_agent.py           # name="ops"
│   │   ├── tools/                     # 11 个 Tool 实现
│   │   │   ├── base.py
│   │   │   ├── kube.py                # get_api(cls) 工厂
│   │   │   ├── _k8s_call.py           # try / log / reraise 包装
│   │   │   ├── pod_tool.py
│   │   │   ├── pod_describe_tool.py
│   │   │   ├── pod_log_tool.py
│   │   │   ├── deployment_tool.py
│   │   │   ├── deployment_status_tool.py
│   │   │   ├── event_tool.py
│   │   │   ├── node_tool.py
│   │   │   ├── restart_pod_tool.py
│   │   │   ├── restart_deployment_tool.py
│   │   │   └── scale_deployment_tool.py
│   │   └── webui/                     # 内嵌单页 HTML chat 前端
│   ├── pyproject.toml
│   ├── uv.lock
│   └── Dockerfile
├── deploy/                            # 终端用户部署清单
│   ├── crd.yaml
│   ├── operator.yaml                  # operator Deployment + RBAC
│   ├── runtime-rbac.yaml              # runtime SA + read ClusterRole(Binding)
│   ├── agent-runtime-write-rbac.yaml  # write ClusterRole(Binding)（按需 apply）
│   └── sample-agent.yaml
├── webui/                             # 独立静态 webui（可选，runtime 已内嵌）
├── README.md
└── LICENSE
```

## CRD

### Spec

```go
type AgentSpec struct {
    Runtime           AgentRuntimeSpec          // 必填：{Image: "..."}
    Task              AgentTaskSpec             // 可选，chat 模式下运行时忽略
    Config            AgentConfigSpec           // 可选，{Namespace: ""}
    CredentialsSecret *string                   // 默认 "minimax-credentials"
    WriteEnabled      bool                      // 默认 false（只读）
    History           *HistoryPersistenceSpec   // 可选 PVC 持久化
}

type HistoryPersistenceSpec struct {
    Enabled          bool    // 默认 false
    Size             string  // 默认 "1Gi"
    StorageClassName *string // 默认集群 default SC
}
```

> `spec.task` 字段保留是为了兼容 v0.1 的 Agent CR。chat service 模式下完全由 HTTP 请求驱动，`spec.task` 不被读取。

### Status

```go
type AgentStatus struct {
    Phase              AgentPhase         // Pending/Running/Ready/Succeeded/Failed/Unknown
    ObservedGeneration int64              // 上次调和时的 spec generation
    Conditions         []AgentCondition   // 状态时间线
    DeploymentRef      AgentDeploymentRef // 当前 Deployment 名
    ServiceRef         AgentServiceRef    // 当前 Service 名
    ReadyReplicas      int32              // 就绪副本数
    StartedAt          *metav1.Time       // 首次 Ready 的时间
}
```

`kubectl get agent` 默认打印：`Phase / Generation / Ready / Deployment / Service / Age`。

## 双 agent 模型

| Agent | name | 工具 | RBAC |
|---|---|---|---|
| `ClusterAgent` | `cluster` | `list_pods` / `list_deployments` / `list_events` / `describe_pod` / `get_pod_logs` / `get_deployment_status` / `list_nodes` / `describe_node` | `agent-runtime`（读） |
| `OpsAgent` | `ops` | 上面 4 个 + `restart_pod` / `restart_deployment` / `scale_deployment` | `agent-runtime`（读） + `agent-runtime-write`（写） |

请求体：

```json
{
  "model": "MiniMax-M3",
  "agent": "cluster",          // 或 "ops"
  "messages": [
    {"role": "user", "content": "default ns 里的 pod 都有哪些"}
  ]
}
```

`/tools?agent=cluster|ops` 列出对应 agent 的工具描述，方便 prompt 调试。

## HTTP API

| Method | Path | 说明 |
|---|---|---|
| POST | `/chat/completions` | OpenAI 兼容 chat endpoint，支持 streaming |
| GET | `/tools?agent=cluster\|ops` | 列出 agent 的工具描述（默认 `cluster`） |
| GET | `/sessions/{session_id}` | 拉历史会话（503 当 history 未启用） |
| GET | `/health`, `/ready` | 探针 |
| GET | `/` 或 `/webui/` | 内嵌单页 chat 前端 |

## 端到端流程

1. **apply Agent CR**
   ```bash
   kubectl apply -f deploy/sample-agent.yaml
   ```
2. **Operator Reconcile**，按顺序：
   - SA `<name>-agent-runtime`
   - ClusterRoleBinding → `agent-runtime`（读，始终）
   - 如果 `spec.writeEnabled=true`：ClusterRoleBinding → `agent-runtime-write`
   - 如果 `spec.history.enabled=true`：PVC `<name>-history`
   - Deployment（image 来自 `spec.runtime.image`，envFrom 来自 `spec.credentialsSecret`）
   - Service（ClusterIP，targetPort 8080）
3. **等 Ready**：`kubectl get agent -w`，`Status.Phase=Ready` 时可服务
4. **端口转发**：
   ```bash
   kubectl port-forward svc/<name>-runtime 8080:8080
   ```
5. **chat**：
   ```bash
   curl -X POST http://localhost:8080/chat/completions \
     -H 'Content-Type: application/json' \
     -d '{"model":"MiniMax-M3","agent":"cluster","messages":[{"role":"user","content":"list default ns pods"}]}'
   ```
   或浏览器开 `http://localhost:8080/`。
6. **凭证轮换**：`kubectl edit secret minimax-credentials -n default`，operator 监听到 `resourceVersion` 变化自动滚动重启。
7. **清理**：`kubectl delete agent <name>` 触发 OwnerReference 级联回收 SA / RBAC / PVC / Deployment / Service。

## RBAC 矩阵

### runtime 用的 read 角色（`agent-runtime` ClusterRole）

| API group | resources | verbs |
|---|---|---|
| `""` | pods, pods/log, events, nodes, namespaces, services | get, list, watch |
| `apps` | deployments, replicasets, statefulsets, daemonsets | get, list, watch |
| `batch` | jobs, cronjobs | get, list, watch |

### runtime 用的 write 角色（`agent-runtime-write` ClusterRole，仅 write 时绑定）

| API group | resources | verbs |
|---|---|---|
| `""` | pods | get, list, watch, delete |
| `apps` | deployments | get, list, watch, patch |
| `apps` | deployments/scale | get, list, watch, patch, update |

> `deployments/scale` 是独立子资源，必须单独声明；`deployments` 的规则不覆盖它。

### operator 自己的角色（`kagent-ls-manager-role` ClusterRole）

| API group | resources | verbs |
|---|---|---|
| `agent.demo.io` | agents, agents/status | get, list, watch, create, update, patch, delete |
| `""` | pods, pods/log, events | get, list, watch, create, patch |
| `apps` | deployments, services | get, list, watch, create, update, patch, delete |
| `rbac.authorization.k8s.io` | serviceaccounts, clusterrolebindings | get, list, watch, create, update, patch, delete |
| `core` | persistentvolumeclaims | get, list, watch, create, update, patch, delete |
| `coordination.k8s.io` | leases | （leader election） |

## 构建与部署

### 0. 前提

- Go ≥ 1.22（operator 编译用）
- uv ≥ 0.4（runtime lockfile 用）
- Docker + 可推送的镜像 registry（默认阿里云 ACR 个人版）
- kubectl ≥ 1.28
- 一个 K8s 集群（kind / minikube / Docker Desktop k8s / 真实集群）
- 申请好的 LLM 凭证（API key、base URL、model name）

### 1. 构建并推送镜像

下面的命令把 `<your-registry>` 替换成你的镜像 registry（阿里云 ACR / Docker Hub / 自建都行），把 `<your-namespace>` 替换成你在该 registry 下的命名空间，`<tag>` 替换成你想要打的 tag（跟 `deploy/operator.yaml` 和 `deploy/sample-agent.yaml` 里引用的 tag 一致即可）。

```bash
REG=<your-registry>
NS=<your-namespace>
TAG=<tag>    # 比如 v0.3，跟 deploy/ 下的镜像引用保持一致

# Operator
cd operator
make docker-build IMG=${REG}/${NS}/kagent-ls-operator:${TAG}
docker push     ${REG}/${NS}/kagent-ls-operator:${TAG}

# Runtime
cd ../runtime
docker build -t ${REG}/${NS}/cluster-agent:${TAG} .
docker push     ${REG}/${NS}/cluster-agent:${TAG}
```

### 2. 部署 Operator

```bash
# 修改 deploy/operator.yaml 里 image 字段为你刚 push 的镜像 tag
kubectl apply -f deploy/crd.yaml
kubectl apply -f deploy/operator.yaml
```

### 3. 创建 LLM 凭证 Secret

```bash
kubectl create namespace <your-namespace>    # 如果 Agent CR 不在 default ns

kubectl -n <your-namespace> create secret generic minimax-credentials \
  --from-literal=LLM_API_KEY=<your-key> \
  --from-literal=LLM_BASE_URL=https://api.minimaxi.com/v1 \
  --from-literal=LLM_MODEL=MiniMax-M3
```

> Secret 名默认是 `minimax-credentials`，可在 Agent CR 的 `spec.credentialsSecret` 覆盖。

### 4. 部署 runtime 用的 RBAC

```bash
# 改 deploy/runtime-rbac.yaml 里的 namespace 字段到你 Agent CR 所在的 ns
kubectl apply -f deploy/runtime-rbac.yaml

# 仅当 Agent CR 的 spec.writeEnabled=true 时才需要：
kubectl apply -f deploy/agent-runtime-write-rbac.yaml
```

### 5. 提交 Agent CR

`deploy/sample-agent.yaml`：

```yaml
apiVersion: agent.demo.io/v1alpha1
kind: Agent
metadata:
  name: k8s-assistant
  namespace: default
spec:
  runtime:
    image: <your-registry>/<your-namespace>/cluster-agent:<tag>
  config:
    namespace: ""                # 空 = 全集群；填 "default" 则限定
  # writeEnabled: true           # 取消注释以开启写权限
  # history:
  #   enabled: true
  #   size: 1Gi
```

```bash
kubectl apply -f deploy/sample-agent.yaml
```

### 6. 验证

```bash
# 等到 Ready
kubectl get agent k8s-assistant -n default -w

# 端口转发
kubectl -n default port-forward svc/k8s-assistant-runtime 8080:8080

# 试一次只读 chat
curl -X POST http://localhost:8080/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"MiniMax-M3","agent":"cluster","messages":[{"role":"user","content":"list default ns pods"}]}'

# 试一次 ops（仅 writeEnabled=true 时）
curl -X POST http://localhost:8080/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"MiniMax-M3","agent":"ops","messages":[{"role":"user","content":"restart deployment X in default"}]}'
```

成功标志：

```
NAME            PHASE    GENERATION   READY   DEPLOYMENT                SERVICE                 AGE
k8s-assistant   Ready    1            1       k8s-assistant-deployment   k8s-assistant-service   30s
```

## 排错

| 现象 | 可能原因 | 排查 |
|---|---|---|
| Agent 一直 `Pending` | Operator 镜像拉不到 / SA 缺 `apps/deployments: create` | `kubectl logs -n kagent-ls-system deploy/kagent-ls-controller-manager` |
| Pod `ErrImagePull` | 镜像地址错 / 私有仓库没配 imagePullSecret | `kubectl describe pod <pod>` 看 Events |
| Pod `CrashLoopBackOff` | runtime 启动缺模块（`ModuleNotFoundError`） | 本地 `uv sync` 验证 lockfile |
| Deployment 一直 `0/1` | 缺 LLM 凭证或 env 拼错 | `kubectl describe deploy <name>` + `kubectl logs` |
| `agent=ops` 报 `forbidden` | 没 apply `agent-runtime-write-rbac.yaml` 或 Agent CR 没 `writeEnabled: true` | apply write rbac 后 `kubectl delete agent` 重 apply |
| `agent=ops` 报 `forbidden: cannot create ...` 之外 | SA 没在当前 namespace 绑到 write 角色 | `kubectl get clusterrolebinding | grep agent-runtime-write` |
| 改 Secret 后 Deployment 不滚动 | `spec.credentialsSecret` 名拼错 / Secret 在其他 ns | operator 日志里应该看到 "credentials-version" annotation 写入 |
| `__RESULT_BEGIN__` 错误 | （旧 v0.1 残留）chat 模式不再用这个协议 | 忽略；chat 模式下结果通过 HTTP 响应返回 |
| controller 报 `forbidden: ... configmaps` | leader election 需要 `leases` 权限（已配）；如还报 configmap 大概率 SA 漂移 | `kubectl get clusterrolebinding` 检查 SA 引用 |
| `make run` 报连不上集群 | `KUBECONFIG` / context 默认 ns 不存在 | `kubectl cluster-info`；`kubectl config set-context --current --namespace=default` |
| 写工具调用后集群没变化 | `agent=ops` 没在请求体里 / 工具在 `cluster` agent 里不可见 | `curl /tools?agent=ops` 确认 |

## 扩展：新增工具

1. **Runtime**：在 `runtime/src/agent_runtime/tools/` 下新建 `xxx_tool.py`，继承 `Tool` 基类；导入到 `tools/__init__.py`
2. **langchain StructuredTool 包装**：在 `runtime/src/agent_runtime/langchain_tools.py` 加 shim 函数和 `StructuredTool.from_function` 条目
3. **决定加入哪个 agent**：
   - 读工具 → 加进 `build_tools()`（cluster）和 `build_ops_tools()`（ops，按需）
   - 写工具 → 只加进 `build_ops_tools()`（ops）
4. **RBAC**：在 `deploy/agent-runtime-write-rbac.yaml` 加对应资源 + verbs（写工具）
5. **镜像**：`docker build` runtime → push → 改 Agent CR 的 `spec.runtime.image` → 触发滚动重启

## 排错：CR 不会自动更新 schema

`make manifests` 重生 CRD YAML 后，已经存在的 CR 不会立刻看到新 schema。需要：

- 改完 CRD 后 `kubectl apply -f deploy/crd.yaml` 升级 CRD definition
- 已经创建的 CR 用 `kubectl delete` 再 `kubectl apply`，或 `kubectl edit` 触发 schema 校验

## 路线图

| 版本 | 状态 | 内容 |
|---|---|---|
| v0.1 | 已废弃但 CR 向后兼容 | 一次性 Job，stdout `__RESULT_BEGIN__` 协议 |
| v0.2 | 历史 | 长连接 chat service + `ClusterAgent`（8 只读工具） |
| v0.3 | 当前 | 双 agent（`cluster` + `ops`），写权限按需，可选 chat history |
| v0.4 | 计划 | admission webhook（参数校验）、多 LLM provider 适配 |

## 许可

Apache-2.0
