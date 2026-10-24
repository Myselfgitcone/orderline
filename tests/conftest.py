import os
import uuid

import psycopg
import pytest

from orderline import db

ADMIN_DSN = os.environ.get("ORDERLINE_TEST_DSN")


@pytest.fixture
def make_db():
    """Fresh, empty databases on the scratch server; dropped (with their slots) afterwards."""
    if not ADMIN_DSN:
        pytest.skip("set ORDERLINE_TEST_DSN to a scratch Postgres with wal_level=logical")
    admin = psycopg.connect(ADMIN_DSN, autocommit=True)
    made = []

    def make():
        name = f"orderline_{uuid.uuid4().hex[:10]}"
        admin.execute(f"create database {name}")
        made.append(name)
        url = psycopg.conninfo.make_conninfo(ADMIN_DSN, dbname=name)
        return db.connect(url), name

    yield make
    for name in made:
        admin.execute(
            "select pg_drop_replication_slot(slot_name) from pg_replication_slots where database = %s",
            (name,),
        )
        admin.execute(f"drop database {name} with (force)")
    admin.close()


@pytest.fixture
def shop(make_db):
    """A shop with tables, publication and slot, named after its database."""
    conn, name = make_db()
    db.setup(conn, publication="orderline", slot=name)
    yield conn, name
    conn.close()
