"""Tests for tool construction + argument clamping."""

from __future__ import annotations

from agent_runtime.tools.constants import POD_LOG_DEFAULT_TAIL_LINES, POD_LOG_MAX_TAIL_LINES
from agent_runtime.tools.pod_log_tool import PodLogTool
from agent_runtime.tools.scale_deployment_tool import ScaleDeploymentTool


def test_pod_log_tool_clamps_tail_lines_above_max():
    tool = PodLogTool(name="p", namespace="ns", tail_lines=POD_LOG_MAX_TAIL_LINES + 5000)
    assert tool._tail_lines == POD_LOG_MAX_TAIL_LINES  # type: ignore[attr-defined]


def test_pod_log_tool_clamps_tail_lines_below_one():
    tool = PodLogTool(name="p", namespace="ns", tail_lines=0)
    assert tool._tail_lines == 1  # type: ignore[attr-defined]


def test_pod_log_tool_default_tail_lines():
    tool = PodLogTool(name="p", namespace="ns")
    assert tool._tail_lines == POD_LOG_DEFAULT_TAIL_LINES  # type: ignore[attr-defined]


def test_scale_deployment_rejects_negative_replicas():
    tool = ScaleDeploymentTool(name="d", namespace="ns", replicas=-1)
    try:
        tool.execute()
    except ValueError as exc:
        assert "replicas" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for negative replicas")


def test_scale_deployment_accepts_zero_replicas():
    # Validate only — actual API call is skipped because we never invoke
    # execute() (no cluster). Just confirms the value is stored.
    tool = ScaleDeploymentTool(name="d", namespace="ns", replicas=0)
    assert tool._replicas == 0  # type: ignore[attr-defined]