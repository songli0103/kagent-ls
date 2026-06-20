"""FastAPI server exposing the langchain-powered chat agent.

Endpoints (OpenAI-compatible where possible):
  GET  /                        single-page chat UI (HTML)
  GET  /health                  liveness probe (always 200 if the process is up)
  GET  /ready                   readiness probe (200 only after the agent is built)
  GET  /tools?agent=cluster|ops list tool names + descriptions (debug)
  GET  /sessions/{session_id}   return persisted chat history for a session
                                (404 if persistence is disabled or session
                                unknown). Empty body if the session file is
                                empty/corrupt.
  POST /classify                lightweight LLM intent classification:
                                {text} -> {intent: "read"|"write"}.
                                Used by the webui to auto-pick which agent to
                                route to without spinning up a full agent.
  POST /chat/completions        main chat endpoint; supports stream=true (SSE);
                                body has `agent: "cluster" | "ops"` to pick
                                which agent runs the request. When the
                                operator mounts a PVC and RUNTIME_HISTORY_DIR
                                is set, requests with a `session_id` field
                                load prior context from disk and persist
                                the new turn on completion.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Path as PathParam, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agent_runtime.agent import Agent, AgentName, ClusterAgent, DEFAULT_AGENT_NAME, OpsAgent
from agent_runtime.history import HistoryStore
from agent_runtime.llm import (
    DEFAULT_LLM_MODEL,
    get_chat_model,
)

logger = logging.getLogger(__name__)

# /classify uses a smaller, faster LLM timeout than the main chat path
# because the caller (webui auto-routing) is blocked on it. If the
# classifier can't respond in this window, fall back to the safe default.
_CLASSIFY_TIMEOUT_SECONDS: int = 15


class _ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None


class _ChatRequest(BaseModel):
    model: str = Field(default=DEFAULT_LLM_MODEL)
    messages: list[_ChatMessage]
    # Which agent handles this request. Defaults to the read-only cluster
    # agent so existing callers are unaffected. Selecting `ops` enables the
    # restart/scale tools (and the agent-runtime-write RBAC role).
    agent: AgentName = DEFAULT_AGENT_NAME
    # OpenAI fields we accept but currently ignore (stubbed for SDK compat):
    temperature: float | None = None
    top_p: float | None = None
    n: int | None = 1
    stream: bool = False
    user: str | None = None
    # session_id opts the request into history persistence. The runtime
    # loads prior messages from <RUNTIME_HISTORY_DIR>/<session_id>.json
    # (prepending them to the new messages) and writes the combined
    # conversation back on completion. Empty / omitted → stateless,
    # which is the default so existing callers are unaffected.
    session_id: str | None = None


class _ChatResponse(BaseModel):
    answer: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    model: str
    agent: str
    usage: dict[str, int] = Field(default_factory=dict)


class _ClassifyRequest(BaseModel):
    text: str = Field(description="The user's raw input to classify.")


class _ClassifyResponse(BaseModel):
    intent: Literal["read", "write"]
    agent: AgentName
    # The LLM's raw one-word reply for debugging; usually just "READ" or
    # "WRITE". Empty if the LLM failed and we fell back to the safe default.
    raw: str = ""


# System prompt for the lightweight intent classifier. We deliberately use
# a separate, much shorter prompt than the agents themselves — this is a
# pure text-in / one-word-out decision, no tool calls, no chat history.
_CLASSIFY_SYSTEM_PROMPT = """你是一个意图分类器。根据用户的输入判断是要对 Kubernetes 集群进行变更操作（写），还是只想查询信息（读）。

- WRITE：用户想要变更集群状态（重启 / 重启 pod / 重启 deployment / 扩缩 / 缩容 / scale up / scale down / 删除 / 删 / 杀掉 / drain / cordon / 修改 / 改 / patch / update 等）
- READ：用户只想查询 / 了解集群状态（列出 / 看看 / 查看 / describe / 诊断 / 为什么 / 几个 / 哪些 / 状态 / 日志 / 等）

只回答一个单词：READ 或 WRITE。不要解释。"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the LLM + agents once at startup; tear down on shutdown."""
    try:
        llm = get_chat_model()
        app.state.llm = llm
        # Build both agents up front so /ready covers both, and per-request
        # dispatch is just a dict lookup (no lazy-init race).
        app.state.agents: dict[str, Agent] = {
            ClusterAgent.name: ClusterAgent(llm),
            OpsAgent.name: OpsAgent(llm),
        }
        # Back-compat alias for anything that still reads `app.state.agent`;
        # the chat endpoint itself uses `app.state.agents`.
        app.state.agent = app.state.agents[ClusterAgent.name]
        # History persistence is opt-in: only enabled when the operator
        # has set RUNTIME_HISTORY_DIR (and mounted a PVC there). When
        # disabled, the chat endpoint behaves exactly as before.
        app.state.history = HistoryStore(os.environ.get("RUNTIME_HISTORY_DIR"))
        app.state.ready = True
        tool_summary = {
            name: [t.name for t in agent.tools]
            for name, agent in app.state.agents.items()
        }
        logger.info("agents ready", extra={"tool_summary": tool_summary, "history_enabled": app.state.history.enabled})
    except Exception as exc:  # noqa: BLE001
        # /ready will report not-ready; /health still returns 200.
        app.state.ready = False
        app.state.startup_error = str(exc)
        logger.error("agent failed to initialise", extra={"reason": str(exc)})
    yield


app = FastAPI(title="kagent-ls runtime", version="0.3.0", lifespan=lifespan)


# Load the chat UI HTML once at import time. importlib.resources works for
# both editable (uv pip install -e .) and wheel installs, and avoids baking
# the HTML into a Python source file.
try:
    _WEBUI_HTML = (files("agent_runtime") / "webui" / "index.html").read_text(encoding="utf-8")
except Exception:  # noqa: BLE001
    _WEBUI_HTML = "<h1>kagent-ls runtime</h1><p>webui/index.html not found in package</p>"


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index() -> str:
    """Serve the single-page chat UI."""
    return _WEBUI_HTML


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> JSONResponse:
    if getattr(app.state, "ready", False):
        return JSONResponse({"status": "ready"})
    err = getattr(app.state, "startup_error", "agent not initialised")
    return JSONResponse({"status": "not_ready", "error": err}, status_code=503)


@app.get("/tools")
async def list_tools(agent: AgentName = Query(default=DEFAULT_AGENT_NAME)) -> list[dict[str, str]]:
    """List the tools exposed by the named agent (debug / introspection)."""
    agents: dict[str, Agent] | None = getattr(app.state, "agents", None)
    if not agents:
        return []
    selected = agents.get(agent)
    if selected is None:
        # Should be unreachable because pydantic enforces the Literal, but
        # guard anyway so unknown values give an empty list rather than 500.
        return []
    return [{"name": t.name, "description": t.description} for t in selected.tools]


def _classify_intent(text: str) -> _ClassifyResponse:
    """Run a single, lightweight LLM call to decide read vs write.

    Returns a `_ClassifyResponse` whose `intent` field is always one of
    `read` / `write` (defaults to `read` on any error — the safe choice
    because the read agent has no write tools and can't cause damage).

    Uses a dedicated `ChatOpenAI` with a shorter timeout so a stalled
    LLM never blocks the webui's auto-router past `_CLASSIFY_TIMEOUT_SECONDS`.
    """
    try:
        classifier = get_chat_model(timeout_seconds=_CLASSIFY_TIMEOUT_SECONDS)
        reply = classifier.invoke(
            [
                SystemMessage(content=_CLASSIFY_SYSTEM_PROMPT),
                HumanMessage(content=text),
            ]
        )
        raw = (reply.content or "").strip().upper()
        if "WRITE" in raw:
            return _ClassifyResponse(intent="write", agent=OpsAgent.name, raw=raw)
        return _ClassifyResponse(intent="read", agent=ClusterAgent.name, raw=raw)
    except Exception as exc:  # noqa: BLE001
        # Never let a classification failure kill the chat. Fall back to
        # the safe read-only agent and surface the error in the response.
        logger.warning("classify failed; defaulting to read", extra={"reason": str(exc)})
        return _ClassifyResponse(intent="read", agent=ClusterAgent.name, raw="")


@app.post("/classify", response_model=_ClassifyResponse)
async def classify(req: _ClassifyRequest) -> _ClassifyResponse:
    """Lightweight intent router used by the webui to pick the right agent.

    The classification is a direct LLM call (no agent machinery, no tools),
    so it costs ~50 tokens per request — much cheaper than spinning up the
    full agent just to look at the prompt. The webui calls this before
    /chat/completions and passes the returned `agent` back in the body.
    """
    if not getattr(app.state, "ready", False):
        raise HTTPException(
            status_code=503,
            detail=getattr(app.state, "startup_error", "agent not initialised"),
        )
    text = req.text.strip()
    if not text:
        # Empty input is meaningless to classify; route to the read agent.
        return _ClassifyResponse(intent="read", agent=ClusterAgent.name, raw="")
    return _classify_intent(text)


def _messages_to_dicts(req: _ChatRequest) -> list[dict[str, str]]:
    return [{"role": m.role, "content": m.content} for m in req.messages]


def _persist_turn(
    history: HistoryStore,
    session_id: str | None,
    request_messages: list[dict[str, str]],
    assistant_answer: str,
) -> None:
    """Append this turn's messages to the persisted session, if enabled.

    No-op when `session_id` is None (the client didn't opt in) or when
    history persistence is disabled (no PVC mounted).

    We persist only conversational roles ({user, assistant, system}) and
    drop `tool` role messages — the agent rebuilds tool calls on each
    invocation from the conversation so saving them would bloat the
    session file with redundant data.

    All failures are caught and logged: history persistence is best-effort
    and must never break the chat path. A disk-full or permission error
    when saving shouldn't surface to the client.
    """
    if not session_id or not history.enabled:
        return
    try:
        prior = history.load(session_id)
    except ValueError:
        logger.warning("invalid session_id, skipping persist", extra={"session_id": session_id})
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("history load failed, skipping persist", extra={"session_id": session_id, "reason": str(exc)})
        return
    conversational = [
        m for m in request_messages if m.get("role") in {"user", "assistant", "system"}
    ]
    combined = list(prior) + conversational
    if assistant_answer:
        combined.append({"role": "assistant", "content": assistant_answer})
    try:
        history.save(session_id, combined)
    except ValueError:
        logger.warning("invalid session_id on save, skipping", extra={"session_id": session_id})
    except Exception as exc:  # noqa: BLE001
        logger.warning("history save failed", extra={"session_id": session_id, "reason": str(exc)})


@app.get("/sessions/{session_id}")
async def get_session(session_id: str = PathParam(..., min_length=1, max_length=128)) -> JSONResponse:
    """Return the persisted chat history for `session_id`.

    Returns 503 if persistence isn't enabled (no PVC mounted — the
    operator didn't request history), 200 with `{"messages": []}` if
    enabled but the session is unknown, 200 with the messages otherwise.
    The session_id is sanitized server-side (see HistoryStore._path).
    """
    history: HistoryStore | None = getattr(app.state, "history", None)
    if history is None or not history.enabled:
        raise HTTPException(
            status_code=503,
            detail="history persistence is not enabled (RUNTIME_HISTORY_DIR not set)",
        )
    try:
        messages = history.load(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid session_id") from None
    return JSONResponse({"session_id": session_id, "messages": messages})


@app.post("/chat/completions")
async def chat_completions(req: _ChatRequest):
    if not getattr(app.state, "ready", False):
        raise HTTPException(
            status_code=503,
            detail=getattr(app.state, "startup_error", "agent not initialised"),
        )

    agents: dict[str, Agent] = app.state.agents
    agent = agents.get(req.agent)
    if agent is None:
        # Pydantic Literal already rejects unknown values with 422, but a
        # future field type change should still surface a clean 400 rather
        # than a KeyError.
        raise HTTPException(status_code=400, detail=f"unknown agent: {req.agent}")

    msg_dicts = _messages_to_dicts(req)
    # Defensive: drop any messages with an unknown role. Pydantic normally
    # catches this with a 422, but if a client sends e.g. role="tool_call"
    # as a free-form string some middleware in front of FastAPI could let it
    # through. We don't want those to crash the agent either way.
    msg_dicts = [m for m in msg_dicts if m.get("role") in {"system", "user", "assistant", "tool"}]

    history: HistoryStore = app.state.history

    if req.stream:
        # For streaming we collect events to extract the final answer, so
        # we can persist the turn at the end. We still stream the events
        # to the client as they arrive.
        async def event_source():
            collected_answer = ""
            try:
                for ev in agent.stream(msg_dicts):
                    if ev.get("type") == "done" and isinstance(ev.get("answer"), str):
                        collected_answer = ev["answer"]
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            except Exception as exc:  # noqa: BLE001
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)}, ensure_ascii=False)}\n\n"
            # Always emit [DONE] before the (best-effort) persist so a
            # disk-full or permission error can't strand the SSE stream
            # without a terminator. _persist_turn swallows its own
            # exceptions so this `finally` is belt-and-braces.
            try:
                yield "data: [DONE]\n\n"
            finally:
                _persist_turn(history, req.session_id, msg_dicts, collected_answer)

        return StreamingResponse(event_source(), media_type="text/event-stream")

    # Non-streaming: run the agent to completion and return the full response.
    try:
        result = agent.run(msg_dicts)
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent invocation failed", extra={"agent": req.agent})
        raise HTTPException(status_code=500, detail=f"agent error: {exc}") from exc

    answer = result.get("answer", "")
    _persist_turn(history, req.session_id, msg_dicts, answer)

    body = _ChatResponse(
        answer=answer,
        tool_calls=result.get("tool_calls", []),
        tool_results=result.get("tool_results", []),
        model=req.model,
        agent=req.agent,
    )
    return body.model_dump()
