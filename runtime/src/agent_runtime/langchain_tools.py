"""Wraps the concrete Tool classes as langchain StructuredTool instances.

Each tool gets a name, a docstring description, and a pydantic args_schema
that the LLM sees when deciding which tool to call. The function bodies
are thin shims that call the underlying tool and serialise the result.

Two builders are exposed:
- `build_tools()` returns the 8 read-only tools for `ClusterAgent`.
- `build_ops_tools()` returns the 7-tool ops subset (4 read + 3 write) for
  `OpsAgent`. The two overlap intentionally so the LLM can identify a
  resource via the read tools before invoking a destructive write tool.
"""
from __future__ import annotations

import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agent_runtime.tools.constants import POD_LOG_DEFAULT_TAIL_LINES, POD_LOG_MAX_TAIL_LINES
from agent_runtime.tools.deployment_status_tool import DeploymentStatusTool
from agent_runtime.tools.deployment_tool import DeploymentTool
from agent_runtime.tools.event_tool import EventTool
from agent_runtime.tools.node_tool import NodeListTool, NodeStatusTool
from agent_runtime.tools.pod_describe_tool import PodDescribeTool
from agent_runtime.tools.pod_log_tool import PodLogTool
from agent_runtime.tools.pod_tool import PodTool
from agent_runtime.tools.restart_deployment_tool import RestartDeploymentTool
from agent_runtime.tools.restart_pod_tool import RestartPodTool
from agent_runtime.tools.scale_deployment_tool import ScaleDeploymentTool


# ---------------------------------------------------------------------------
# Argument schemas
# ---------------------------------------------------------------------------


class _NsArg(BaseModel):
    namespace: str = Field(
        default="",
        description="Kubernetes namespace to scope the query. Empty string means all namespaces.",
    )


class _PodArgs(BaseModel):
    name: str = Field(description="Exact name of the Pod.")
    namespace: str = Field(description="Namespace the Pod lives in.")


class _PodLogArgs(BaseModel):
    name: str = Field(description="Exact name of the Pod.")
    namespace: str = Field(description="Namespace the Pod lives in.")
    tail_lines: int = Field(
        default=POD_LOG_DEFAULT_TAIL_LINES,
        description=(
            f"How many recent log lines to return. Bounded 1..{POD_LOG_MAX_TAIL_LINES}."
        ),
    )


class _DeploymentArgs(BaseModel):
    name: str = Field(description="Exact name of the Deployment.")
    namespace: str = Field(description="Namespace the Deployment lives in.")


class _ScaleDeploymentArgs(BaseModel):
    name: str = Field(description="Exact name of the Deployment.")
    namespace: str = Field(description="Namespace the Deployment lives in.")
    replicas: int = Field(
        description="Target replica count. Must be >= 0; 0 is allowed but considered destructive.",
    )


class _NodeArgs(BaseModel):
    name: str = Field(description="Exact name of the Node.")


class _NoArgs(BaseModel):
    """Empty args schema for tools that take no parameters.

    langchain's pydantic subset helper requires a real model with
    ``model_fields``; passing ``BaseModel`` directly raises
    ``KeyError('data')`` during ``bind_tools``."""


# ---------------------------------------------------------------------------
# Tool implementations (thin shims)
# ---------------------------------------------------------------------------


def _list_pods(namespace: str = "") -> str:
    """List Pods. namespace empty => all namespaces. Returns JSON array."""
    return json.dumps(PodTool(namespace=namespace).execute(), ensure_ascii=False, default=str)


def _list_deployments(namespace: str = "") -> str:
    """List Deployments. namespace empty => all namespaces. Returns JSON array."""
    return json.dumps(DeploymentTool(namespace=namespace).execute(), ensure_ascii=False, default=str)


def _list_events(namespace: str = "") -> str:
    """List Events. namespace empty => all namespaces. Returns JSON array."""
    return json.dumps(EventTool(namespace=namespace).execute(), ensure_ascii=False, default=str)


def _describe_pod(name: str, namespace: str) -> str:
    """Get a single Pod's full status plus the events that mention it.
    Use this when asked 'what is wrong with pod X' or 'why is pod X
    crashing' — the events carry the reason."""
    return json.dumps(PodDescribeTool(name=name, namespace=namespace).execute(), ensure_ascii=False, default=str)


def _get_pod_logs(name: str, namespace: str, tail_lines: int = POD_LOG_DEFAULT_TAIL_LINES) -> str:
    """Read the most recent log lines of a Pod container. Use this AFTER
    describe_pod has identified a misbehaving pod, to see what it printed."""
    return PodLogTool(name=name, namespace=namespace, tail_lines=tail_lines).execute()


def _get_deployment_status(name: str, namespace: str) -> str:
    """Get replica counts (replicas/ready/available) and conditions of a
    single Deployment. Use this when asked 'is deploy X healthy' or
    'how many pods does X have'."""
    return json.dumps(
        DeploymentStatusTool(name=name, namespace=namespace).execute(),
        ensure_ascii=False,
        default=str,
    )


def _list_nodes() -> str:
    """List all Nodes in the cluster with Ready / MemoryPressure / DiskPressure.
    Use this when asked 'which nodes are up' or 'is there a node problem'."""
    return json.dumps(NodeListTool().execute(), ensure_ascii=False, default=str)


def _describe_node(name: str) -> str:
    """Describe a single Node: conditions, allocatable resources, taints."""
    return json.dumps(NodeStatusTool(name=name).execute(), ensure_ascii=False, default=str)


def _restart_pod(name: str, namespace: str) -> str:
    """Delete a Pod so its controller recreates it (rolling restart for
    Deployment/ReplicaSet-owned pods). Use AFTER describe_pod has
    confirmed the exact name and namespace."""
    return json.dumps(
        RestartPodTool(name=name, namespace=namespace).execute(),
        ensure_ascii=False,
        default=str,
    )


def _restart_deployment(name: str, namespace: str) -> str:
    """Trigger a rolling restart of every Pod in a Deployment by stamping
    the kubectl restartedAt annotation. Use AFTER get_deployment_status
    has confirmed the exact name and namespace."""
    return json.dumps(
        RestartDeploymentTool(name=name, namespace=namespace).execute(),
        ensure_ascii=False,
        default=str,
    )


def _scale_deployment(name: str, namespace: str, replicas: int) -> str:
    """Scale a Deployment to `replicas` replicas via the /scale subresource.
    Use AFTER get_deployment_status has confirmed the exact name and
    namespace. Scaling to 0 stops the workload — confirm intent first."""
    return json.dumps(
        ScaleDeploymentTool(name=name, namespace=namespace, replicas=replicas).execute(),
        ensure_ascii=False,
        default=str,
    )


# ---------------------------------------------------------------------------
# StructuredTool assembly
# ---------------------------------------------------------------------------


def build_tools() -> list[StructuredTool]:
    """Return the list of all StructuredTool instances the agent can call."""
    return [
        StructuredTool.from_function(
            func=_list_pods,
            name="list_pods",
            description=(
                "列出指定 namespace 的 Pod。namespace 为空字符串时列出全集群。"
                "返回每个 Pod 的 name / namespace / phase / node / pod_ip / containers。"
                "适合回答「集群里有哪些 pod」「default ns 里跑了啥」之类的问题。"
            ),
            args_schema=_NsArg,
        ),
        StructuredTool.from_function(
            func=_list_deployments,
            name="list_deployments",
            description=(
                "列出指定 namespace 的 Deployment。namespace 为空字符串时列出全集群。"
                "返回 name / namespace / replicas / ready_replicas / available_replicas / updated_replicas。"
            ),
            args_schema=_NsArg,
        ),
        StructuredTool.from_function(
            func=_list_events,
            name="list_events",
            description=(
                "列出指定 namespace 的 Event。namespace 为空字符串时列出全集群。"
                "返回 type / reason / message / involved_object。"
                "用于看最近发生过什么、谁被调度了、谁重启了。"
            ),
            args_schema=_NsArg,
        ),
        StructuredTool.from_function(
            func=_describe_pod,
            name="describe_pod",
            description=(
                "获取单个 Pod 的完整 spec + status + 关联的 Event。"
                "回答「pod xxx 怎么了」「xxx 为什么不健康」时优先调这个。"
            ),
            args_schema=_PodArgs,
        ),
        StructuredTool.from_function(
            func=_get_pod_logs,
            name="get_pod_logs",
            description=(
                f"读 Pod 最近 tail_lines 行日志。describe_pod 之后想看具体输出时用。"
                f"tail_lines 默认 {POD_LOG_DEFAULT_TAIL_LINES}，上限 {POD_LOG_MAX_TAIL_LINES}。"
            ),
            args_schema=_PodLogArgs,
        ),
        StructuredTool.from_function(
            func=_get_deployment_status,
            name="get_deployment_status",
            description=(
                "获取单个 Deployment 的副本状态（replicas / ready / available / unavailable）"
                "和 conditions。回答「deploy xxx 健康吗」「xxx 几个 ready」用这个。"
            ),
            args_schema=_DeploymentArgs,
        ),
        StructuredTool.from_function(
            func=_list_nodes,
            name="list_nodes",
            description=(
                "列出集群所有 Node，每个返回 ready / memory_pressure / disk_pressure /"
                "unschedulable / roles。回答「哪些节点 down」「节点压力大吗」用这个。"
            ),
            args_schema=_NoArgs,
        ),
        StructuredTool.from_function(
            func=_describe_node,
            name="describe_node",
            description="获取单个 Node 的 conditions / allocatable / capacity / taints。",
            args_schema=_NodeArgs,
        ),
    ]


def build_ops_tools() -> list[StructuredTool]:
    """Return the focused toolset for `OpsAgent` — 4 read + 3 write tools.

    The read tools are a subset of `build_tools()` and are needed so the
    LLM can identify the exact resource (name + namespace) before invoking
    a destructive write tool. Omitted from the ops set: `list_events`,
    `list_nodes`, `describe_node`, `get_pod_logs` — not directly relevant
    to restart/scale workflows; keeping the surface lean reduces
    accidental scope.
    """
    return [
        StructuredTool.from_function(
            func=_list_pods,
            name="list_pods",
            description=(
                "列出指定 namespace 的 Pod，辅助确认要操作的 Pod 名字。"
                "namespace 为空字符串时列出全集群。"
            ),
            args_schema=_NsArg,
        ),
        StructuredTool.from_function(
            func=_list_deployments,
            name="list_deployments",
            description="列出指定 namespace 的 Deployment，辅助确认要操作的 Deployment 名字。",
            args_schema=_NsArg,
        ),
        StructuredTool.from_function(
            func=_describe_pod,
            name="describe_pod",
            description=(
                "在重启 Pod 前先 describe 一下确认 name 和 namespace 都正确，"
                "避免误删别的 Pod。"
            ),
            args_schema=_PodArgs,
        ),
        StructuredTool.from_function(
            func=_get_deployment_status,
            name="get_deployment_status",
            description=(
                "重启 / 扩缩 Deployment 前先确认副本状态，"
                "得到正确的 name 和 namespace。"
            ),
            args_schema=_DeploymentArgs,
        ),
        StructuredTool.from_function(
            func=_restart_pod,
            name="restart_pod",
            description=(
                "删除一个 Pod 让 controller 重建它（对 Deployment/ReplicaSet 所属的 Pod "
                "会触发该副本的滚动重启，对裸 Pod 直接终止）。"
                "调用前必须先 describe_pod 确认 name 和 namespace。"
            ),
            args_schema=_PodArgs,
        ),
        StructuredTool.from_function(
            func=_restart_deployment,
            name="restart_deployment",
            description=(
                "给 Deployment 的 pod template 打上 restartedAt annotation，"
                "触发所有副本的滚动重启。调用前必须先 get_deployment_status 确认。"
            ),
            args_schema=_DeploymentArgs,
        ),
        StructuredTool.from_function(
            func=_scale_deployment,
            name="scale_deployment",
            description=(
                "把 Deployment 扩缩到指定 replicas 数（通过 /scale 子资源）。"
                "调用前必须先 get_deployment_status 确认；replicas=0 会停服，"
                "需要在回复中明确提醒。"
            ),
            args_schema=_ScaleDeploymentArgs,
        ),
    ]