"""Pod listing tool backed by the Kubernetes Python client."""
from __future__ import annotations

import logging
from typing import Any, ClassVar

from kubernetes import client

from agent_runtime.tools._k8s_call import call_k8s
from agent_runtime.tools.base import Tool
from agent_runtime.tools.kube import get_api

logger = logging.getLogger(__name__)


class PodTool(Tool):
    """List Pods in a single namespace or across the whole cluster."""

    name: ClassVar[str] = "list_pods"

    def __init__(self, namespace: str = "") -> None:
        self._namespace = namespace.strip()

    def execute(self) -> list[dict[str, Any]]:
        api = get_api(client.CoreV1Api)

        if self._namespace:
            response = call_k8s(
                operation="listing pods in namespace",
                extra={"namespace": self._namespace},
                func=lambda: api.list_namespaced_pod(namespace=self._namespace, watch=False),
            )
        else:
            response = call_k8s(
                operation="listing pods in all namespaces",
                extra=None,
                func=lambda: api.list_pod_for_all_namespaces(watch=False),
            )

        result: list[dict[str, Any]] = []
        for pod in response.items:
            containers = [c.name for c in (pod.spec.containers or [])]
            result.append(
                {
                    "name": pod.metadata.name,
                    "namespace": pod.metadata.namespace,
                    "phase": pod.status.phase,
                    "node": pod.spec.node_name,
                    "pod_ip": pod.status.pod_ip,
                    "containers": containers,
                }
            )
        logger.info("collected pods", extra={"count": len(result)})
        return result