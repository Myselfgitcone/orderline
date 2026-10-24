from datetime import UTC, datetime, timedelta

import pytest

from orderline import db, simulate

UNTIL = datetime(2026, 10, 20, 12, tzinfo=UTC)
KNOBS = simulate.Knobs(orders_per_day=120, backfill_days=4)


def snapshot(conn):
    """Everything a downstream consumer could see, in a stable order."""
    tables = {
        "customers": "select * from shop.customers order by customer_id",
        "products": "select * from shop.products order by product_id",
        "orders": "select * from shop.orders order by order_id",
        "items": "select * from shop.order_items order by order_id, line_no",
    }
    return {name: conn.execute(query).fetchall() for name, query in tables.items()}


@pytest.mark.postgres
def test_setup_is_idempotent_and_publishes_only_the_shop(shop):
    conn, name = shop
    again = db.setup(conn, publication="orderline", slot=name)
    assert again[1:] == ["publication orderline exists", f"slot {name} exists"]
    published = {
        row[0]
        for row in conn.execute(
            "select schemaname || '.' || tablename from pg_publication_tables where pubname = 'orderline'"
        )
    }
    assert published == {
        "shop.warehouses",
        "shop.customers",
        "shop.products",
        "shop.orders",
        "shop.order_items",
    }


@pytest.mark.postgres
def test_history_is_the_same_in_one_run_or_many(make_db):
    one, _ = make_db()
    many, _ = make_db()
    for conn in (one, many):
        with conn.transaction():
            conn.execute(db.SCHEMA.read_text())

    start = UNTIL - timedelta(days=KNOBS.backfill_days)
    simulate.run(one, UNTIL, seed=11, knobs=KNOBS, start=start)
    for cut in (
        start + timedelta(hours=7),
        start + timedelta(days=1, hours=13),
        start + timedelta(days=3),
        UNTIL,
    ):
        simulate.run(many, cut, seed=11, knobs=KNOBS, start=start)

    assert snapshot(one) == snapshot(many)
    assert len(snapshot(one)["orders"]) > 300


@pytest.mark.postgres
def test_a_second_run_with_nothing_due_does_nothing(shop):
    conn, _ = shop
    first = simulate.run(conn, UNTIL, knobs=KNOBS)
    second = simulate.run(conn, UNTIL + timedelta(minutes=59), knobs=KNOBS)
    assert first.hours == 96 and first.placed > 0
    assert second.hours == 0
    assert str(second) == "Already up to date."


@pytest.mark.postgres
def test_orders_follow_the_rules(shop):
    conn, _ = shop
    simulate.run(conn, UNTIL, knobs=KNOBS)

    broken = conn.execute(
        """
        select order_id, status from shop.orders where not (
            -- each status has exactly the timestamps it should
            case status
                when 'pending'   then paid_at is null and packed_at is null and cancelled_at is null
                when 'paid'      then paid_at is not null and packed_at is null and cancelled_at is null
                when 'packed'    then packed_at >= paid_at and shipped_at is null
                when 'shipped'   then shipped_at >= packed_at and delivered_at is null
                when 'delivered' then delivered_at >= shipped_at
                when 'cancelled' then cancelled_at >= paid_at and packed_at is null
            end
            and paid_at >= placed_at
            and updated_at = greatest(placed_at, paid_at, packed_at, shipped_at, delivered_at, cancelled_at)
        )
        """
    ).fetchall()
    assert broken == []

    totals = conn.execute(
        """
        select o.order_id from shop.orders o join shop.order_items i using (order_id)
        group by o.order_id, o.total_cents, o.item_count, o.ship_method
        having o.item_count <> sum(i.quantity)
            or o.total_cents - sum(i.quantity * i.unit_price_cents) not in (0, 599, 1499)
        """
    ).fetchall()
    assert totals == []

    # Abandoned carts are deleted, so nothing sits unpaid for more than the 2-hour window.
    stale = conn.execute(
        "select count(*) from shop.orders where status = 'pending' and placed_at < %s",
        (UNTIL - timedelta(hours=3),),
    ).fetchone()[0]
    assert stale == 0

    # Every open order has exactly one next step waiting; finished ones have none.
    mismatched = conn.execute(
        """
        select o.order_id from shop.orders o left join sim.due d using (order_id)
        where (o.status in ('delivered', 'cancelled')) = (d.order_id is not null)
        """
    ).fetchall()
    assert mismatched == []


@pytest.mark.postgres
def test_planted_signal_shows_up(shop):
    conn, _ = shop
    simulate.run(conn, UNTIL, knobs=simulate.Knobs(backfill_days=21))
    late = dict(
        conn.execute(
            """
            select carrier, avg(((delivered_at at time zone 'America/New_York')::date > promised_on)::int)
            from shop.orders where status = 'delivered' and ship_method = 'standard' group by 1
            """
        ).fetchall()
    )
    assert late["Parcelo"] > late["Northpost"] > late["Swiftline"]


@pytest.mark.postgres
def test_the_slot_sees_inserts_updates_and_deletes_but_not_the_simulator(shop):
    conn, name = shop
    simulate.run(conn, UNTIL, knobs=simulate.Knobs(orders_per_day=80, backfill_days=1))
    kinds = {
        kind: n
        for kind, n in conn.execute(
            """
            select chr(get_byte(data, 0)), count(*)
            from pg_logical_slot_peek_binary_changes(%s, null, null,
                'proto_version', '1', 'publication_names', 'orderline')
            group by 1
            """,
            (name,),
        )
    }
    orders = conn.execute("select count(*) from shop.orders").fetchone()[0]
    assert kinds["I"] > orders  # orders, items, customers, products, warehouses
    assert kinds["U"] > orders  # every surviving order was at least paid
    assert kinds["D"] > 0  # abandoned carts and their items
    # Relation messages name each table once per session; sim.* must never appear.
    relations = conn.execute(
        """
        select count(*) from pg_logical_slot_peek_binary_changes(%s, null, null,
            'proto_version', '1', 'publication_names', 'orderline')
        where chr(get_byte(data, 0)) = 'R' and position('sim'::bytea in data) > 0
        """,
        (name,),
    ).fetchone()[0]
    assert relations == 0
