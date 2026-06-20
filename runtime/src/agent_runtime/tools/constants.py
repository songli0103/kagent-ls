"""Module-level constants shared by all tool implementations.

Centralised so the same value isn't redefined in multiple places (e.g.
pod-log tail cap appears in both `pod_log_tool.py` and the args_schema
description in `langchain_tools.py`).
"""
from __future__ import annotations

# Maximum number of log lines the `get_pod_logs` tool will return per
# call. Hard cap protects the runtime from accidentally pulling megabytes
# of logs into the LLM context window.
POD_LOG_MAX_TAIL_LINES: int = 5000

# Default number of log lines if the caller doesn't specify a count.
POD_LOG_DEFAULT_TAIL_LINES: int = 100