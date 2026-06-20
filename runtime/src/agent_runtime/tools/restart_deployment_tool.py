"""Restart a Deployment by patching a restart annotation onto its pod template.

This is the standard `kubectl rollout restart` mechanism: adding a new value
to `spec.template.metadata.annotations["kubectl.kubernetes.io/restartedAt"]`
changes the pod template hash, causing the ReplicaSet controller to roll out
new pods while keeping the Deployment spec otherwise unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar

from kubernetes import client

from agent_runtime.tools._k8s_call import call_k8s
from agent_runtime.tools.base import Tool
from agent_runtime.tools.kube import get_api

# Annotation key/value pair kubectl uses for rollout restart. Kept as a
# module-level constant so the keys agree with what kubectl itself sets
# (matches `kubectl rollout restart deployment/foo`).
ROLLOUT_RESTART_ANNOTATION_KEY = "kubectl.kubernetes.io/restartedAt"


class RestartDeploymentTool(Tool):
    """Trigger a rolling restart of a Deployment by patching the pod template."""

    name: ClassVar[str] = "restart_deployment"

    def __init__(self, name: str, namespace: str) -> None:
        self._name = name
        self._namespace = namespace

    def execute(self) -> dict[str, Any]:
        api = get_api(client.AppsV1Api)
        restarted_at = datetime.now(timezone.utc).isoformat()
        body = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            ROLLOUT_RESTART_ANNOTATION_KEY: restarted_at,
                        }
                    }
                }
            }
        }
        call_k8s(
            operation="patching deployment to trigger rollout",
            extra={
                "deployment_name": self._name,
                "namespace": self._namespace,
                "restarted_at": restarted_at,
            },
            func=lambda: api.patch_namespaced_deployment(
                name=self._name,
                namespace=self._namespace,
                body=body,
            ),
        )

        return {
            "restarted": self._name,
            "namespace": self._namespace,
            "rollout_annotation": restarted_at,
        }