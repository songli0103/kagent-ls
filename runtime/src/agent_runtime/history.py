"""Session-scoped chat history persistence.

When the operator sets RUNTIME_HISTORY_DIR (and mounts a PVC at that
path), the runtime can persist chat history per session_id across pod
restarts. Without a session_id, the runtime behaves as before (stateless
per request).

Storage layout:

    <RUNTIME_HISTORY_DIR>/<session_id>.json
    {
      "messages": [
        {"role": "user"|"assistant", "content": "..."},
        ...
      ],
      "updated_at": "2026-06-19T10:30:00Z"
    }

The on-disk format is intentionally simple (one JSON file per session)
so a human can `kubectl exec` into the pod and grep. We do not need an
embedded DB; expected sessions are O(1) per Agent and file sizes are
small (a few KB even for long conversations).
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Whitelist for session_id characters. Stricter than a path-segment
# blacklist: only alphanumerics, dot, underscore, dash. Prevents
# directory traversal, NUL bytes, control chars, and surprising
# Unicode (look-alikes that some filesystems normalize differently).
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

# Hard cap on persisted messages per session. A long-running chat could
# otherwise grow unbounded; at ~1 KB per turn, 200 messages is ~200 KB
# which is still trivially small but bounded. When the cap is hit we
# keep the *last* N messages so the most recent context survives.
_MAX_PERSISTED_MESSAGES = 200


class HistoryStore:
    """File-backed session store. Pure stdlib, no external deps."""

    def __init__(self, directory: str | os.PathLike[str] | None) -> None:
        self._dir: Path | None
        if directory:
            self._dir = Path(directory)
            # Make sure the directory exists; the operator pre-creates it
            # via the PVC, but a missing directory is recoverable (the
            # runtime would just fail to persist rather than 500).
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
                logger.info("history store enabled", extra={"dir": str(self._dir)})
            except OSError as exc:  # noqa: PERF203
                # Don't crash startup over a history-store failure: log
                # loudly and disable persistence for this process.
                logger.warning(
                    "history dir unavailable, persistence disabled",
                    extra={"dir": str(self._dir), "reason": str(exc)},
                )
                self._dir = None
        else:
            self._dir = None

    @property
    def enabled(self) -> bool:
        return self._dir is not None

    def _path(self, session_id: str) -> Path:
        # Whitelist-based sanitization: refuse any session_id that
        # doesn't match the strict alphanumeric+._- pattern. This
        # forbids path separators, "..", NUL bytes, and control chars
        # in a single check.
        if not _SESSION_ID_RE.match(session_id or ""):
            raise ValueError(f"invalid session_id: {session_id!r}")
        if self._dir is None:
            raise RuntimeError("history store not enabled")
        return self._dir / f"{session_id}.json"

    def load(self, session_id: str) -> list[dict[str, Any]]:
        """Return the prior messages for this session, or [] if none."""
        if not self.enabled:
            return []
        path = self._path(session_id)
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            # Corrupt or unreadable file: log and start fresh. We do NOT
            # delete the file (the human can inspect/recover it), but we
            # also don't fail the request — the user's question should
            # still get answered.
            logger.warning(
                "history file unreadable, starting empty",
                extra={"session_id": session_id, "reason": str(exc)},
            )
            return []
        messages = payload.get("messages", [])
        # Defensive: only keep entries with role+content.
        return [
            m for m in messages
            if isinstance(m, dict) and isinstance(m.get("content"), str)
            and m.get("role") in {"user", "assistant", "system", "tool"}
        ]

    def save(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """Persist `messages` for `session_id`. Atomic via tmp+rename.

        Trims to the last `_MAX_PERSISTED_MESSAGES` entries so a runaway
        session can't grow the file without bound.
        """
        if not self.enabled:
            return
        path = self._path(session_id)
        if len(messages) > _MAX_PERSISTED_MESSAGES:
            messages = messages[-_MAX_PERSISTED_MESSAGES:]
        payload = {
            "messages": messages,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        data = json.dumps(payload, ensure_ascii=False, indent=2)
        # Same dir as the target so rename is atomic (same filesystem).
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._dir), prefix=f".{session_id}.", suffix=".tmp"
        )
        # Ownership of fd transfers to fdopen on success; if fdopen
        # itself raises (rare: ENOMEM, EMFILE race), close the raw fd
        # explicitly to avoid leaking it.
        try:
            f = os.fdopen(fd, "w", encoding="utf-8")
        except OSError:
            os.close(fd)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        try:
            with f:
                f.write(data)
                f.flush()
                # fsync best-effort; on read-only mounts this raises but
                # the outer try still cleans up.
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp_path, path)
        except OSError as exc:
            # Don't fail the chat if we can't persist — the user still
            # gets their answer this turn, just without a durable record.
            logger.warning(
                "history save failed",
                extra={"session_id": session_id, "reason": str(exc)},
            )
            # Best-effort tmp cleanup.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
