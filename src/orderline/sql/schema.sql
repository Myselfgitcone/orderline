-- The shop: the OLTP tables Debezium captures. Nothing here knows about CDC
-- except replica identity, which makes updates and deletes carry the full old
-- row so downstream can see what changed, not just that something did.

create schema if not exists shop;

create table if not exists shop.warehouses (
    warehouse_id smallint primary key,
    code         text not null unique,
    city         text not null,
    state        text not null,
    lat          double precision not null,
    lon          double precision not null,
    -- orders a day it can pack before a backlog builds
    daily_capacity integer not null
);

create table if not exists shop.customers (
    customer_id bigint generated always as identity primary key,
    email       text not null unique,
    full_name   text not null,
    city        text not null,
    state       text not null,
    lat         double precision not null,
    lon         double precision not null,
    segment     text not null check (segment in ('new', 'regular', 'vip')),
    created_at  timestamptz not null,
    updated_at  timestamptz not null
);

create table if not exists shop.products (
    product_id  bigint generated always as identity primary key,
    sku         text not null unique,
    name        text not null,
    category    text not null,
    price_cents integer not null check (price_cents > 0),
    weight_g    integer not null check (weight_g > 0),
    active      boolean not null default true,
    updated_at  timestamptz not null
);

create table if not exists shop.orders (
    order_id     bigint generated always as identity primary key,
    customer_id  bigint not null references shop.customers,
    warehouse_id smallint not null references shop.warehouses,
    status       text not null
        check (status in ('pending', 'paid', 'packed', 'shipped', 'delivered', 'cancelled')),
    ship_method  text not null check (ship_method in ('standard', 'express')),
    carrier      text not null,
    item_count   integer not null,
    weight_g     integer not null,
    total_cents  integer not null,
    placed_at    timestamptz not null,
    promised_on  date not null,
    paid_at      timestamptz,
    packed_at    timestamptz,
    shipped_at   timestamptz,
    delivered_at timestamptz,
    cancelled_at timestamptz,
    updated_at   timestamptz not null
);

create index if not exists orders_status on shop.orders (status);
create index if not exists orders_warehouse_placed on shop.orders (warehouse_id, placed_at);

create table if not exists shop.order_items (
    order_id         bigint not null references shop.orders on delete cascade,
    line_no          smallint not null,
    product_id       bigint not null references shop.products,
    quantity         integer not null check (quantity > 0),
    unit_price_cents integer not null,
    primary key (order_id, line_no)
);

alter table shop.warehouses  replica identity full;
alter table shop.customers   replica identity full;
alter table shop.products    replica identity full;
alter table shop.orders      replica identity full;
alter table shop.order_items replica identity full;

-- The simulator's own bookkeeping. Kept out of the shop schema so it never
-- reaches the publication: downstream should only see what a real shop emits.
create schema if not exists sim;

create table if not exists sim.clock (
    id      boolean primary key default true check (id),
    next_hour timestamptz not null  -- first hour not simulated yet
);

create table if not exists sim.due (
    order_id   bigint primary key,
    next_step  text not null,
    due_at     timestamptz not null
);

create index if not exists due_at on sim.due (due_at);
