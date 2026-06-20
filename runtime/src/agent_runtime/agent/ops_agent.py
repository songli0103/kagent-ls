"""OpsAgent — write-capable Kubernetes operator assistant.

Unlike `ClusterAgent` (read-only, 8 tools), OpsAgent has a focused toolset of
4 read tools + 3 write tools so the LLM can identify a target resource and
then restart / scale it. Selection of this agent is gated by the
`agent: "ops"` field on the chat request — the LLM is never exposed to the
write tools under the default `cluster` agent.

Safety rules baked into the system prompt:
  - always identify the exact resource first (describe_pod /
    get_deployment_status) before issuing a destructive call
  - confirm the resource name + namespace in the assistant's reply text
    before calling restart_pod / restart_deployment / scale_deployment
  - report what was done in the final answer (timestamp + name + namespace)
"""
from __future__ import annotations

from typing import ClassVar

from langchain_core.tools import BaseTool

from agent_runtime.agent._langchain_executor import ToolCallingExecutor
from agent_runtime.agent.base import Agent
from agent_runtime.langchain_tools import build_ops_tools

SYSTEM_PROMPT = """你是一个 Kubernetes 集群运维助手（OpsAgent），可以调用工具查询集群状态，并在用户明确授权后执行受控的写操作（重启 / 扩缩）。

可用能力：
- 读：list_pods / list_deployments / describe_pod / get_deployment_status
- 写：restart_pod / restart_deployment / scale_deployment

安全规则（必须遵守）：
1. 执行任何写操作前必须先用读工具确认目标的精确 name 和 namespace，避免误操作。
2. 在回复文本中先向用户说明即将操作的对象（name + namespace）和动作，再调用写工具。
3. 写操作完成后在最终回复里给出：操作时间戳、资源名称、namespace，以及成功的证据（replica 数变化 / rollout annotation 等）。
4. 如果用户的描述含糊（比如「把那个 pod 重启一下」），先 list_pods / describe_pod 让用户确认是哪个。
5. 不要批量重启多个 deployment；如果用户要求批量，先问一句确认。
6. scale 目标到 0（停服）属于破坏性操作，必须在回复里特别提醒。

回答风格：用中文，先给出诊断，再给出动作，最后给一句「下一步建议」。"""


class OpsAgent(ToolCallingExecutor, Agent):
    """Long-lived write-capable agent executor for K8s ops tasks."""

    name: ClassVar[str] = "ops"
    system_prompt: ClassVar[str] = SYSTEM_PROMPT

    def _default_tools(self) -> list[BaseTool]:
        return build_ops_tools()