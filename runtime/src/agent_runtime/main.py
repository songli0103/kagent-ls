"""Entry point for the agent runtime container.

Starts the uvicorn server that hosts the langchain-backed FastAPI app.
"""
from __future__ import annotations

import logging
import os

import uvicorn

from agent_runtime.logging_config import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    host = os.environ.get("RUNTIME_HOST", "0.0.0.0")
    port = int(os.environ.get("RUNTIME_PORT", "8080"))
    log_level = os.environ.get("RUNTIME_LOG_LEVEL", "info").lower()
    logger.info("starting agent runtime", extra={"host": host, "port": port})
    uvicorn.run(
        "agent_runtime.server:app",
        host=host,
        port=port,
        log_level=log_level,
        access_log=False,
    )


if __name__ == "__main__":
    main()
