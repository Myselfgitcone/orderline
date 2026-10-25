"""Pull the numbers for the public page out of Databricks.

Uses the SQL Statement Execution API against the workspace's SQL warehouse, so
the only credentials needed are the ones the sync already has. Everything goes
into one JSON file that a static page reads.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

NUMERIC = {"BYTE", "SHORT", "INT", "LONG", "FLOAT", "DOUBLE", "DECIMAL"}


class QueryFailed(RuntimeError):
    pass


class SqlWarehouse:
    """Runs a statement and returns rows as dicts with real numbers and booleans."""

    def __init__(
        self,
        host: str,
        token: str,
        warehouse_id: str | None = None,
        client: httpx.Client | None = None,
        poll_s: float = 2.0,
        timeout_s: float = 600,
    ):
        self.base = host if host.startswith("http") else f"https://{host}"
        self.client = client or httpx.Client(timeout=90, headers={"Authorization": f"Bearer {token}"})
        self.poll_s = poll_s
        self.timeout_s = timeout_s
        self.warehouse_id = warehouse_id or self._only_warehouse()

    def _only_warehouse(self) -> str:
        """Free Edition has exactly one SQL warehouse; use whichever there is."""
        response = self.client.get(f"{self.base}/api/2.0/sql/warehouses")
        response.raise_for_status()
        warehouses = response.json().get("warehouses", [])
        if not warehouses:
            raise QueryFailed("the workspace has no SQL warehouse")
        running = [w for w in warehouses if w.get("state") == "RUNNING"]
        return (running or warehouses)[0]["id"]

    def query(self, sql: str) -> list[dict[str, Any]]:
        response = self.client.post(
            f"{self.base}/api/2.0/sql/statements",
            json={
                "statement": sql,
                "warehouse_id": self.warehouse_id,
                "wait_timeout": "50s",
                "on_wait_timeout": "CONTINUE",  # a cold warehouse can take a few minutes
                "format": "JSON_ARRAY",
                "disposition": "INLINE",
            },
        )
        response.raise_for_status()
        body = response.json()
        deadline = time.monotonic() + self.timeout_s
        while body["status"]["state"] in ("PENDING", "RUNNING"):
            if time.monotonic() > deadline:
                raise QueryFailed(f"statement still {body['status']['state']} after {self.timeout_s:.0f}s")
            time.sleep(self.poll_s)
            body = self.client.get(f"{self.base}/api/2.0/sql/statements/{body['statement_id']}").json()
        if body["status"]["state"] != "SUCCEEDED":
            error = body["status"].get("error", {}).get("message", body["status"]["state"])
            raise QueryFailed(f"{error}\n--- query ---\n{sql.strip()}")

        columns = body["manifest"]["schema"]["columns"]
        rows = list((body.get("result") or {}).get("data_array") or [])
        link = (body.get("result") or {}).get("next_chunk_internal_link")
        while link:
            chunk = self.client.get(f"{self.base}{link}").json()
            rows += chunk.get("data_array") or []
            link = chunk.get("next_chunk_internal_link")
        return [
            {c["name"]: _typed(value, c["type_name"]) for c, value in zip(columns, row, strict=True)}
            for row in rows
        ]


def _typed(value: str | None, type_name: str) -> Any:
    if value is None:
        return None
    if type_name in NUMERIC:
        number = float(value)
        return int(number) if type_name in {"BYTE", "SHORT", "INT", "LONG"} else number
    if type_name == "BOOLEAN":
        return value.lower() == "true"
    return value


def queries(catalog: str) -> dict[str, str]:
    c = catalog
    recent = "placed_at >= current_date() - INTERVAL 30 DAYS"
    return {
        "summary": f"""
            SELECT
              (SELECT count(*) FROM {c}.silver.orders) AS orders,
              (SELECT count_if(status IN ('paid', 'packed', 'shipped')) FROM {c}.silver.orders)
                AS open_orders,
              (SELECT count(DISTINCT customer_id) FROM {c}.silver.customers_history) AS customers,
              (SELECT max(committed_at) FROM {c}.silver.orders) AS last_commit,
              (SELECT max(landed_at) FROM {c}.gold.capture_health) AS last_landed,
              (SELECT round(avg(is_late::INT), 4) FROM {c}.gold.order_facts
                WHERE delivered_at >= current_date() - INTERVAL 30 DAYS) AS late_share_30d,
              (SELECT round(avg(lead_days), 2) FROM {c}.gold.order_facts
                WHERE delivered_at >= current_date() - INTERVAL 30 DAYS) AS lead_days_30d,
              (SELECT max(scored_at) FROM {c}.ml.late_risk) AS scored_at
        """,
        "daily": f"""
            SELECT day, sum(orders) AS orders, round(sum(revenue_usd), 0) AS revenue_usd,
                   sum(delivered) AS delivered, sum(late) AS late, sum(cancelled) AS cancelled
            FROM {c}.gold.daily_delivery
            WHERE day >= date_sub(current_date(), 60)
            GROUP BY day ORDER BY day
        """,
        "carriers": f"""
            SELECT carrier, ship_method, count_if(delivered_at IS NOT NULL) AS delivered,
                   round(avg(CASE WHEN delivered_at IS NOT NULL THEN is_late::INT END), 4) AS late_share,
                   round(avg(lead_days), 2) AS lead_days
            FROM {c}.gold.order_facts WHERE {recent}
            GROUP BY ALL ORDER BY late_share DESC
        """,
        "warehouses": f"""
            SELECT warehouse, count(*) AS orders, round(avg(warehouse_load), 2) AS avg_load,
                   round(max(warehouse_load), 2) AS peak_load,
                   round(avg(1 - from_nearest_warehouse::INT), 3) AS overflow_share,
                   round(avg(CASE WHEN delivered_at IS NOT NULL THEN is_late::INT END), 4) AS late_share
            FROM {c}.gold.order_facts WHERE {recent}
            GROUP BY warehouse ORDER BY warehouse
        """,
        "syncs": f"""
            SELECT sync_run, sum(events) AS events, sum(inserts) AS inserts, sum(updates) AS updates,
                   sum(deletes) AS deletes, max(landed_at) AS landed_at, max(last_commit) AS last_commit,
                   round(max(minutes_to_land), 1) AS minutes_to_land
            FROM {c}.gold.capture_health
            WHERE sync_run IS NOT NULL AND sync_run <> ''
            GROUP BY sync_run ORDER BY sync_run DESC LIMIT 12
        """,
        "risky": f"""
            SELECT r.order_id, r.status, r.carrier, r.ship_method, r.warehouse, r.promised_on,
                   r.p_late, r.risk_band, f.distance_km, round(f.weight_g / 1000, 1) AS weight_kg,
                   round(f.warehouse_load, 2) AS warehouse_load, f.customer_state
            FROM {c}.ml.late_risk r JOIN {c}.gold.order_facts f USING (order_id)
            ORDER BY r.p_late DESC LIMIT 12
        """,
        "bands": f"SELECT risk_band, count(*) AS orders FROM {c}.ml.late_risk GROUP BY risk_band",
        "runs": f"""
            SELECT trained_at, model_version, auc, baseline_auc, average_precision, late_share,
                   test_orders, checks_passed, checks, importances, promoted, test_from
            FROM {c}.ml.training_runs ORDER BY trained_at DESC LIMIT 8
        """,
        # Each order's first score against what then happened, once the promise date has passed.
        "track_record": f"""
            WITH first_score AS (
              SELECT order_id, min_by(p_late, scored_at) AS p, min_by(risk_band, scored_at) AS band
              FROM {c}.ml.late_risk_history GROUP BY order_id
            )
            SELECT s.band AS risk_band, count(*) AS orders, round(avg(s.p), 3) AS predicted,
                   round(avg(coalesce(f.is_late, true)::INT), 3) AS actual
            FROM first_score s JOIN {c}.gold.order_facts f USING (order_id)
            WHERE f.cancelled_at IS NULL AND f.promised_on < current_date()
            GROUP BY s.band
        """,
    }


def export(warehouse: SqlWarehouse, catalog: str, out: Path) -> dict[str, Any]:
    data: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds")}
    for name, sql in queries(catalog).items():
        data[name] = warehouse.query(sql)
    data["summary"] = data["summary"][0] if data["summary"] else {}
    for run in data["runs"]:
        for key in ("checks", "importances"):
            run[key] = json.loads(run[key]) if run.get(key) else {}
    if not data["daily"]:
        raise QueryFailed("no daily numbers came back; refusing to publish an empty page")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1))
    return data
