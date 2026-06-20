"""Agent abstractions and concrete implementations."""

from typing import Literal

from agent_runtime.agent.base import Agent
from agent_runtime.agent.cluster_agent import ClusterAgent
from agent_runtime.agent.ops_agent import OpsAgent

# Single source of truth for the agent-name wire format. The `Literal` is
# duplicated in server.py (request body + query param) but they all
# reference this alias; add a new agent here and the rest follows.
AgentName = Literal["cluster", "ops"]
DEFAULT_AGENT_NAME: str = ClusterAgent.name

__all__ = ["Agent", "ClusterAgent", "OpsAgent", "AgentName", "DEFAULT_AGENT_NAME"]