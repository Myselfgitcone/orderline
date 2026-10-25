"""Shared plumbing for the Databricks ML tasks: arguments, Spark, and reading order facts."""

from __future__ import annotations

import argparse

# Numbers come back from Spark as decimals or ints; the model wants floats.
FACT_COLUMNS = """
    order_id, status, ship_method, carrier, warehouse, customer_segment,
    cast(item_count AS DOUBLE) AS item_count, cast(weight_g AS DOUBLE) AS weight_g,
    cast(total_usd AS DOUBLE) AS total_usd, cast(placed_dow AS DOUBLE) AS placed_dow,
    cast(placed_hour AS DOUBLE) AS placed_hour, cast(warehouse_load AS DOUBLE) AS warehouse_load,
    cast(distance_km AS DOUBLE) AS distance_km, from_nearest_warehouse,
    placed_at, promised_on, delivered_at, cancelled_at, is_late
"""


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="orderline")
    return parser.parse_args()


def spark():
    from pyspark.sql import SparkSession

    session = SparkSession.builder.getOrCreate()
    session.conf.set("spark.sql.session.timeZone", "UTC")
    return session


def model_name(catalog: str) -> str:
    return f"{catalog}.ml.late_delivery"


def read_facts(session, catalog: str, where: str = "true"):
    return session.sql(f"SELECT {FACT_COLUMNS} FROM {catalog}.gold.order_facts WHERE {where}").toPandas()
