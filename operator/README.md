# kagent-ls Operator

Kubebuilder v3 写的 Kubernetes Operator，把 `Agent` CR 调和成一个长运行的 chat-service Deployment（外加 ServiceAccount / ClusterRoleBinding / 可选 PVC）。

跟 [项目根 README](../README.md) 和 [runtime README](../runtime/README.md) 配合阅读——那里讲 CRD 用法和 runtime 容器本身，这里讲 **Operator 是怎么调和这些资源的**。

## 它做什么

把一个声明式 Agent CR（`agent.demo.io/v1alpha1`）物化成：

| 派生资源 | 是否始终创建 | 命名规则 | OwnerRef |
|---|---|---|---|
| `ServiceAccount` | ✓ | `<agent.name>-agent-runtime` | Agent CR |
| `ClusterRoleBinding`（read） | ✓ | `<agent.name>-agent-runtime-read` | **无**（cluster-scoped） |
| `ClusterRoleBinding`（write） | 仅 `spec.writeEnabled=true` | `<agent.name>-agent-runtime-write` | **无**（cluster-scoped） |
| `PersistentVolumeClaim` | 仅 `spec.history.enabled=true` | `<agent.name>-history` | Agent CR |
| `Deployment` | ✓ | `<agent.name>-runtime` | Agent CR |
| `Service`（ClusterIP） | ✓ | `<agent.name>-runtime` | Agent CR |

> 两条 `ClusterRoleBinding` 是 cluster-scoped 的（因为底层 `ClusterRole` 是 cluster-scoped 的），**不带 OwnerReference**——删 Agent CR 不会自动 GC 绑定。Operator 自己负责在 `spec.writeEnabled` 翻 false 时清掉 write binding（见下文"Reconcile 状态机"）。
>
> 两个 `ClusterRole`（`agent-runtime` / `agent-runtime-write`）也**不归 Operator 管**——它们在 `config/runtime/` 下、由部署者管理，因为平台团队往往想把角色定义集中管起来。

## 架构

```
┌────────────────────────────────────────────────────────────┐
│ AgentReconciler (internal/controller/agent_controller.go)  │
│                                                            │
│  Reconcile(ctx, req):                                      │
│    1. Get Agent (NotFound → return nil)                    │
│    2. rbacSvc.ReconcileRBAC   (SA + read CRB ± write CRB)  │
│    3. storageSvc.ReconcilePVC (± history PVC)              │
│    4. (gen changed) → DeleteDeployment + ResetForRerun     │
│    5. (no DeploymentRef) → startDeployment                 │
│    6. (DeploymentRef) → observeDeployment                  │
└──────────┬─────────────┬──────────────┬─────────────┬──────┘
           │             │              │             │
           ▼             ▼              ▼             ▼
     ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
     │ RBAC     │  │ Storage  │  │ Deploy-  │  │ Status   │
     │ Service  │  │ Service  │  │ ment     │  │ Service  │
     │          │  │          │  │ Service  │  │          │
     │ SA + CRB │  │ PVC      │  │ Deploy+  │  │ Mark     │
     │          │  │          │  │ Service  │  │ Ready/   │
     │          │  │          │  │          │  │ Running/ │
     │          │  │          │  │          │  │ Failed   │
     └──────────┘  └──────────┘  └──────────┘  └──────────┘
                                                         ▲
                                                         │
            ┌────────────────────────────────────────────┘
            │ 所有 status 写入都走 retry.RetryOnConflict
            │ 防并发 reconcile 互踩
            │
   SetupWithManager:
     For(&Agent{})
     Watches(&Secret{}, secretToAgents, secretDataChangedPredicate{})
```

## CRD：Agent

```go
type AgentSpec struct {
    Runtime           AgentRuntimeSpec          // 必填 {Image: "..."}
    Task              AgentTaskSpec             // 可选，chat 模式忽略
    Config            AgentConfigSpec           // 可选 {Namespace: ""}
    CredentialsSecret *string                   // 默认 "minimax-credentials"
    WriteEnabled      bool                      // 默认 false（只读）
    History           *HistoryPersistenceSpec   // 可选 {Enabled, Size, StorageClassName}
}

type AgentStatus struct {
    Phase             AgentPhase         // Pending/Running/Ready/Succeeded/Failed/Unknown
    ObservedGeneration int64
    Conditions        []AgentCondition
    DeploymentRef     AgentDeploymentRef  // {Name: "<agent>-runtime"}
    ServiceRef        AgentServiceRef     // {Name: "<agent>-runtime"}
    ReadyReplicas     int32
    StartedAt         *metav1.Time
}
```

完整定义见 [`api/v1alpha1/agent_types.go`](api/v1alpha1/agent_types.go)。

Printcolumns（`kubectl get agent` 默认列）：

```
NAME            PHASE    GENERATION   READY   DEPLOYMENT              SERVICE               AGE
k8s-assistant   Ready    1            1       k8s-assistant-runtime   k8s-assistant-runtime 30s
```

## Reconcile 状态机

`agent_controller.go::Reconcile` 是按"先轻后重"组织的：

```
Reconcile(req)
  │
  ├─ Get Agent
  │   └─ NotFound / DeletionTimestamp != nil → return nil
  │
  ├─ rbacSvc.ReconcileRBAC(agent)             ← 始终
  │   ├─ create SA
  │   ├─ create CRB → agent-runtime
  │   └─ if spec.writeEnabled: create CRB → agent-runtime-write
  │      else:               delete CRB → agent-runtime-write
  │
  ├─ storageSvc.ReconcilePVC(agent)           ← 始终
  │   └─ if spec.history.enabled: ensurePVC
  │      else:                  deletePVC
  │
  ├─ Status.DeploymentRef != "" && ObservedGen != Generation
  │   └─ DeleteDeployment + ResetForRerun
  │      (spec 改了 → 重建 Deployment)   → Requeue=true
  │
  ├─ Status.DeploymentRef == ""
  │   └─ startDeployment:
  │       ├─ DepSvc.CreateDeployment
  │       └─ StatusSvc.MarkRunning(deploymentName)   ← 用 name 字符串，别 GetDeployment
  │      → RequeueAfter = 3s
  │
  └─ Status.DeploymentRef != ""
      └─ observeDeployment:
          ├─ GetDeployment (NotFound → ResetForRerun + Requeue)
          ├─ rollIfCredentialsChanged
          │   └─ if secret.RV != pod-template.annotation: 打 annotation → 触发滚动
          ├─ IsReady?
          │   ├─ yes → MarkReady (idempotent) → RequeueAfter = 10s
          │   └─ no  →                          RequeueAfter = 5s
```

四个 requeue 周期集中在 `agent_controller.go` 顶部常量：

| 常量 | 值 | 用途 |
|---|---|---|
| `requeueAfterDeploymentCreate` | 3s | 刚创建完 Deployment，等 apiserver 一拍 |
| `requeueAfterCredentialRoll` | 3s | 刚因为凭证变化打了 annotation |
| `requeueAfterObservedReady` | 10s | 稳态：Deployment 一直 Ready 时的轮询间隔 |
| `requeueAfterObservedNotReady` | 5s | 还没 Ready 时的轮询间隔 |

## 凭证轮换机制

Operator 不直接存任何 LLM 凭据——它把 Secret 当成"凭证版本号源"：

```
1. 用户 kubectl edit secret minimax-credentials
2. apiserver 更新 Secret，resourceVersion 自增
3. secretDataChangedPredicate 过滤：只对 Data/StringData 真改的事件响应
4. secretToAgents mapper 找出同一 ns 下所有引用此 Secret 的 Agent CR
5. 对每个 Agent 触发 Reconcile
6. rollIfCredentialsChanged:
     if pod-template.annotations["agent.demo.io/credentials-version"] != secret.resourceVersion:
         patch annotation ← secret.resourceVersion
7. Deployment controller 看到 pod-template 变了 → 滚动重启
8. 新 Pod envFrom 解析到新 Secret 数据 → runtime 拿到新 LLM_API_KEY
```

整条链路**不需要 rebuild runtime 镜像**。

## 关键设计

### 1. OwnerReference 边界

| 资源 | OwnerRef = Agent CR？ | 原因 |
|---|---|---|
| ServiceAccount | ✓ | 跟着 Agent 走 |
| Deployment | ✓ | 跟着 Agent 走 |
| Service | ✓ | 跟着 Agent 走 |
| PVC | ✓ | 跟着 Agent 走 |
| ClusterRoleBinding | **✗** | cluster-scoped，apiserver 拒绝跨 ns owner；GC 靠 Operator 自己清理 |
| ClusterRole | **不归 Operator 管** | 平台团队管理 |

### 2. 并发安全

所有 status 写入（`MarkReady` / `MarkRunning` / `MarkFailed` / `ResetForRerun`）都走 `services/retry.go::RetryOnConflict` 包裹的读-改-写循环。原因：`Secret` watch 触发的 Reconcile 跟 Agent 主 Reconcile 会并发跑（参见 `agent_controller.go:rollIfCredentialsChanged` 内的注释），不带 retry 就 `Conflict`。

### 3. `startDeployment` 不调用 `GetDeployment`

`MarkRunning` 拿 deployment name 是用 `deployment.DeploymentName(agent)` **直接构造**字符串，不走 apiserver lookup。原因是此时 `Status.DeploymentRef.Name == ""`，如果先 Get 再传 ref，会 lookup 一个空 ref 然后 404（参见 `agent_controller.go:startDeployment` 注释）。

### 4. `writeEnabled` 翻 false 自动清 write CRB

`rbacSvc.ReconcileRBAC` 是 idempotent 的：每次 Reconcile 都检查 `spec.writeEnabled` 状态，按需 create 或 delete write binding。**不需要重启 Operator、不需要手工清理**，spec 一改下一次 reconcile 就处理掉。

### 5. Secret watch 的事件过滤

`predicates.go::secretDataChangedPredicate` 只对 `Secret.Data` / `Secret.StringData` 真改的 event 响应，apiserver 自己的 metadata tick（labels、resourceVersion 内部维护）不会触发 reconcile——否则每条 watch 都会走一遍全 Agent List，性能浪费。

## 项目结构

```
operator/
├── api/v1alpha1/
│   ├── agent_types.go             # AgentSpec / AgentStatus + printcolumn marker
│   ├── groupversion_info.go       # SchemeBuilder 注册
│   ├── doc.go                     # +kubebuilder:rbac: 注释
│   └── zz_generated.deepcopy.go   # ❌ 绝不手改（controller-gen 生成）
├── cmd/
│   └── main.go                    # 入口：scheme 注册 + Manager + Reconciler 装配
├── internal/
│   ├── controller/
│   │   ├── agent_controller.go    # Reconcile 状态机（5 步）
│   │   └── predicates.go          # Secret watch 事件过滤
│   ├── services/
│   │   ├── rbac_service.go        # SA + read/write CRB
│   │   ├── storage_service.go     # history PVC
│   │   ├── deployment_service.go  # 调 deployment.Builder + 创建/删除/查询
│   │   ├── status_service.go      # MarkReady/Running/Failed + RetryOnConflict
│   │   └── retry.go               # RetryOnConflict helper（+ 单元测试）
│   └── deployment/
│       ├── builder.go             # Deployment + Service + Container + history mount
│       └── labels.go              # 标准 label / annotation key 常量
├── config/
│   ├── crd/bases/                 # ❌ 不手改（controller-gen 生成）
│   ├── rbac/                      # operator 自己的 RBAC（manager 跑起来用）
│   ├── manager/                   # operator Deployment 模板
│   ├── runtime/                   # agent-runtime + agent-runtime-write ClusterRole
│   │                             # （**平台团队管理**，不是 operator 生成）
│   ├── default/                   # kustomize 入口
│   └── samples/                   # sample Agent CR
├── Dockerfile                     # multi-stage
├── Makefile                       # build / test / manifests / generate / install / deploy
├── PROJECT                        # kubebuilder 元数据
├── go.mod / go.sum
└── README.md                      # 本文件
```

## 本地开发

### 前置

- Go ≥ 1.22
- `make`
- 一个 K8s 集群（kind / minikube / Docker Desktop k8s）+ `KUBECONFIG`

### 编译

```bash
cd operator
make build         # 输出 bin/manager
```

### 跑 controller（连本地集群）

```bash
make install       # 装 CRD（kustomize build config/crd | kubectl apply -f -）
make run           # 前台跑 controller，watch 集群里所有 Agent
```

要停：`Ctrl+C`。

### 跑测试

```bash
make test-unit     # 单元测试（不需要 envtest，< 1s）
make test          # 完整测试（含 envtest，会自动下载 kube-apiserver / etcd）
```

### 重生 CRD / RBAC

改 `api/v1alpha1/agent_types.go` 的 marker（`+kubebuilder:validation:...` / `+kubebuilder:printcolumn:...`）后：

```bash
make manifests     # 重生 config/crd/bases/*.yaml
make generate      # 重生 zz_generated.deepcopy.go
```

改 `internal/controller/agent_controller.go` 顶部的 `+kubebuilder:rbac:` marker 后：

```bash
make manifests     # 重生 config/rbac/role.yaml
```

### 重生后**重要**：已存在 CR 的 schema 不会自动更新

```bash
kubectl apply -f config/crd/bases/agent.demo.io_agents.yaml   # 升级 CRD definition
# 已有 CR 不会立刻看到新 schema，但 kubectl apply 同名文件不会报错
# 想强制重读：kubectl delete + apply
```

## 构建并部署 Operator

```bash
cd operator

# 1. 编译
make build

# 2. 打镜像
make docker-build IMG=<your-registry>/<your-namespace>/kagent-ls-operator:<tag>
docker push        <your-registry>/<your-namespace>/kagent-ls-operator:<tag>

# 3. 改 config/manager/manager.yaml 里的 image 字段
# （或用 kustomize edit set image）

# 4. 部署
make deploy
```

或者用项目里手挑的 `deploy/operator.yaml`（`config/default/kustomization.yaml` 渲染后手改 image 的简化版）：

```bash
kubectl apply -f deploy/operator.yaml
```

## 扩展

### 加 AgentSpec 字段

1. 改 `api/v1alpha1/agent_types.go` 的 `AgentSpec` struct，加新字段 + marker（如 `// +optional` / `// +kubebuilder:validation:...`）
2. `make generate` 重生 `zz_generated.deepcopy.go`
3. `make manifests` 重生 `config/crd/bases/agent.demo.io_agents.yaml`
4. `kubectl apply -f config/crd/bases/agent.demo.io_agents.yaml` 升级 CRD
5. 在 `internal/deployment/builder.go::buildContainer` 或 `internal/controller/agent_controller.go::Reconcile` 里读新字段

### 加新的"派生资源"（比如 Ingress）

1. 在 `internal/services/` 下新建 `ingress_service.go`，仿照 `rbac_service.go` 写一个 `ReconcileIngress(ctx, agent) error`
2. 在 `cmd/main.go` 里 `ingressService := services.NewIngressService(...)`，注入到 `AgentReconciler`
3. `agent_controller.go::Reconcile` 里在 SA + RBAC 之后调一次
4. 加 `+kubebuilder:rbac:` marker（`networking.k8s.io/ingresses`）
5. `make manifests` 升级 RBAC YAML

### 加新的 Reconcile 触发源（类似 Secret watch）

1. 在 `agent_controller.go::SetupWithManager` 链上加 `Watches(&corev1.ConfigMap{}, handler.EnqueueRequestsFromMapFunc(r.cmToAgents), builder.WithPredicates(...))`
2. 写 `cmToAgents` mapper（仿 `secretToAgents`）
3. 写一个 predicate（仿 `secretDataChangedPredicate`），过滤掉 apiserver metadata tick

### 加 ClusterRole 角色

`config/runtime/agent-runtime-*.yaml` 里**手维护** ClusterRole 定义（operator 不会生成），用 `kubectl apply` 部署一次。

## 排错

| 现象 | 可能原因 | 排查 |
|---|---|---|
| `kubectl get agent` 报 `no matches for kind` | CRD 没装 | `make install` |
| Agent 一直 `Pending` | operator 镜像拉不到 / manager 自己 RBAC 缺 `agents` | `kubectl -n kagent-ls-system logs deploy/kagent-ls-controller-manager` |
| 派生资源创建不出来 | manager 缺对应 RBAC | 同上日志 + `kubectl auth can-i create deployments --as=system:serviceaccount:kagent-ls-system:kagent-ls-controller-manager` |
| `Conflict` 错误刷屏 | 并发 Reconcile 撞 status 写 | 检查 `services/status_service.go` 是否走了 `RetryOnConflict` |
| Deployment 一直 `0/1` | runtime 镜像拉不到 / Pod 启动失败 | `kubectl describe pod` 看 Events；`kubectl logs` 看 runtime 日志 |
| 改 Secret 后 Deployment 不滚动 | operator 没 watch 这个 Secret / Secret 在其他 ns | operator 日志应该看到 "credentials changed, rolling deployment" |
| `writeEnabled` 翻 false 但 write CRB 还在 | Operator 没在跑 / `rbac_service.ReconcileRBAC` 没被调到 | `kubectl logs` + `kubectl get clusterrolebindings | grep <agent>` |
| `history.enabled` 翻 false 但 PVC 还在 | 同上 | `kubectl get pvc -n <ns>` |
| 删 Agent CR 后 ClusterRoleBinding 残留 | **预期行为**，CRB 没有 OwnerRef | 手工删：`kubectl delete clusterrolebinding <agent>-agent-runtime-read` |
| `kubectl get agent` 列出来的 `Ready` 不对 | `ReadyReplicas` 还没从 `Status` 同步 | operator 还没 observe 到新一轮 Deployment status，等下一次 reconcile |
| `make manifests` 报 `controller-gen: not found` | `bin/controller-gen` 不存在 | 第一次跑会自动下到 `bin/`；也可以 `make controller-gen` 强制下 |
| `make test` 卡在 envtest | envtest-setup 没下载 | `make envtest` 预先下载 |

## 许可

Apache-2.0
