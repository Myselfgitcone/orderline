-- Silver: the shop's tables rebuilt from their change events.
--
-- AUTO CDC applies each event in LSN order, so a batch that arrives twice (the
-- sync is at-least-once) or out of order still lands right. Deletes are real
-- deletes: an abandoned cart disappears here just like it did in Postgres.
--
-- Orders, line items and warehouses keep only their latest state (SCD type 1).
-- Customers and products keep history (SCD type 2), so an order can be joined
-- to the address and segment the customer had when it was placed.

-- Debezium's JSON to typed columns. Deletes carry the old row in `before`,
-- so the key comes from whichever side is present.

CREATE TEMPORARY VIEW orders_changes AS
SELECT
  coalesce(after.order_id, before.order_id) AS order_id,
  after.customer_id,
  after.warehouse_id,
  after.status,
  after.ship_method,
  after.carrier,
  after.item_count,
  after.weight_g,
  after.total_cents,
  to_timestamp(after.placed_at) AS placed_at,
  date_add(DATE'1970-01-01', after.promised_on) AS promised_on,  -- io.debezium.time.Date: days since epoch
  to_timestamp(after.paid_at) AS paid_at,
  to_timestamp(after.packed_at) AS packed_at,
  to_timestamp(after.shipped_at) AS shipped_at,
  to_timestamp(after.delivered_at) AS delivered_at,
  to_timestamp(after.cancelled_at) AS cancelled_at,
  to_timestamp(after.updated_at) AS updated_at,
  op,
  source.lsn AS lsn,
  timestamp_millis(source.ts_ms) AS committed_at
FROM STREAM(bronze.orders);

CREATE TEMPORARY VIEW order_items_changes AS
SELECT
  coalesce(after.order_id, before.order_id) AS order_id,
  coalesce(after.line_no, before.line_no) AS line_no,
  after.product_id,
  after.quantity,
  after.unit_price_cents,
  op,
  source.lsn AS lsn,
  timestamp_millis(source.ts_ms) AS committed_at
FROM STREAM(bronze.order_items);

CREATE TEMPORARY VIEW customers_changes AS
SELECT
  coalesce(after.customer_id, before.customer_id) AS customer_id,
  after.email,
  after.full_name,
  after.city,
  after.state,
  after.lat,
  after.lon,
  after.segment,
  to_timestamp(after.created_at) AS created_at,
  to_timestamp(after.updated_at) AS updated_at,
  op,
  source.lsn AS lsn
FROM STREAM(bronze.customers);

CREATE TEMPORARY VIEW products_changes AS
SELECT
  coalesce(after.product_id, before.product_id) AS product_id,
  after.sku,
  after.name,
  after.category,
  after.price_cents,
  after.weight_g,
  after.active,
  to_timestamp(after.updated_at) AS updated_at,
  op,
  source.lsn AS lsn
FROM STREAM(bronze.products);

CREATE TEMPORARY VIEW warehouses_changes AS
SELECT
  coalesce(after.warehouse_id, before.warehouse_id) AS warehouse_id,
  after.code,
  after.city,
  after.state,
  after.lat,
  after.lon,
  after.daily_capacity,
  op,
  source.lsn AS lsn
FROM STREAM(bronze.warehouses);


CREATE OR REFRESH STREAMING TABLE silver.orders
COMMENT 'Orders as they stand in the shop now. One row per order; abandoned carts are deleted.';

CREATE FLOW orders_apply AS AUTO CDC INTO silver.orders
FROM STREAM(orders_changes)
KEYS (order_id)
APPLY AS DELETE WHEN op = 'd'
SEQUENCE BY lsn
COLUMNS * EXCEPT (op)
STORED AS SCD TYPE 1;


CREATE OR REFRESH STREAMING TABLE silver.order_items
COMMENT 'Order lines as they stand now.';

CREATE FLOW order_items_apply AS AUTO CDC INTO silver.order_items
FROM STREAM(order_items_changes)
KEYS (order_id, line_no)
APPLY AS DELETE WHEN op = 'd'
SEQUENCE BY lsn
COLUMNS * EXCEPT (op)
STORED AS SCD TYPE 1;


CREATE OR REFRESH STREAMING TABLE silver.warehouses
COMMENT 'The four warehouses and their daily packing capacity.';

CREATE FLOW warehouses_apply AS AUTO CDC INTO silver.warehouses
FROM STREAM(warehouses_changes)
KEYS (warehouse_id)
APPLY AS DELETE WHEN op = 'd'
SEQUENCE BY lsn
COLUMNS * EXCEPT (op)
STORED AS SCD TYPE 1;


-- History is tracked only on what matters downstream; an updated_at bump
-- alone doesn't open a new version.
CREATE OR REFRESH STREAMING TABLE silver.customers_history
COMMENT 'Every version of every customer. __START_AT/__END_AT are LSNs; updated_at is the business time.';

CREATE FLOW customers_apply AS AUTO CDC INTO silver.customers_history
FROM STREAM(customers_changes)
KEYS (customer_id)
APPLY AS DELETE WHEN op = 'd'
SEQUENCE BY lsn
COLUMNS * EXCEPT (op)
STORED AS SCD TYPE 2
TRACK HISTORY ON email, city, state, lat, lon, segment;


CREATE OR REFRESH STREAMING TABLE silver.products_history
COMMENT 'Every version of every product: price changes and retirements.';

CREATE FLOW products_apply AS AUTO CDC INTO silver.products_history
FROM STREAM(products_changes)
KEYS (product_id)
APPLY AS DELETE WHEN op = 'd'
SEQUENCE BY lsn
COLUMNS * EXCEPT (op)
STORED AS SCD TYPE 2
TRACK HISTORY ON price_cents, active, name;
