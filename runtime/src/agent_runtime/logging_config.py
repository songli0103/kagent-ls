"""JSON-formatted logging configuration for the agent runtime."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Mapping

# LogRecord attributes that are reserved by the logging module itself.
# Any extras containing one of these keys (e.g. someone logging
# `extra={"name": ...}`) would either be silently overwritten by the
# LogRecord constructor (and in the case of "name" raise
# `KeyError: 'Attempt to overwrite "name" in LogRecord'`) or shadow a
# built-in field. We filter them out of the JSON `extra` payload so the
# operator's structured logs are predictable.
#
# Exported at module level so tests can reuse the exact set rather than
# duplicating it.
LOG_RECORD_RESERVED_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extras = self._extras(record)
        if extras:
            payload["extra"] = extras
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _extras(record: logging.LogRecord) -> Mapping[str, Any]:
        return {
            k: v for k, v in record.__dict__.items() if k not in LOG_RECORD_RESERVED_KEYS
        }


def configure_logging(level: int = logging.INFO) -> None:
    """Install the JSON formatter on the root logger."""
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # The kubernetes client library is very chatty at DEBUG. Tone it down.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("kubernetes").setLevel(logging.INFO)