"""Scale a Deployment to a target replica count via the scale subresource.

Uses `patch_namespaced_deployment_scale` so the Deployment's pod template is
not touched — only `.spec.replicas` is updated, triggering the ReplicaSet
controller to converge.
"""
from __future__ import annotations

from typing import Any, ClassVar

from kubernetes import client

from agent_runtime.tools._k8s_call import call_k8s
from agent_runtime.tools.base import Tool
from agent_runtime.tools.kube import get_api


class ScaleDeploymentTool(Tool):
    """Set the replica count of a Deployment."""

    name: ClassVar[str] = "scale_deployment"

    def __init__(self, name: str, namespace: str, replicas: int) -> None:
        self._name = name
        self._namespace = namespace
        self._replicas = replicas

    def execute(self) -> dict[str, Any]:
        if self._replicas < 0:
            raise ValueError(f"replicas must be >= 0, got {self._replicas}")

        api = get_api(client.AppsV1Api)
        body = {"spec": {"replicas": self._replicas}}
        call_k8s(
            operation="scaling deployment",
            extra={
                "deployment_name": self._name,
                "namespace": self._namespace,
                "replicas": self._replicas,
            },
            func=lambda: api.patch_namespaced_deployment_scale(
                name=self._name,
                namespace=self._namespace,
                body=body,
            ),
        )

        return {
            "scaled": self._name,
            "namespace": self._namespace,
            "replicas": self._replicas,
        }