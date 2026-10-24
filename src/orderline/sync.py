"""One sync: drain the replication slot into Databricks.

    marker  ->  wait for it  ->  upload  ->  stop Debezium (slot advances)
                                    \\-> on any failure: kill Debezium (slot stays put)

The slot only moves after the batch is safely in the volume, so a failed sync
loses nothing; the next one gets the same changes again. That makes delivery
at-least-once, and silver de-duplicates on LSN.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from orderline import debezium
from orderline.receiver import Receiver
from orderline.settings import Settings
from orderline.spool import Spool
from orderline.volume import Volume


@dataclass
class SyncReport:
    run_id: str
    counts: Counter = field(default_factory=Counter)
    ops: Counter = field(default_factory=Counter)
    files: list[Path] = field(default_factory=list)
    uploaded: list[str] = field(default_factory=list)
    marker_lsn: str = ""
    last_data_lsn: str = ""
    confirmed_lsn: str = ""
    slot_advanced: bool = False
    seconds: float = 0.0

    def __str__(self) -> str:
        tables = ", ".join(f"{t} {n}" for t, n in sorted(self.counts.items())) or "no changes"
        ops = ", ".join(f"{o}={n}" for o, n in sorted(self.ops.items()))
        where = (
            f"uploaded {len(self.uploaded)} files"
            if self.uploaded
            else f"kept {len(self.files)} files locally"
        )
        slot = "confirmed" if self.slot_advanced else "NOT confirmed (next sync resends)"
        return (
            f"Sync {self.run_id}: {sum(self.counts.values())} events ({tables}; {ops}); {where}; "
            f"last change {self.last_data_lsn or 'none'}, slot {slot} at {self.confirmed_lsn}; "
            f"{self.seconds:.0f}s."
        )


def run(conn: psycopg.Connection, settings: Settings, upload: bool, timeout_s: float = 900) -> SyncReport:
    started = time.monotonic()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report = SyncReport(run_id)
    slot = conn.execute(
        "select active from pg_replication_slots where slot_name = %s", (settings.slot,)
    ).fetchone()
    if slot is None:
        raise RuntimeError(f"no replication slot {settings.slot}; run `orderline setup` first")
    if slot[0]:
        raise RuntimeError(f"slot {settings.slot} is in use; is another sync running?")
    volume = None
    if upload:
        if not (settings.databricks_host and settings.databricks_token):
            raise RuntimeError(
                "set ORDERLINE_DATABRICKS_HOST and ORDERLINE_DATABRICKS_TOKEN, or use --no-upload"
            )
        volume = Volume(
            settings.databricks_host, settings.databricks_token.get_secret_value(), settings.volume
        )

    root = settings.spool_dir / run_id
    spool = Spool(root, run_id)
    container = f"orderline-debezium-{run_id.lower()}"
    with Receiver(spool, settings.receiver_port):
        debezium.start(
            settings.debezium_image,
            container,
            settings.database_url,
            settings.slot,
            settings.publication,
            f"http://localhost:{settings.receiver_port}/events",
        )
        try:
            # Transactional, so it's decoded in commit order: everything committed
            # before this point reaches the receiver before the marker does.
            report.marker_lsn = conn.execute(
                "select pg_logical_emit_message(true, 'orderline', %s)::text", (run_id,)
            ).fetchone()[0]
            _wait_for(spool, run_id, container, timeout_s)
            report.files = spool.close()
            if volume:
                report.uploaded = volume.upload_all(root, report.files)
        except BaseException:
            debezium.kill(container)
            spool.close()
            raise
        debezium.stop(container)

    report.counts, report.ops = spool.counts, spool.ops
    report.last_data_lsn = format_lsn(spool.max_lsn) if spool.max_lsn else ""
    report.confirmed_lsn, report.slot_advanced = _confirmed(conn, settings.slot, spool.max_lsn)
    report.seconds = time.monotonic() - started
    return report


def _wait_for(spool: Spool, run_id: str, container: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while run_id not in spool.markers:
        if not debezium.running(container):
            raise RuntimeError(f"Debezium stopped before the marker arrived:\n{debezium.logs(container)}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"no marker after {timeout_s:.0f}s:\n{debezium.logs(container)}")
        time.sleep(0.5)


def parse_lsn(text: str) -> int:
    """'0/16A51E8' -> the same 64-bit number Debezium puts in source.lsn."""
    high, low = text.split("/")
    return (int(high, 16) << 32) + int(low, 16)


def format_lsn(value: int) -> str:
    return f"{value >> 32:X}/{value & 0xFFFFFFFF:X}"


def _confirmed(conn: psycopg.Connection, slot: str, last_change: int) -> tuple[str, bool]:
    """Whether the slot, once Debezium lets go of it, is past every change we captured.

    Compared with the last data change rather than the marker: Debezium confirms
    up to the last transaction it fully handed over, which can stop just short of
    the marker's own (empty) transaction. Resending that costs nothing.
    """
    for _ in range(30):
        active, confirmed = conn.execute(
            "select active, confirmed_flush_lsn::text from pg_replication_slots where slot_name = %s",
            (slot,),
        ).fetchone()
        if not active:
            break
        time.sleep(1)
    return confirmed, parse_lsn(confirmed) >= last_change
