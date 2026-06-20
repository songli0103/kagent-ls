"""Read recent logs from a specific Pod."""
from __future__ import annotations

import logging
from typing import ClassVar

from kubernetes import client

from agent_runtime.tools._k8s_call import call_k8s
from agent_runtime.tools.base import Tool
from agent_runtime.tools.constants import POD_LOG_DEFAULT_TAIL_LINES, POD_LOG_MAX_TAIL_LINES
from agent_runtime.tools.kube import get_api

logger = logging.getLogger(__name__)


class PodLogTool(Tool):
    """Read the tail of a Pod's log via the Kubernetes API.

    Used by the LLM when troubleshooting a misbehaving pod. `tail_lines` is
    clamped to `[1, POD_LOG_MAX_TAIL_LINES]` so a runaway request can't
    pull megabytes of logs into the LLM context window.
    """

    name: ClassVar[str] = "get_pod_logs"

    def __init__(
        self,
        name: str,
        namespace: str,
        tail_lines: int = POD_LOG_DEFAULT_TAIL_LINES,
    ) -> None:
        self._name = name
        self._namespace = namespace
        self._tail_lines = max(1, min(tail_lines, POD_LOG_MAX_TAIL_LINES))

    def execute(self) -> str:
        api = get_api(client.CoreV1Api)
        # NB: log extra uses `pod_name`, not `name` — `name` is reserved by
        # Python's LogRecord and would either be silently shadowed by the
        # logger name or raise KeyError on write.
        extra = {
            "pod_name": self._name,
            "namespace": self._namespace,
            "tail_lines": self._tail_lines,
        }
        return call_k8s(
            operation="reading pod logs",
            extra=extra,
            func=lambda: api.read_namespaced_pod_log(
                name=self._name,
                namespace=self._namespace,
                tail_lines=self._tail_lines,
                timestamps=True,
            ),
        )