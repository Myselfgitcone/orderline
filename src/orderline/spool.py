"""Where change events land between Debezium and Databricks: JSONL files per table."""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, TextIO


class Spool:
    """Append Debezium events to one JSONL file per table, rotating large ones.

    Each line is the event exactly as Debezium sent it, so bronze in Databricks
    holds what was captured, not our interpretation of it. Logical decoding
    messages (op "m") are markers for the sync job, not data, and aren't written.
    """

    def __init__(self, root: Path, run_id: str, max_lines: int = 50_000):
        self.root = root
        self.run_id = run_id
        self.max_lines = max_lines
        self.counts: Counter[str] = Counter()
        self.ops: Counter[str] = Counter()
        self.markers: set[str] = set()
        self.last_event_at: float | None = None
        self.max_lsn = 0  # highest data-change LSN seen; markers don't count
        self._open: dict[str, tuple[TextIO, Path, int]] = {}
        self._parts: Counter[str] = Counter()
        self._done: list[Path] = []
        self._lock = threading.Lock()

    def add(self, events: list[dict[str, Any]]) -> None:
        with self._lock:
            touched = set()
            for event in events:
                if not event:  # a tombstone, if they're ever switched on
                    continue
                if event.get("op") == "m":
                    message = event.get("message") or {}
                    self.markers.add(_decode(message.get("content")))
                    continue
                table = (event.get("source") or {}).get("table")
                if not table:
                    continue  # heartbeats and anything else without a table
                out = self._file(table)
                out.write(json.dumps(event, separators=(",", ":")) + "\n")
                self._bump(table)
                touched.add(table)
                self.counts[table] += 1
                self.max_lsn = max(self.max_lsn, (event.get("source") or {}).get("lsn") or 0)
                self.ops[event.get("op", "?")] += 1
            # Durable before the HTTP 200 goes back, which is what lets
            # Debezium count these events as delivered.
            for table in touched:
                handle = self._open[table][0]
                handle.flush()
                os.fsync(handle.fileno())
            self.last_event_at = time.monotonic()

    def close(self) -> list[Path]:
        with self._lock:
            for table in list(self._open):
                self._finish(table)
            return sorted(self._done)

    def _file(self, table: str) -> TextIO:
        if table not in self._open:
            self._parts[table] += 1
            folder = self.root / table
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{self.run_id}-{self._parts[table]:03d}.jsonl"
            self._open[table] = (path.open("w", encoding="utf-8"), path, 0)
        return self._open[table][0]

    def _bump(self, table: str) -> None:
        handle, path, lines = self._open[table]
        self._open[table] = (handle, path, lines + 1)
        if lines + 1 >= self.max_lines:
            self._finish(table)

    def _finish(self, table: str) -> None:
        handle, path, _ = self._open.pop(table)
        handle.close()
        self._done.append(path)


def _decode(content: Any) -> str:
    """Message content arrives base64-encoded (it's bytea); be lenient if it isn't."""
    if not isinstance(content, str):
        return ""
    try:
        return base64.b64decode(content, validate=True).decode()
    except ValueError:
        return content
