-- Bronze: Debezium change events exactly as they were captured, one table per
-- source table. Auto Loader picks up each JSONL batch the sync job uploads.
--
-- The schemas are spelled out rather than inferred. Debezium's JSON has no type
-- information (schemas.enable=false), so inference would guess: dates arrive as
-- days since 1970 and timestamps as ISO strings. The whole envelope is declared,
-- so _rescued_data stays empty unless Debezium or the shop's tables change shape,
-- and the expectation below shows it the moment they do.

CREATE OR REFRESH STREAMING TABLE bronze.orders (
  CONSTRAINT has_position EXPECT (source.lsn IS NOT NULL),
  CONSTRAINT nothing_rescued EXPECT (_rescued_data IS NULL)
)
COMMENT 'Change events for shop.orders, as captured by Debezium.'
AS SELECT *, _metadata.file_path AS source_file, _metadata.file_modification_time AS landed_at
FROM STREAM read_files(
  '${orderline.volume}/orders/',
  format => 'json',
  rescuedDataColumn => '_rescued_data',
  schema => '
    before STRUCT<order_id BIGINT, customer_id BIGINT, warehouse_id INT, status STRING, ship_method STRING,
      carrier STRING, item_count INT, weight_g INT, total_cents INT, placed_at STRING, promised_on INT,
      paid_at STRING, packed_at STRING, shipped_at STRING, delivered_at STRING, cancelled_at STRING,
      updated_at STRING>,
    after STRUCT<order_id BIGINT, customer_id BIGINT, warehouse_id INT, status STRING, ship_method STRING,
      carrier STRING, item_count INT, weight_g INT, total_cents INT, placed_at STRING, promised_on INT,
      paid_at STRING, packed_at STRING, shipped_at STRING, delivered_at STRING, cancelled_at STRING,
      updated_at STRING>,
    op STRING, ts_ms BIGINT, ts_us BIGINT, ts_ns BIGINT,
    source STRUCT<version STRING, connector STRING, name STRING, ts_ms BIGINT, ts_us BIGINT, ts_ns BIGINT,
      snapshot STRING, db STRING, sequence STRING, schema STRING, table STRING, txId BIGINT, lsn BIGINT,
      xmin BIGINT>,
    transaction STRUCT<id STRING, total_order BIGINT, data_collection_order BIGINT>'
);

CREATE OR REFRESH STREAMING TABLE bronze.order_items (
  CONSTRAINT has_position EXPECT (source.lsn IS NOT NULL),
  CONSTRAINT nothing_rescued EXPECT (_rescued_data IS NULL)
)
COMMENT 'Change events for shop.order_items, as captured by Debezium.'
AS SELECT *, _metadata.file_path AS source_file, _metadata.file_modification_time AS landed_at
FROM STREAM read_files(
  '${orderline.volume}/order_items/',
  format => 'json',
  rescuedDataColumn => '_rescued_data',
  schema => '
    before STRUCT<order_id BIGINT, line_no INT, product_id BIGINT, quantity INT, unit_price_cents INT>,
    after STRUCT<order_id BIGINT, line_no INT, product_id BIGINT, quantity INT, unit_price_cents INT>,
    op STRING, ts_ms BIGINT, ts_us BIGINT, ts_ns BIGINT,
    source STRUCT<version STRING, connector STRING, name STRING, ts_ms BIGINT, ts_us BIGINT, ts_ns BIGINT,
      snapshot STRING, db STRING, sequence STRING, schema STRING, table STRING, txId BIGINT, lsn BIGINT,
      xmin BIGINT>,
    transaction STRUCT<id STRING, total_order BIGINT, data_collection_order BIGINT>'
);

CREATE OR REFRESH STREAMING TABLE bronze.customers (
  CONSTRAINT has_position EXPECT (source.lsn IS NOT NULL),
  CONSTRAINT nothing_rescued EXPECT (_rescued_data IS NULL)
)
COMMENT 'Change events for shop.customers, as captured by Debezium.'
AS SELECT *, _metadata.file_path AS source_file, _metadata.file_modification_time AS landed_at
FROM STREAM read_files(
  '${orderline.volume}/customers/',
  format => 'json',
  rescuedDataColumn => '_rescued_data',
  schema => '
    before STRUCT<customer_id BIGINT, email STRING, full_name STRING, city STRING, state STRING,
      lat DOUBLE, lon DOUBLE, segment STRING, created_at STRING, updated_at STRING>,
    after STRUCT<customer_id BIGINT, email STRING, full_name STRING, city STRING, state STRING,
      lat DOUBLE, lon DOUBLE, segment STRING, created_at STRING, updated_at STRING>,
    op STRING, ts_ms BIGINT, ts_us BIGINT, ts_ns BIGINT,
    source STRUCT<version STRING, connector STRING, name STRING, ts_ms BIGINT, ts_us BIGINT, ts_ns BIGINT,
      snapshot STRING, db STRING, sequence STRING, schema STRING, table STRING, txId BIGINT, lsn BIGINT,
      xmin BIGINT>,
    transaction STRUCT<id STRING, total_order BIGINT, data_collection_order BIGINT>'
);

CREATE OR REFRESH STREAMING TABLE bronze.products (
  CONSTRAINT has_position EXPECT (source.lsn IS NOT NULL),
  CONSTRAINT nothing_rescued EXPECT (_rescued_data IS NULL)
)
COMMENT 'Change events for shop.products, as captured by Debezium.'
AS SELECT *, _metadata.file_path AS source_file, _metadata.file_modification_time AS landed_at
FROM STREAM read_files(
  '${orderline.volume}/products/',
  format => 'json',
  rescuedDataColumn => '_rescued_data',
  schema => '
    before STRUCT<product_id BIGINT, sku STRING, name STRING, category STRING, price_cents INT,
      weight_g INT, active BOOLEAN, updated_at STRING>,
    after STRUCT<product_id BIGINT, sku STRING, name STRING, category STRING, price_cents INT,
      weight_g INT, active BOOLEAN, updated_at STRING>,
    op STRING, ts_ms BIGINT, ts_us BIGINT, ts_ns BIGINT,
    source STRUCT<version STRING, connector STRING, name STRING, ts_ms BIGINT, ts_us BIGINT, ts_ns BIGINT,
      snapshot STRING, db STRING, sequence STRING, schema STRING, table STRING, txId BIGINT, lsn BIGINT,
      xmin BIGINT>,
    transaction STRUCT<id STRING, total_order BIGINT, data_collection_order BIGINT>'
);

CREATE OR REFRESH STREAMING TABLE bronze.warehouses (
  CONSTRAINT has_position EXPECT (source.lsn IS NOT NULL),
  CONSTRAINT nothing_rescued EXPECT (_rescued_data IS NULL)
)
COMMENT 'Change events for shop.warehouses, as captured by Debezium.'
AS SELECT *, _metadata.file_path AS source_file, _metadata.file_modification_time AS landed_at
FROM STREAM read_files(
  '${orderline.volume}/warehouses/',
  format => 'json',
  rescuedDataColumn => '_rescued_data',
  schema => '
    before STRUCT<warehouse_id INT, code STRING, city STRING, state STRING, lat DOUBLE, lon DOUBLE,
      daily_capacity INT>,
    after STRUCT<warehouse_id INT, code STRING, city STRING, state STRING, lat DOUBLE, lon DOUBLE,
      daily_capacity INT>,
    op STRING, ts_ms BIGINT, ts_us BIGINT, ts_ns BIGINT,
    source STRUCT<version STRING, connector STRING, name STRING, ts_ms BIGINT, ts_us BIGINT, ts_ns BIGINT,
      snapshot STRING, db STRING, sequence STRING, schema STRING, table STRING, txId BIGINT, lsn BIGINT,
      xmin BIGINT>,
    transaction STRUCT<id STRING, total_order BIGINT, data_collection_order BIGINT>'
);
