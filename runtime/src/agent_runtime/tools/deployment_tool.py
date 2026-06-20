"""Deployment listing tool backed by the Kubernetes Python client."""
from __future__ import annotations

import logging
from typing import Any, ClassVar

from kubernetes import client

from agent_runtime.tools._k8s_call import call_k8s
from agent_runtime.tools.base import Tool
from agent_runtime.tools.kube import get_api

logger = logging.getLogger(__name__)


class DeploymentTool(Tool):
    """List Deployments in a single namespace or across the whole cluster."""

    name: ClassVar[str] = "list_deployments"

    def __init__(self, namespace: str = "") -> None:
        self._namespace = namespace.strip()

    def execute(self) -> list[dict[str, Any]]:
        api = get_api(client.AppsV1Api)

        if self._namespace:
            response = call_k8s(
                operation="listing deployments in namespace",
                extra={"namespace": self._namespace},
                func=lambda: api.list_namespaced_deployment(namespace=self._namespace, watch=False),
            )
        else:
            response = call_k8s(
                operation="listing deployments in all namespaces",
                extra=None,
                func=lambda: api.list_deployment_for_all_namespaces(watch=False),
            )

        result: list[dict[str, Any]] = []
        for deploy in response.items:
            result.append(
                {
                    "name": deploy.metadata.name,
                    "namespace": deploy.metadata.namespace,
                    "replicas": deploy.spec.replicas,
                    "ready_replicas": deploy.status.ready_replicas,
                    "available_replicas": deploy.status.available_replicas,
                    "updated_replicas": deploy.status.updated_replicas,
                }
            )
        logger.info("collected deployments", extra={"count": len(result)})
        return result