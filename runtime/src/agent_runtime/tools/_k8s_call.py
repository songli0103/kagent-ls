"""Shared helper for K8s API calls made by the concrete Tool classes.

Every tool wraps a single K8s API call in the same try/except/log/reraise
pattern. Centralising it removes ~6 lines of boilerplate per tool and keeps
the log shape consistent across all tool failures.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

T = TypeVar("T")


def call_k8s(
    operation: str,
    extra: dict[str, Any] | None,
    func: Callable[[], T],
) -> T:
    """Execute `func` and translate K8s API failures into a single log line.

    On `ApiException` we log the operation name + the extras (resource
    names, namespaces, etc.) and re-raise so the LLM-facing wrapper can
    surface the error to the caller. Non-K8s exceptions are passed
    through untouched.

    Args:
        operation: short verb phrase (e.g. "list pods in namespace") used
            in the success + error log message.
        extra: structured fields merged into both the success and error
            log records (e.g. `{"namespace": "default"}`). Pass `None` for
            no extras.
        func: zero-arg callable that performs the actual API call.
    """
    log_extra = dict(extra or {})
    logger.info(operation, extra=log_extra)
    try:
        return func()
    except ApiException as exc:
        error_extra = {**log_extra, "reason": str(exc)}
        logger.error(f"{operation} failed", extra=error_extra)
        raise