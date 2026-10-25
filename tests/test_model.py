from datetime import UTC, date, datetime
from importlib.resources import files

import pandas as pd
import pytest

import late_model
from orderline import simulate


def facts_frame(rows):
    base = {
        "ship_method": "standard",
        "carrier": "Parcelo",
        "warehouse": "EWR",
        "customer_segment": "new",
        "item_count": 1,
        "weight_g": 1000,
        "total_usd": 50.0,
        "placed_dow": 3,
        "placed_hour": 12,
        "warehouse_load": 0.8,
        "distance_km": 500,
        "from_nearest_warehouse": True,
        "cancelled_at": None,
        "delivered_at": None,
        "is_late": None,
    }
    return pd.DataFrame([base | r for r in rows])


def test_only_orders_past_their_promise_are_labelled_and_still_out_counts_as_late():
    facts = facts_frame(
        [
            {"order_id": 1, "promised_on": date(2026, 10, 1), "is_late": False},  # arrived on time
            {"order_id": 2, "promised_on": date(2026, 10, 1), "is_late": True},  # arrived late
            {"order_id": 3, "promised_on": date(2026, 10, 1)},  # still out after its promise: late
            {"order_id": 4, "promised_on": date(2026, 10, 9)},  # promise not reached: unknown
            {"order_id": 5, "promised_on": date(2026, 10, 1), "cancelled_at": "2026-09-28"},  # never shipped
        ]
    )
    known = late_model.labelled(facts, as_of=date(2026, 10, 5))
    assert dict(zip(known["order_id"], known["late"], strict=True)) == {1: False, 2: True, 3: True}


def test_split_is_by_time_not_random():
    known = pd.DataFrame({"placed_at": pd.date_range("2026-09-01", periods=40, freq="D", tz="UTC")})
    train, test = late_model.split_by_time(known, test_days=14)
    assert train["placed_at"].max() < test["placed_at"].min()
    assert len(test) == 15  # the cutoff day itself and the 14 after it


def test_promotion_rules():
    assert late_model.should_promote(0.90, None, checks_passed=True)  # first model
    assert late_model.should_promote(0.899, 0.900, checks_passed=True)  # within noise
    assert not late_model.should_promote(0.88, 0.90, checks_passed=True)  # worse
    assert not late_model.should_promote(0.99, 0.90, checks_passed=False)  # wrong about the causes


def test_risk_bands():
    assert [late_model.risk_band(p) for p in (0.1, 0.25, 0.49, 0.5, 0.9)] == [
        "low",
        "medium",
        "medium",
        "high",
        "high",
    ]


@pytest.mark.postgres
def test_model_beats_the_baseline_and_finds_the_planted_causes(shop):
    conn, _ = shop
    simulate.run(
        conn, datetime(2026, 10, 20, tzinfo=UTC), knobs=simulate.Knobs(orders_per_day=200, backfill_days=45)
    )
    cur = conn.execute(files("orderline").joinpath("sql/order_facts.sql").read_text())
    facts = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])

    trained = late_model.train(facts)

    assert trained.metrics["auc"] > 0.8
    assert trained.metrics["auc"] > trained.baseline["auc"] + 0.1
    assert trained.checks_passed, trained.summary()
    assert set(list(trained.importances)[:4]) & {"distance_km", "carrier", "weight_g", "warehouse_load"}
