"""LLM factory: build a ChatOpenAI pointed at the configured OpenAI-compatible endpoint.

Environment variables (injected by the Operator from a Secret):
- LLM_API_KEY   (required) — the API key
- LLM_BASE_URL  (default: DEFAULT_LLM_BASE_URL) — OpenAI-compatible endpoint
- LLM_MODEL     (default: DEFAULT_LLM_MODEL) — model name to send in API calls
"""
from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

# Defaults shared by all entry points (server, classify endpoint, tests).
# Override via env (LLM_BASE_URL / LLM_MODEL / LLM_API_KEY).
DEFAULT_LLM_BASE_URL: str = "https://api.minimaxi.com/v1"
DEFAULT_LLM_MODEL: str = "MiniMax-M3"
DEFAULT_LLM_TEMPERATURE: float = 0.3
DEFAULT_LLM_TIMEOUT_SECONDS: int = 60


def get_chat_model(
    base_url: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    timeout_seconds: int | None = None,
) -> ChatOpenAI:
    """Construct the chat model. Reads credentials from env each call so that
    the runtime can be re-deployed without rebuilding the image.

    Args:
        base_url: override the OpenAI-compatible endpoint. Defaults to the
            ``LLM_BASE_URL`` env var or `DEFAULT_LLM_BASE_URL`.
        model: override the model name. Defaults to ``LLM_MODEL`` env var
            or `DEFAULT_LLM_MODEL`.
        temperature: sampling temperature. Defaults to `DEFAULT_LLM_TEMPERATURE`.
        timeout_seconds: per-request timeout. Defaults to `DEFAULT_LLM_TIMEOUT_SECONDS`.
    """
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        # Defer raising until first request so the server still starts and
        # /health returns 200 (kubelet can tell the container is alive).
        # The /ready endpoint and the chat endpoint will surface the error.
        raise RuntimeError("LLM_API_KEY is not set in the container environment")

    return ChatOpenAI(
        model=model or os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL),
        api_key=api_key,
        base_url=base_url or os.environ.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL),
        temperature=temperature if temperature is not None else DEFAULT_LLM_TEMPERATURE,
        timeout=timeout_seconds if timeout_seconds is not None else DEFAULT_LLM_TIMEOUT_SECONDS,
    )
