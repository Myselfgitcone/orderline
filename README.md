# Orderline

Change data capture from a live order database into Databricks, with a
late-delivery model trained on the result.

Work in progress. What's here so far is the source side: the shop.

## The shop

A Postgres database for a made-up outdoor-gear retailer: customers, products,
four warehouses, orders and their line items. A simulator runs it forward an
hour at a time, so the tables change the way a real shop's do:

| What happens | What CDC sees |
| --- | --- |
| An order is placed, with its line items | inserts |
| It's paid, packed, shipped, delivered | four updates to the same row |
| A cart is abandoned after 2 hours | the order and its items are deleted |
| A customer moves or changes email | an update, worth keeping as history |
| A price changes, a product is retired | an update to the catalog |

Every table has `replica identity full`, so updates and deletes carry the whole
old row, not just the key.

The publication covers the `shop` schema only. The simulator's own bookkeeping
(`sim.clock`, `sim.due`) lives elsewhere, so downstream sees exactly what a
real shop would emit and nothing else.

### Why some orders arrive late

Checkout promises 5 days for standard shipping and 2 for express, whatever the
order. What actually decides the delivery date is planted in the simulator so
the model has something real to find:

| Factor | Effect on delivery (60-day run) |
| --- | --- |
| Carrier | standard via Parcelo 29% late, Northpost 17%, Swiftline 7% |
| Overflow routing to a far warehouse | 51% late vs 17% |
| Over 10 kg | 37% late vs 16% |
| Warehouse past daily capacity (promo days, holidays) | packing slows in proportion to the backlog |
| Sunday | no packing; only Swiftline delivers |
| Winter | some shipments lose 1 to 3 days to storms |

About 22% of orders arrive late overall. Because the causes are known, the model
can be checked against them, not just scored on accuracy.

### Same history, however it's run

Each simulated hour seeds its own random generator from the hour, in UTC. Building 60 days in one
run or in many small ones gives identical tables, and a test checks that.

## Run it

Needs Postgres with `wal_level=logical`.

```bash
uv sync
export ORDERLINE_DATABASE_URL=postgresql://postgres@localhost:5432/orderline
uv run orderline setup     # tables, publication, replication slot
uv run orderline tick      # first run builds 60 days of history; later runs catch up to now
uv run orderline stats
```

The slot is created before any data, so the first sync streams every row as an
insert and needs no snapshot.

## Tests

```bash
export ORDERLINE_TEST_DSN=postgresql://postgres@localhost:5432/postgres
uv run pytest
```

The Postgres tests create and drop their own databases. One of them reads the
replication slot directly and checks that it sees inserts, updates and deletes,
and nothing from the simulator's tables.
