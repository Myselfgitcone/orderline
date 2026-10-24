import base64
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from starlette.testclient import TestClient

from orderline import debezium, simulate
from orderline.receiver import Receiver, build_app
from orderline.spool import Spool
from orderline.verify import EPOCH, KEYS, Check, compare, replay
from orderline.volume import Volume


def event(table, op, lsn, before=None, after=None):
    return {
        "before": before,
        "after": after,
        "op": op,
        "ts_ms": 1,
        "source": {"table": table, "schema": "shop", "lsn": lsn, "txId": 1},
    }


def marker(text):
    return {
        "op": "m",
        "message": {"prefix": "orderline", "content": base64.b64encode(text.encode()).decode()},
    }


def test_spool_writes_one_file_per_table_and_keeps_markers_out(tmp_path):
    spool = Spool(tmp_path, "run1")
    spool.add(
        [
            event("orders", "c", 10, after={"order_id": 1}),
            event("order_items", "c", 11, after={"order_id": 1, "line_no": 1}),
            marker("run1"),
            None,
            {"ts_ms": 5},  # a heartbeat: no table
        ]
    )
    files = spool.close()
    assert [f.relative_to(tmp_path).as_posix() for f in files] == [
        "order_items/run1-001.jsonl",
        "orders/run1-001.jsonl",
    ]
    assert spool.markers == {"run1"}
    assert spool.counts == {"orders": 1, "order_items": 1}
    assert json.loads(files[1].read_text())["after"] == {"order_id": 1}


def test_spool_rotates_big_files(tmp_path):
    spool = Spool(tmp_path, "r", max_lines=2)
    spool.add([event("orders", "c", i, after={"order_id": i}) for i in range(5)])
    names = [f.name for f in spool.close()]
    assert names == ["r-001.jsonl", "r-002.jsonl", "r-003.jsonl"]


def test_receiver_takes_single_events_and_batches(tmp_path):
    spool = Spool(tmp_path, "r")
    client = TestClient(build_app(spool))
    assert client.post("/events", json=event("orders", "c", 1, after={"order_id": 1})).status_code == 200
    batch = [event("orders", "u", 2, after={"order_id": 1}), marker("r")]
    assert client.post("/events", json=batch).status_code == 200
    assert client.post("/events", content=b"not json").status_code == 400
    assert client.post("/events", json=[1, 2]).status_code == 400
    assert spool.counts["orders"] == 2 and spool.markers == {"r"}


def test_volume_upload_retries_and_treats_conflict_as_done(tmp_path, monkeypatch):
    monkeypatch.setattr("orderline.volume.time.sleep", lambda _: None)
    calls = []
    script = iter([503, 200, 409, 403])

    def handler(request):
        calls.append(request)
        return httpx.Response(next(script), text="nope")

    volume = Volume(
        "dbc-1.cloud.databricks.com",
        "tok",
        "/Volumes/orderline/raw/cdc/",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    file = tmp_path / "orders" / "r-001.jsonl"
    file.parent.mkdir()
    file.write_text("{}\n")
    assert volume.upload(file, "orders/r-001.jsonl") == "/Volumes/orderline/raw/cdc/orders/r-001.jsonl"
    assert str(calls[0].url) == (
        "https://dbc-1.cloud.databricks.com/api/2.0/fs/files/Volumes/orderline/raw/cdc/orders/r-001.jsonl"
        "?overwrite=false"
    )
    assert calls[0].headers["authorization"] == "Bearer tok"
    assert volume.upload(file, "orders/r-001.jsonl")  # 409: already there
    with pytest.raises(RuntimeError, match="403"):
        volume.upload(file, "orders/r-001.jsonl")


def test_debezium_env_from_a_neon_style_url():
    env = debezium.source_env(
        "postgresql://shop_owner:s3cret@ep-cool-1.us-east-2.aws.neon.tech/orderline?sslmode=require",
        slot="orderline",
        publication="orderline",
    )
    assert env["DEBEZIUM_SOURCE_DATABASE_HOSTNAME"] == "ep-cool-1.us-east-2.aws.neon.tech"
    assert env["DEBEZIUM_SOURCE_DATABASE_PORT"] == "5432"
    assert env["DEBEZIUM_SOURCE_DATABASE_PASSWORD"] == "s3cret"
    assert env["DEBEZIUM_SOURCE_DATABASE_SSLMODE"] == "require"
    assert env["DEBEZIUM_SOURCE_DATABASE_DBNAME"] == "orderline"


def test_replay_applies_in_lsn_order_and_skips_duplicates():
    check = Check()
    events = [
        event(
            "orders",
            "u",
            20,
            before={"order_id": 1, "status": "pending"},
            after={"order_id": 1, "status": "paid"},
        ),
        event("orders", "c", 10, after={"order_id": 1, "status": "pending"}),
        event("orders", "c", 30, after={"order_id": 2, "status": "pending"}),
        event("orders", "d", 40, before={"order_id": 2, "status": "pending"}),
        event("orders", "c", 10, after={"order_id": 1, "status": "pending"}),  # resent by a failed sync
    ]
    state = replay(events, check)
    assert state["orders"] == {(1,): {"order_id": 1, "status": "paid"}}
    assert check.duplicates == 1


def as_debezium(value):
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return (value - EPOCH).days
    return value


@pytest.mark.postgres
def test_verify_matches_a_faithful_capture_and_catches_a_lost_one(shop):
    conn, _ = shop
    simulate.run(conn, datetime(2026, 10, 20, tzinfo=UTC), knobs=simulate.Knobs(backfill_days=1))
    events, lsn = [], 0
    for table in KEYS:
        cur = conn.execute(f"select * from shop.{table}")
        columns = [d.name for d in cur.description]
        for values in cur:
            lsn += 1
            events.append(
                event(
                    table, "c", lsn, after={c: as_debezium(v) for c, v in zip(columns, values, strict=True)}
                )
            )

    check = Check()
    assert compare(conn, replay(events, check), check).ok

    lost = Check()
    compare(conn, replay(events[:-1], lost), lost)
    assert not lost.ok and lost.tables["order_items"][1] == 1

    stale = [dict(e) for e in events]
    order = next(e for e in stale if e["source"]["table"] == "orders")
    order["after"] = order["after"] | {
        "updated_at": as_debezium(datetime(2020, 1, 1, tzinfo=UTC) + timedelta(0))
    }
    wrong = Check()
    compare(conn, replay(stale, wrong), wrong)
    assert wrong.tables["orders"][1] == 1


def test_receiver_runs_on_a_real_socket(tmp_path):
    spool = Spool(tmp_path, "r")
    with Receiver(spool, port=8099):
        response = httpx.post(
            "http://127.0.0.1:8099/events", json=[event("orders", "c", 1, after={"order_id": 1})]
        )
    assert response.status_code == 200
    assert spool.counts["orders"] == 1
