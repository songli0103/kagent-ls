"""Status of a single Deployment (replica counts + conditions)."""
from __future__ import annotations

import logging
from typing import Any, ClassVar

from kubernetes import client

from agent_runtime.tools._k8s_call import call_k8s
from agent_runtime.tools.base import Tool
from agent_runtime.tools.kube import get_api

logger = logging.getLogger(__name__)


class DeploymentStatusTool(Tool):
    """Get a Deployment's replica counts and current conditions.

    The LLM uses this to answer "is deployment X healthy" or
    "how many replicas are ready".
    """

    name: ClassVar[str] = "get_deployment_status"

    def __init__(self, name: str, namespace: str) -> None:
        self._name = name
        self._namespace = namespace

    def execute(self) -> dict[str, Any]:
        api = get_api(client.AppsV1Api)
        extra = {"deployment_name": self._name, "namespace": self._namespace}

        def _read() -> Any:
            return api.read_namespaced_deployment(name=self._name, namespace=self._namespace)

        deploy = call_k8s(operation="getting deployment status", extra=extra, func=_read)
        status = deploy.status
        return {
            "name": deploy.metadata.name,
            "namespace": deploy.metadata.namespace,
            "replicas": status.replicas,
            "ready_replicas": status.ready_replicas,
            "available_replicas": status.available_replicas,
            "updated_replicas": status.updated_replicas,
            "unavailable_replicas": status.unavailable_replicas,
            "conditions": [
                {
                    "type": c.type,
                    "status": c.status,
                    "reason": c.reason,
                    "message": c.message,
                }
                for c in (status.conditions or [])
            ],
        }
