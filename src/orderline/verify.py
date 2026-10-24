"""Replay captured events and compare the result with the source tables.

If every insert, update and delete arrived exactly as committed, applying them
in LSN order rebuilds each table as it stands in Postgres right now.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg

KEYS = {
    "warehouses": ("warehouse_id",),
    "customers": ("customer_id",),
    "products": ("product_id",),
    "orders": ("order_id",),
    "order_items": ("order_id", "line_no"),
}
EPOCH = date(1970, 1, 1)


@dataclass
class Check:
    events: int = 0
    duplicates: int = 0
    tables: dict[str, tuple[int, int]] = field(default_factory=dict)  # table -> (replayed rows, mismatches)
    examples: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(bad == 0 for _, bad in self.tables.values())


def read_events(root: Path) -> Iterable[dict[str, Any]]:
    for path in sorted(root.rglob("*.jsonl")):
        with path.open(encoding="utf-8") as lines:
            for line in lines:
                yield json.loads(line)


def replay(events: Iterable[dict[str, Any]], check: Check) -> dict[str, dict[tuple, dict]]:
    """Apply events in LSN order. Duplicates (same table and LSN) are applied once."""
    ordered = sorted(events, key=lambda e: (e["source"]["lsn"], e["source"]["table"]))
    state: dict[str, dict[tuple, dict]] = {table: {} for table in KEYS}
    seen = set()
    for event in ordered:
        check.events += 1
        table = event["source"]["table"]
        marker = (table, event["source"]["lsn"], json.dumps(event.get("after") or event.get("before")))
        if marker in seen:
            check.duplicates += 1
            continue
        seen.add(marker)
        row = event["after"] if event["op"] in ("c", "r", "u") else event["before"]
        key = tuple(row[k] for k in KEYS[table])
        if event["op"] == "d":
            state[table].pop(key, None)
        else:
            state[table][key] = row
    return state


def compare(conn: psycopg.Connection, state: dict[str, dict[tuple, dict]], check: Check) -> Check:
    for table, keys in KEYS.items():
        cur = conn.execute(f"select * from shop.{table}")
        columns = [d.name for d in cur.description]
        source = {}
        for values in cur:
            row = dict(zip(columns, values, strict=True))
            source[tuple(row[k] for k in keys)] = row
        replayed = state[table]
        bad = 0
        for key in source.keys() | replayed.keys():
            want, got = source.get(key), replayed.get(key)
            if want is None or got is None or any(not _same(want[c], got.get(c)) for c in columns):
                bad += 1
                if len(check.examples) < 5:
                    check.examples.append(f"{table} {key}: source={want} replayed={got}")
        check.tables[table] = (len(replayed), bad)
    return check


def _same(db_value: Any, event_value: Any) -> bool:
    """Debezium's JSON for a value vs the value psycopg returns."""
    if isinstance(db_value, datetime):
        return isinstance(event_value, str) and datetime.fromisoformat(event_value) == db_value.astimezone(
            UTC
        )
    if isinstance(db_value, date):
        return event_value == (db_value - EPOCH).days  # io.debezium.time.Date: days since epoch
    if isinstance(db_value, float) and isinstance(event_value, int | float):
        return abs(db_value - event_value) < 1e-9
    return db_value == event_value


def verify(conn: psycopg.Connection, root: Path) -> Check:
    check = Check()
    return compare(conn, replay(read_events(root), check), check)
