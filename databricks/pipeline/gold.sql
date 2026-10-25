-- Gold: what the business and the model read.

-- Each customer version with the business-time window it was true for, so an
-- order joins to the customer as they were when it was placed.
-- Each version also knows which warehouse is nearest to that address.
CREATE TEMPORARY VIEW customer_versions AS
WITH versions AS (
  SELECT
    customer_id, email, city, state, lat, lon, segment,
    updated_at AS valid_from,
    coalesce(
      lead(updated_at) OVER (PARTITION BY customer_id ORDER BY __START_AT),
      TIMESTAMP'9999-12-31'
    ) AS valid_to
  FROM silver.customers_history
)
SELECT
  v.customer_id, v.email, v.city, v.state, v.lat, v.lon, v.segment, v.valid_from, v.valid_to,
  min_by(w.warehouse_id, pow(w.lat - v.lat, 2) + pow((w.lon - v.lon) * cos(radians(v.lat)), 2))
    AS nearest_warehouse_id
FROM versions v
CROSS JOIN silver.warehouses w
GROUP BY ALL;


CREATE OR REFRESH MATERIALIZED VIEW gold.order_facts (
  CONSTRAINT customer_found EXPECT (customer_state IS NOT NULL),
  CONSTRAINT steps_in_order EXPECT (
    coalesce(paid_at >= placed_at, true)
    AND coalesce(shipped_at >= packed_at, true)
    AND coalesce(delivered_at >= shipped_at, true)
  ),
  CONSTRAINT lines_add_up EXPECT (line_items = item_count OR line_items IS NULL)
)
COMMENT 'One row per order with everything known at checkout, plus how delivery went. The model trains on this.'
AS
WITH lines AS (
  SELECT order_id, sum(quantity) AS line_items, count(*) AS distinct_products
  FROM silver.order_items GROUP BY order_id
),
placed AS (
  SELECT
    o.*,
    -- Orders the same warehouse took in the 24 hours before this one: the backlog it walked into.
    count(*) OVER (
      PARTITION BY o.warehouse_id ORDER BY unix_timestamp(o.placed_at)
      RANGE BETWEEN 86400 PRECEDING AND 1 PRECEDING
    ) AS warehouse_orders_24h
  FROM silver.orders o
)
SELECT
  o.order_id,
  o.status,
  o.ship_method,
  o.carrier,
  o.item_count,
  l.line_items,
  l.distinct_products,
  o.weight_g,
  o.total_cents / 100.0 AS total_usd,
  o.placed_at,
  from_utc_timestamp(o.placed_at, 'America/New_York') AS placed_local,
  dayofweek(from_utc_timestamp(o.placed_at, 'America/New_York')) AS placed_dow,  -- 1 = Sunday
  hour(from_utc_timestamp(o.placed_at, 'America/New_York')) AS placed_hour,
  o.promised_on,
  o.paid_at, o.packed_at, o.shipped_at, o.delivered_at, o.cancelled_at,
  w.code AS warehouse,
  w.daily_capacity,
  o.warehouse_orders_24h,
  o.warehouse_orders_24h / w.daily_capacity AS warehouse_load,
  c.state AS customer_state,
  c.segment AS customer_segment,
  -- Great-circle distance from warehouse to customer, as the customer's address stood at checkout.
  round(2 * 6371 * asin(sqrt(
    pow(sin(radians(c.lat - w.lat) / 2), 2)
    + cos(radians(w.lat)) * cos(radians(c.lat)) * pow(sin(radians(c.lon - w.lon) / 2), 2)
  ))) AS distance_km,
  -- Was it shipped from the warehouse nearest the customer? Computed, not stored in the shop.
  o.warehouse_id = c.nearest_warehouse_id AS from_nearest_warehouse,
  -- Outcome. Lateness is judged in shop time, the same calendar the promise was made in.
  CASE WHEN o.delivered_at IS NOT NULL
    THEN to_date(from_utc_timestamp(o.delivered_at, 'America/New_York')) > o.promised_on END AS is_late,
  CASE WHEN o.delivered_at IS NOT NULL
    THEN datediff(to_date(from_utc_timestamp(o.delivered_at, 'America/New_York')), o.promised_on) END
    AS days_vs_promise,
  (unix_timestamp(o.delivered_at) - unix_timestamp(o.placed_at)) / 86400.0 AS lead_days,
  o.lsn,
  o.committed_at
FROM placed o
JOIN silver.warehouses w USING (warehouse_id)
LEFT JOIN lines l USING (order_id)
LEFT JOIN customer_versions c
  ON c.customer_id = o.customer_id AND o.placed_at >= c.valid_from AND o.placed_at < c.valid_to;


CREATE OR REFRESH MATERIALIZED VIEW gold.daily_delivery
COMMENT 'Per shop day, warehouse and carrier: volume, revenue and how deliveries went.'
AS SELECT
  to_date(placed_local) AS day,
  warehouse,
  carrier,
  ship_method,
  count(*) AS orders,
  round(sum(total_usd), 2) AS revenue_usd,
  count_if(status = 'cancelled') AS cancelled,
  count_if(delivered_at IS NOT NULL) AS delivered,
  count_if(is_late) AS late,
  round(avg(CASE WHEN delivered_at IS NOT NULL THEN is_late::INT END), 3) AS late_share,
  round(avg(lead_days), 2) AS avg_lead_days,
  round(avg(warehouse_load), 2) AS avg_warehouse_load
FROM gold.order_facts
GROUP BY ALL;


CREATE OR REFRESH MATERIALIZED VIEW gold.capture_health
COMMENT 'How each sync went: events per table, and how long changes took to land in Databricks.'
AS
WITH events AS (
  SELECT 'orders' AS source_table, op, source.ts_ms, landed_at, source_file FROM bronze.orders
  UNION ALL SELECT 'order_items', op, source.ts_ms, landed_at, source_file FROM bronze.order_items
  UNION ALL SELECT 'customers', op, source.ts_ms, landed_at, source_file FROM bronze.customers
  UNION ALL SELECT 'products', op, source.ts_ms, landed_at, source_file FROM bronze.products
  UNION ALL SELECT 'warehouses', op, source.ts_ms, landed_at, source_file FROM bronze.warehouses
)
SELECT
  regexp_extract(source_file, '/([0-9]{8}T[0-9]{6}Z)-[0-9]+\\.jsonl$', 1) AS sync_run,
  source_table,
  count(*) AS events,
  count_if(op = 'c') AS inserts,
  count_if(op = 'u') AS updates,
  count_if(op = 'd') AS deletes,
  timestamp_millis(min(ts_ms)) AS first_commit,
  timestamp_millis(max(ts_ms)) AS last_commit,
  max(landed_at) AS landed_at,
  round((unix_millis(max(landed_at)) - max(ts_ms)) / 60000.0, 1) AS minutes_to_land
FROM events
GROUP BY ALL;
