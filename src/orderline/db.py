"""Connecting to the shop and preparing it for change data capture."""

from __future__ import annotations

from importlib.resources import files

import psycopg
from psycopg import sql

SCHEMA = files("orderline").joinpath("sql/schema.sql")


def connect(url: str) -> psycopg.Connection:
    # Autocommit, so every write sits in an explicit `with conn.transaction()`
    # and nothing commits by accident when a connection closes.
    conn = psycopg.connect(url, autocommit=True)
    conn.execute("set timezone = 'UTC'")
    return conn


def setup(conn: psycopg.Connection, publication: str, slot: str) -> list[str]:
    """Create the tables, the publication and the replication slot. Safe to rerun.

    The slot has to exist before the shop has any data: Debezium then streams
    every row as an insert, and the first sync needs no snapshot.
    """
    done = []
    with conn.transaction():
        conn.execute(SCHEMA.read_text())
    done.append("tables ready")

    level = conn.execute("show wal_level").fetchone()[0]
    if level != "logical":
        raise RuntimeError(
            f"wal_level is {level}; CDC needs logical. "
            "On Neon, turn on logical replication in the project settings."
        )

    if not conn.execute(
        "select exists (select from pg_publication where pubname = %s)", (publication,)
    ).fetchone()[0]:
        conn.execute(
            sql.SQL("create publication {} for tables in schema shop").format(sql.Identifier(publication))
        )
        done.append(f"publication {publication} created")
    else:
        done.append(f"publication {publication} exists")

    if not conn.execute(
        "select exists (select from pg_replication_slots where slot_name = %s)", (slot,)
    ).fetchone()[0]:
        conn.execute("select pg_create_logical_replication_slot(%s, 'pgoutput')", (slot,))
        done.append(f"slot {slot} created")
    else:
        done.append(f"slot {slot} exists")
    return done


STATS = """
with delivered as (
    select o.*, (o.delivered_at at time zone 'America/New_York')::date > o.promised_on as late
    from shop.orders o where o.status = 'delivered'
)
select 'orders' as metric, count(*)::text as value from shop.orders
union all select 'customers', count(*)::text from shop.customers
union all select 'open', count(*)::text from shop.orders
    where status in ('pending', 'paid', 'packed', 'shipped')
union all select 'delivered', count(*)::text from delivered
union all select 'late share', round(avg(late::int), 3)::text from delivered
union all select 'cancelled', count(*)::text from shop.orders where status = 'cancelled'
union all select 'simulated through', (select next_hour::text from sim.clock)
"""


def stats(conn: psycopg.Connection) -> list[tuple[str, str]]:
    return conn.execute(STATS).fetchall()
