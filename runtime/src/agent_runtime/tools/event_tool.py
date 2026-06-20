"""Event listing tool backed by the Kubernetes Python client."""
from __future__ import annotations

import logging
from typing import Any, ClassVar

from kubernetes import client

from agent_runtime.tools._k8s_call import call_k8s
from agent_runtime.tools.base import Tool
from agent_runtime.tools.kube import get_api

logger = logging.getLogger(__name__)


class EventTool(Tool):
    """List Events in a single namespace or across the whole cluster."""

    name: ClassVar[str] = "list_events"

    def __init__(self, namespace: str = "") -> None:
        self._namespace = namespace.strip()

    def execute(self) -> list[dict[str, Any]]:
        api = get_api(client.CoreV1Api)

        if self._namespace:
            response = call_k8s(
                operation="listing events in namespace",
                extra={"namespace": self._namespace},
                func=lambda: api.list_namespaced_event(namespace=self._namespace, watch=False),
            )
        else:
            response = call_k8s(
                operation="listing events in all namespaces",
                extra=None,
                func=lambda: api.list_event_for_all_namespaces(watch=False),
            )

        result: list[dict[str, Any]] = []
        for event in response.items:
            involved = event.involved_object
            result.append(
                {
                    "name": event.metadata.name,
                    "namespace": event.metadata.namespace,
                    "type": event.type,
                    "reason": event.reason,
                    "message": event.message,
                    "count": event.count,
                    "involved_object": {
                        "kind": involved.kind if involved else None,
                        "name": involved.name if involved else None,
                        "namespace": involved.namespace if involved else None,
                    },
                    "first_timestamp": _format_time(event.first_timestamp),
                    "last_timestamp": _format_time(event.last_timestamp),
                }
            )
        logger.info("collected events", extra={"count": len(result)})
        return result


def _format_time(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)