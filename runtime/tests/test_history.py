"""Tests for the file-backed HistoryStore."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from agent_runtime.history import HistoryStore, _MAX_PERSISTED_MESSAGES


def _tmp_store() -> tuple[HistoryStore, tempfile.TemporaryDirectory]:
    td = tempfile.TemporaryDirectory()
    return HistoryStore(td.name), td


def test_disabled_when_no_directory():
    store = HistoryStore(None)
    assert not store.enabled
    store.save("any", [{"role": "user", "content": "x"}])
    msgs = store.load("any")
    assert msgs == []


def test_save_load_round_trip():
    store, td = _tmp_store()
    try:
        store.save(
            "session-1",
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ],
        )
        msgs = store.load("session-1")
        assert msgs == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
    finally:
        td.cleanup()


def test_session_id_whitelist():
    store, td = _tmp_store()
    try:
        for bad in ("", "../etc", "a/b", "with space", "null\x00"):
            try:
                store.save(bad, [{"role": "user", "content": "x"}])
            except ValueError:
                continue
            else:
                raise AssertionError(f"expected ValueError for {bad!r}")
    finally:
        td.cleanup()


def test_cap_keeps_recent_messages():
    store, td = _tmp_store()
    try:
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(_MAX_PERSISTED_MESSAGES + 50)]
        store.save("cap", msgs)
        loaded = store.load("cap")
        assert len(loaded) == _MAX_PERSISTED_MESSAGES
        # must keep the most recent
        assert loaded[-1]["content"] == f"m{_MAX_PERSISTED_MESSAGES + 49}"
        assert loaded[0]["content"] == "m50"
    finally:
        td.cleanup()


def test_load_filters_unexpected_role():
    store, td = _tmp_store()
    try:
        path = Path(td.name) / "mixed.json"
        path.write_text(
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "ok"},
                        {"role": "bogus", "content": "drop"},
                        {"role": "tool", "content": "keep"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        msgs = store.load("mixed")
        roles = [m["role"] for m in msgs]
        assert "bogus" not in roles
        assert "user" in roles and "tool" in roles
    finally:
        td.cleanup()