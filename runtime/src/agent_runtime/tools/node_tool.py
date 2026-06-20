"""Cluster node tools: list all nodes, or describe a single node."""
from __future__ import annotations

import logging
from typing import Any, ClassVar

from kubernetes import client

from agent_runtime.tools._k8s_call import call_k8s
from agent_runtime.tools.base import Tool
from agent_runtime.tools.kube import get_api

logger = logging.getLogger(__name__)


class NodeListTool(Tool):
    """List all Nodes in the cluster with their ready/pressure status."""

    name: ClassVar[str] = "list_nodes"

    def execute(self) -> list[dict[str, Any]]:
        api = get_api(client.CoreV1Api)
        nodes = call_k8s(
            operation="listing all nodes",
            extra=None,
            func=lambda: api.list_node().items,
        )

        result: list[dict[str, Any]] = []
        for node in nodes:
            conditions = {c.type: c.status for c in (node.status.conditions or [])}
            result.append(
                {
                    "name": node.metadata.name,
                    "ready": conditions.get("Ready") == "True",
                    "memory_pressure": conditions.get("MemoryPressure") == "True",
                    "disk_pressure": conditions.get("DiskPressure") == "True",
                    "pid_pressure": conditions.get("PIDPressure") == "True",
                    "unschedulable": bool(node.spec.unschedulable),
                    "roles": list((node.metadata.labels or {}).get("node-role.kubernetes.io", "").split(",")),
                }
            )
        return result


class NodeStatusTool(Tool):
    """Describe a single Node: allocatable resources, conditions, taints."""

    name: ClassVar[str] = "describe_node"

    def __init__(self, name: str) -> None:
        self._name = name

    def execute(self) -> dict[str, Any]:
        api = get_api(client.CoreV1Api)
        extra = {"node_name": self._name}
        node = call_k8s(
            operation="describing node",
            extra=extra,
            func=lambda: api.read_node(name=self._name),
        )

        conditions = [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (node.status.conditions or [])
        ]
        taints = [{"key": t.key, "value": t.value, "effect": t.effect} for t in (node.spec.taints or [])]

        return {
            "name": node.metadata.name,
            "conditions": conditions,
            "unschedulable": bool(node.spec.unschedulable),
            "taints": taints,
            "allocatable": dict(node.status.allocatable or {}) if node.status.allocatable else {},
            "capacity": dict(node.status.capacity or {}) if node.status.capacity else {},
        }
