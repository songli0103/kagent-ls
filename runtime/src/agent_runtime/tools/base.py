"""Tool abstraction shared by all concrete tool implementations.

A Tool is a unit of work invoked by an Agent. It exposes a stable `name`
(used for logging and the LLM-facing tool registry) and an `execute()`
method that returns JSON-serialisable data of any shape — a list of
records, a single record, or a raw string. The LLM-facing wrapper
(langchain_tools.py) is responsible for serialising the result to a
JSON string the model can read.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class Tool(ABC):
    """A unit of work invoked by an Agent.

    Subclasses must:
      - set a unique `name` class attribute
      - implement `execute()` returning a JSON-serialisable value
    """

    # Stable identifier used by the LLM-facing tool registry and logs.
    # Must be unique across all tools in the agent's toolset.
    name: ClassVar[str] = ""

    @abstractmethod
    def execute(self) -> Any:
        """Execute the tool and return a JSON-serialisable result.

        Concrete return shapes vary by tool:
          - list tools (list_pods, list_nodes, …) → list[dict]
          - describe tools (describe_pod, get_deployment_status, …) → dict
          - log tools (get_pod_logs) → str
        """
