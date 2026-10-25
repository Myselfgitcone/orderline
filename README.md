# Orderline

Change data capture from a live order database into Databricks, with a
late-delivery model trained on the result.

Work in progress. Done so far: the shop, capture into Databricks, and the pipeline.

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

## Capture

```
Neon Postgres --(replication slot, pgoutput)--> Debezium Server --(HTTP, batches of 500)-->
    receiver --> JSONL per table --> Databricks Files API --> /Volumes/orderline/raw/cdc/<table>/
```

Databricks Free Edition can only reach a short list of trusted domains, so it
can't pull from Postgres. The sync runs in GitHub Actions every 6 hours and
pushes into a Unity Catalog volume instead.

One sync, in order:

1. Start Debezium Server in Docker. It has no stored offset, so it resumes from
   the slot's confirmed position. The slot is the only bookmark.
2. Write a marker into the WAL with `pg_logical_emit_message`. It's transactional,
   so when it reaches the receiver, everything committed before it has too.
3. Upload the batch to the volume.
4. Only now stop Debezium gracefully. It commits its offset, which confirms the
   LSN to the slot.

If anything fails before step 4, the container is killed instead, the slot
doesn't move, and the next sync sends the same changes again. Delivery is
at-least-once; silver de-duplicates on LSN.

The receiver writes each event exactly as Debezium sent it and fsyncs before
answering, so an HTTP 200 means the event is on disk.

```bash
uv run orderline sync               # needs Docker, plus ORDERLINE_DATABRICKS_HOST and _TOKEN
uv run orderline sync --no-upload   # keep the batch in out/spool
uv run orderline verify             # replay out/spool and compare with the source tables
```

`verify` applies every captured event in LSN order and checks the result matches
each source table row for row. CI runs the whole path twice against Postgres 17
with real Debezium, then verifies both batches together with
`--no-allow-duplicates`, which proves the second sync started exactly where the
first one stopped.

## In Databricks

A Lakeflow declarative pipeline, deployed as an asset bundle (`databricks/`):

| Layer | Tables | What it is |
| --- | --- | --- |
| bronze | `orders`, `order_items`, `customers`, `products`, `warehouses` | Debezium events as captured, loaded by Auto Loader from the volume |
| silver | `orders`, `order_items`, `warehouses` | Current state, rebuilt with `AUTO CDC ... SEQUENCE BY lsn` (SCD type 1); deletes are real deletes |
| silver | `customers_history`, `products_history` | Every version (SCD type 2), history tracked only on columns that matter |
| gold | `order_facts` | One row per order: what was known at checkout, joined to the customer *as they were then*, plus how delivery went |
| gold | `daily_delivery` | Per day, warehouse and carrier: orders, revenue, late share |
| gold | `capture_health` | Per sync: events by table and op, and minutes from commit in Postgres to landing in Databricks |

Bronze declares Debezium's whole envelope rather than inferring it: the JSON has
no types, so inference would guess. Dates arrive as days since 1970, timestamps as
ISO strings, and anything unexpected goes to `_rescued_data`, which an expectation
watches.

Sequencing by LSN, not by `updated_at`, is what makes replays safe: a batch sent
twice or late lands in commit order. Business time is still there for the as-of
join in gold.

After every sync, the workflow runs the bundle's `orderline-refresh` job. CI
validates the bundle against the workspace on every PR, and merging to `main`
deploys it.

## Tests

```bash
export ORDERLINE_TEST_DSN=postgresql://postgres@localhost:5432/postgres
uv run pytest
```

The Postgres tests create and drop their own databases. One of them reads the
replication slot directly and checks that it sees inserts, updates and deletes,
and nothing from the simulator's tables.
