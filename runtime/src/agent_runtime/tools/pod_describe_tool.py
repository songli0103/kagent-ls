"""Describe a single Pod: spec + status + recent events involving it."""
from __future__ import annotations

import logging
from typing import Any, ClassVar

from kubernetes import client

from agent_runtime.tools._k8s_call import call_k8s
from agent_runtime.tools.base import Tool
from agent_runtime.tools.kube import get_api

logger = logging.getLogger(__name__)


class PodDescribeTool(Tool):
    """Get a single Pod's full spec/status and any events that mention it.

    This is the primary tool the LLM should reach for when asked
    "what's wrong with pod X" — events carry the reason for restarts, OOM,
    image pull failures, etc.
    """

    name: ClassVar[str] = "describe_pod"

    def __init__(self, name: str, namespace: str) -> None:
        self._name = name
        self._namespace = namespace

    def execute(self) -> dict[str, Any]:
        api = get_api(client.CoreV1Api)
        extra = {"pod_name": self._name, "namespace": self._namespace}

        def _read() -> Any:
            return api.read_namespaced_pod(name=self._name, namespace=self._namespace)

        pod = call_k8s(operation="describing pod", extra=extra, func=_read)

        events = call_k8s(
            operation="listing events for pod describe",
            extra=extra,
            func=lambda: api.list_namespaced_event(namespace=self._namespace).items,
        )
        relevant = [
            {
                "type": e.type,
                "reason": e.reason,
                "message": e.message,
                "count": e.count,
                "first_timestamp": e.first_timestamp.isoformat() if e.first_timestamp else None,
                "last_timestamp": e.last_timestamp.isoformat() if e.last_timestamp else None,
            }
            for e in events
            if e.involved_object
            and e.involved_object.kind == "Pod"
            and e.involved_object.name == self._name
        ]

        container_statuses = []
        for cs in (pod.status.container_statuses or []):
            state_info: dict[str, Any] = {}
            if cs.state.running:
                state_info = {"running": {"started_at": str(cs.state.running.started_at)}}
            elif cs.state.waiting:
                state_info = {
                    "waiting": {
                        "reason": cs.state.waiting.reason,
                        "message": cs.state.waiting.message,
                    }
                }
            elif cs.state.terminated:
                state_info = {
                    "terminated": {
                        "reason": cs.state.terminated.reason,
                        "exit_code": cs.state.terminated.exit_code,
                        "message": cs.state.terminated.message,
                        "finished_at": str(cs.state.terminated.finished_at),
                    }
                }
            container_statuses.append(
                {
                    "name": cs.name,
                    "ready": cs.ready,
                    "restart_count": cs.restart_count,
                    "image": cs.image,
                    "state": state_info,
                }
            )

        return {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "phase": pod.status.phase,
            "node": pod.spec.node_name,
            "pod_ip": pod.status.pod_ip,
            "restart_policy": pod.spec.restart_policy,
            "containers": [c.name for c in (pod.spec.containers or [])],
            "container_statuses": container_statuses,
            "events": relevant[-10:],
        }
