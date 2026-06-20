"""Shared plumbing for the tool-calling agents.

`ClusterAgent` and `OpsAgent` differ only in their system prompt and tool
list; the rest of the executor setup, `run()` invocation, and SSE `stream()`
shape is identical. `ToolCallingExecutor` is a small mixin that owns that
shared shape so each concrete agent can stay a few lines.

The message-shape helpers (`split_messages`, `unpack_step`,
`flatten_intermediate`) live here too — they were originally defined as
module-private helpers in `cluster_agent.py`, but `ops_agent.py` would
otherwise have to reach across that boundary to reuse them.
"""
from __future__ import annotations

from typing import Any, ClassVar, Iterator

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import BaseTool

# Maximum number of characters from a tool observation that we forward to
# the API consumer (or to the next model turn). Truncating protects the
# SSE stream + response body from accidental megabyte outputs (e.g. a
# `get_pod_logs` call where someone passed tail_lines=5000).
TOOL_OBSERVATION_MAX_CHARS: int = 2000

# Whether the AgentExecutor should emit verbose langchain logs. False in
# production so the runtime's structured JSON logs aren't drowned by
# langchain debug output.
AGENT_EXECUTOR_VERBOSE: bool = False


def split_messages(messages: list[dict[str, str]]) -> tuple[list[Any], str]:
    """Convert OpenAI-style messages into (chat_history, last_user_text).

    - The first 'system' message is folded into the agent's system prompt
      by the caller (we don't add it again here).
    - All messages except the last 'user' message become chat_history.
    - The last 'user' message becomes the input.
    """
    if not messages:
        raise ValueError("messages must not be empty")

    # Filter out system messages; they're already handled in the prompt.
    non_system = [m for m in messages if m.get("role") != "system"]

    if not non_system:
        raise ValueError("at least one user message is required")

    last = non_system[-1]
    if last.get("role") != "user":
        raise ValueError("the last message must be a user message")

    history: list[Any] = []
    for m in non_system[:-1]:
        role = m.get("role")
        content = m.get("content", "")
        if role == "user":
            history.append(HumanMessage(content=content))
        elif role == "assistant":
            history.append(AIMessage(content=content))

    return history, last.get("content", "")


def unpack_step(step: Any) -> tuple[Any, Any]:
    """langchain 0.3 has TWO different step shapes:
    - `AgentExecutor.invoke()` → intermediate_steps is List[Tuple[AgentAction, observation]]
    - `AgentExecutor.stream()` → each `step` is an `AgentStep` pydantic model
    with `.action` / `.observation` attributes.
    Detect which one we got and return (action, observation)."""
    if hasattr(step, "action") and hasattr(step, "observation"):
        return step.action, step.observation
    # Tuple path: AgentStep was a Tuple[AgentAction, Any] subclass in older
    # langchain. The first element is the action, the second is the observation.
    return step[0], step[1]


def flatten_intermediate(steps: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert AgentExecutor's intermediate_steps into a pair of parallel
    lists (tool_calls, tool_results) for the API response."""
    calls: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for step in steps:
        action, observation = unpack_step(step)
        calls.append({"name": action.tool, "args": action.tool_input})
        results.append({"name": action.tool, "result": str(observation)[:TOOL_OBSERVATION_MAX_CHARS]})
    return calls, results


class ToolCallingExecutor:
    """Mixin: builds an `AgentExecutor` from (llm, system_prompt, tools) and
    implements `run()` / `stream()` against it.

    Concrete agents subclass this (alongside the abstract `Agent` base) and
    provide:
      - `system_prompt: ClassVar[str]` — the agent's system prompt
      - `_default_tools() -> list[BaseTool]` — the StructuredTool list

    Optionally pass `tools=...` to `__init__` to override (used in tests).
    """

    system_prompt: ClassVar[str] = ""

    def __init__(self, llm: BaseChatModel, tools: list[BaseTool] | None = None) -> None:
        self._custom_tools = list(tools) if tools is not None else None
        self._tools: list[BaseTool] = (
            self._custom_tools if self._custom_tools is not None else self._default_tools()
        )
        self._executor = self._make_executor(llm)

    def _default_tools(self) -> list[BaseTool]:
        """Return this agent's default StructuredTool list. Override in
        subclasses. The default is an empty toolset."""
        return []

    def _make_executor(self, llm: BaseChatModel) -> AgentExecutor:
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self.system_prompt),
                ("placeholder", "{chat_history}"),
                ("human", "{input}"),
                ("placeholder", "{agent_scratchpad}"),
            ]
        )
        agent = create_tool_calling_agent(llm, self._tools, prompt)
        return AgentExecutor(
            agent=agent,
            tools=self._tools,
            verbose=AGENT_EXECUTOR_VERBOSE,
            return_intermediate_steps=True,
            handle_parsing_errors=True,
        )

    @property
    def tools(self) -> list[BaseTool]:
        return list(self._tools)

    # Agent.run / Agent.stream implementations — shape is identical across
    # all tool-calling agents (the executor is already wired up by __init__).

    def run(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        chat_history, last_user = split_messages(messages)
        result = self._executor.invoke({"input": last_user, "chat_history": chat_history})
        tool_calls, tool_results = flatten_intermediate(result.get("intermediate_steps", []))
        return {
            "answer": result.get("output", ""),
            "tool_calls": tool_calls,
            "tool_results": tool_results,
        }

    def stream(self, messages: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        chat_history, last_user = split_messages(messages)
        for event in self._executor.stream({"input": last_user, "chat_history": chat_history}):
            if "steps" in event:
                for step in event["steps"]:
                    action, observation = unpack_step(step)
                    yield {"type": "tool_call", "name": action.tool, "args": action.tool_input}
                    yield {
                        "type": "tool_result",
                        "name": action.tool,
                        "result": str(observation)[:TOOL_OBSERVATION_MAX_CHARS],
                    }
            elif "output" in event:
                yield {"type": "done", "answer": event["output"]}