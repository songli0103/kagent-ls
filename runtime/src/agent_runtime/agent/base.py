"""Abstract base class for agents executed by the runtime.

An Agent owns a toolset and an LLM-backed executor. The runtime asks
`name` for logging/registration and calls `run()` with a chat history
to get a final answer (and any tool calls the agent made).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Iterator


class Agent(ABC):
    """Abstract base class for agents.

    Subclasses must:
      - set a unique `name` class attribute
      - implement `run(messages)` returning the final answer + tool trace
      - implement `stream(messages)` yielding SSE-shaped events

    `messages` is an OpenAI-style list of {role, content} dicts.
    """

    # Stable identifier of the agent, used for logging and registration.
    name: ClassVar[str] = ""

    @abstractmethod
    def run(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Run the agent on the given chat history and return the final
        answer along with the tool calls/results trace.

        Returns a dict shaped like:
          {
            "answer": str,
            "tool_calls": [{"name": str, "args": Any}, ...],
            "tool_results": [{"name": str, "result": Any}, ...],
          }
        """

    @abstractmethod
    def stream(self, messages: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        """Yield SSE-shaped events suitable for streaming over HTTP.

        Emits dicts of shape:
          {"type": "tool_call",   "name": str, "args": Any}
          {"type": "tool_result", "name": str, "result": Any}
          {"type": "done",        "answer": str}
        """
