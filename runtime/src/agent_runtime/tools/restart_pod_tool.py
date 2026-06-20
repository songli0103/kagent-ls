"""Restart a single Pod by deleting it (the controller will recreate it).

For Pods owned by a ReplicaSet/Deployment this triggers a rollout of that one
replica; for bare Pods it just terminates the workload. Use only after the
caller has confirmed the intent and `describe_pod` has identified the target.
"""
from __future__ import annotations

from typing import Any, ClassVar

from kubernetes import client

from agent_runtime.tools._k8s_call import call_k8s
from agent_runtime.tools.base import Tool
from agent_runtime.tools.kube import get_api


class RestartPodTool(Tool):
    """Delete a Pod so its controller recreates it (rolling restart)."""

    name: ClassVar[str] = "restart_pod"

    def __init__(self, name: str, namespace: str) -> None:
        self._name = name
        self._namespace = namespace

    def execute(self) -> dict[str, Any]:
        api = get_api(client.CoreV1Api)
        call_k8s(
            operation="deleting pod to trigger restart",
            extra={"pod_name": self._name, "namespace": self._namespace},
            func=lambda: api.delete_namespaced_pod(name=self._name, namespace=self._namespace),
        )

        return {
            "deleted": self._name,
            "namespace": self._namespace,
            "will_be_recreated": True,
        }