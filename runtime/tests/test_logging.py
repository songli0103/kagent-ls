"""Tests for the structured JSON log formatter."""

from __future__ import annotations

import io
import json
import logging

from agent_runtime.logging_config import LOG_RECORD_RESERVED_KEYS, JsonFormatter


def _format(record: logging.LogRecord) -> dict:
    formatter = JsonFormatter()
    payload = formatter.format(record)
    return json.loads(payload)


def test_json_formatter_emits_known_fields():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="x.py",
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    out = _format(record)
    assert out["level"] == "INFO"
    assert out["logger"] == "test"
    assert out["message"] == "hello world"
    assert "timestamp" in out


def test_json_formatter_includes_safe_extras():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="x.py",
        lineno=1,
        msg="ran",
        args=(),
        exc_info=None,
    )
    # `name` is reserved; `pod_name` is safe.
    record.pod_name = "p1"
    record.namespace = "default"
    out = _format(record)
    assert out["extra"] == {"pod_name": "p1", "namespace": "default"}


def test_json_formatter_strips_reserved_keys():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="x.py",
        lineno=1,
        msg="x",
        args=(),
        exc_info=None,
    )
    # Inject a *different* reserved key (something we don't otherwise
    # surface, like `lineno`). It must NOT leak into the extras payload
    # because it's in LOG_RECORD_RESERVED_KEYS.
    record.lineno = 999
    # Add a safe extra to ensure the extras payload still appears.
    record.pod_name = "p1"
    out = _format(record)
    extras = out.get("extra", {})
    assert "lineno" not in extras
    assert extras.get("pod_name") == "p1"


def test_reserved_keys_constant_contains_baseline():
    # Defensive: a baseline of well-known reserved keys must be in the set
    # so future edits don't accidentally drop them.
    for key in ("name", "msg", "levelname", "asctime"):
        assert key in LOG_RECORD_RESERVED_KEYS