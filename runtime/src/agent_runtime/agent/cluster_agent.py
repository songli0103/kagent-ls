"""ClusterAgent — wraps a langchain tool-calling agent that can answer
free-form questions about a Kubernetes cluster.

The agent has 8 tools available (list_pods, list_deployments, list_events,
describe_pod, get_pod_logs, get_deployment_status, list_nodes,
describe_node). It receives a list of messages (OpenAI-style chat
history) and returns the assistant's final answer along with the
tool calls it made and the tool results it got.
"""
from __future__ import annotations

from typing import ClassVar

from langchain_core.tools import BaseTool

from agent_runtime.agent._langchain_executor import ToolCallingExecutor
from agent_runtime.agent.base import Agent
from agent_runtime.langchain_tools import build_tools

SYSTEM_PROMPT = """你是一个 Kubernetes 集群排错助手，可以调用工具查询集群的真实状态。

使用规则：
- 用户问到任何具体的 Pod / Deployment / Node / Event 信息时，必须先调工具拿数据，不要凭记忆回答。
- 拿到数据后再用中文总结，重点说明异常、原因和可能的修复方向。
- 如果用户的描述含糊（比如"那个 pod"），先 list_pods 让用户确认是哪条。
- 排错思路推荐：describe_pod → 看 container state → get_pod_logs → 看具体输出。
- 涉及 deployment 健康时优先看 get_deployment_status。
- 节点异常（NotReady / 压力）时优先看 list_nodes。
- 回答末尾用一句"建议 / 下一步"收尾，方便用户继续追。"""


class ClusterAgent(ToolCallingExecutor, Agent):
    """A long-lived agent executor that can answer K8s questions via tools."""

    name: ClassVar[str] = "cluster"
    system_prompt: ClassVar[str] = SYSTEM_PROMPT

    def _default_tools(self) -> list[BaseTool]:
        return build_tools()
