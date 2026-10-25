-- gold.order_facts, rebuilt straight from the shop in Postgres, so the model can
-- be trained and tested locally without Databricks. Same columns, same meaning,
-- with one shortcut: Postgres only has each customer's current address, while
-- gold joins to the address they had at checkout.
with backlog as (
    select o.order_id,
           (select count(*) from shop.orders p
            where p.warehouse_id = o.warehouse_id
              and p.placed_at >= o.placed_at - interval '24 hours' and p.placed_at < o.placed_at
           ) as warehouse_orders_24h
    from shop.orders o
),
nearest as (
    select c.customer_id,
           (select w.warehouse_id from shop.warehouses w
            order by power(w.lat - c.lat, 2) + power((w.lon - c.lon) * cos(radians(c.lat)), 2)
            limit 1) as nearest_warehouse_id
    from shop.customers c
)
select
    o.order_id,
    o.status,
    o.ship_method,
    o.carrier,
    o.item_count,
    o.weight_g,
    o.total_cents / 100.0 as total_usd,
    o.placed_at,
    extract(isodow from o.placed_at at time zone 'America/New_York')::int % 7 + 1 as placed_dow,  -- 1 = Sunday
    extract(hour from o.placed_at at time zone 'America/New_York')::int as placed_hour,
    o.promised_on,
    o.delivered_at,
    o.cancelled_at,
    w.code as warehouse,
    w.daily_capacity,
    b.warehouse_orders_24h,
    b.warehouse_orders_24h::float / w.daily_capacity as warehouse_load,
    c.state as customer_state,
    c.segment as customer_segment,
    round((2 * 6371 * asin(sqrt(
        power(sin(radians(c.lat - w.lat) / 2), 2)
        + cos(radians(w.lat)) * cos(radians(c.lat)) * power(sin(radians(c.lon - w.lon) / 2), 2)
    )))::numeric) as distance_km,
    o.warehouse_id = n.nearest_warehouse_id as from_nearest_warehouse,
    case when o.delivered_at is not null
        then (o.delivered_at at time zone 'America/New_York')::date > o.promised_on end as is_late
from shop.orders o
join shop.warehouses w using (warehouse_id)
join shop.customers c using (customer_id)
join nearest n using (customer_id)
join backlog b using (order_id)
