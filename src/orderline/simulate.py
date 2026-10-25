"""The shop's day-to-day: orders arriving and moving through fulfilment.

Time advances an hour at a time, from the first hour not yet simulated up to
the last full hour before `until`. Each hour gets its own random generator,
seeded from the hour itself, so the shop's history comes out the same whether
it's built in one run or a hundred small ones.

Some things make an order arrive late, on purpose, so the model downstream has
something real to find:

- carrier: Parcelo is the cheap, slow one
- distance: orders routed to a far warehouse (overflow) travel further
- backlog: a warehouse past its daily capacity packs slower (promo days, holidays)
- Sunday: nobody packs, and only Swiftline delivers
- weight: anything over 10 kg rides a slower network
- winter: some shipments in Dec to Feb lose days to storms

The promise shown at checkout ignores all of that: 5 days standard, 2 express.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from random import Random
from zoneinfo import ZoneInfo

import psycopg

from orderline.catalog import CATEGORIES, Product, make_catalog, make_email, make_person
from orderline.geo import CITIES, WAREHOUSES, Place, Warehouse, distance_km, nearest_warehouse

HOUR = timedelta(hours=1)
SHOP_TZ = ZoneInfo("America/New_York")

# Relative order volume by hour of day and day of week, in shop time.
HOURLY = (1.0, 0.6, 0.4, 0.3, 0.3, 0.5, 1, 2, 3, 4, 4.5, 5, 5.5, 5, 4.5, 4.5, 4.5, 5, 5.5, 6, 6, 5, 3.5, 2)
WEEKDAY = (1.15, 1.05, 1.0, 1.0, 1.05, 0.85, 0.9)
SEASON = {11: 1.25, 12: 1.4, 1: 0.85}

PROMISE_DAYS = {"standard": 5, "express": 2}
SHIPPING_CENTS = {"standard": 599, "express": 1499}
FREE_SHIPPING_CENTS = 5000
CARRIER_DAYS = {"Swiftline": 1.3, "Northpost": 1.9, "Parcelo": 2.6}
CARRIER_MIX = {
    "standard": (("Parcelo", 0.45), ("Northpost", 0.35), ("Swiftline", 0.20)),
    "express": (("Swiftline", 0.70), ("Northpost", 0.30)),
}
HEAVY_G = 10_000
SEGMENT_AT = ((10, "vip"), (3, "regular"))
SEGMENT_RANK = {"new": 0, "regular": 1, "vip": 2}


@dataclass(frozen=True)
class Knobs:
    orders_per_day: float = 160.0
    backfill_days: int = 60
    new_customer_share: float = 0.2
    overflow_share: float = 0.15
    express_share: float = 0.2
    abandon_share: float = 0.1
    cancel_share: float = 0.03
    promo_days_per_month: float = 2.0
    capacity_headroom: float = 1.3


@dataclass
class Customer:
    customer_id: int
    full_name: str
    lat: float
    lon: float
    segment: str
    paid_orders: int = 0


@dataclass
class Report:
    first_hour: datetime | None = None
    hours: int = 0
    placed: int = 0
    new_customers: int = 0
    steps: Counter = field(default_factory=Counter)

    def __str__(self) -> str:
        if not self.hours:
            return "Already up to date."
        steps = ", ".join(f"{n} {step}" for step, n in sorted(self.steps.items()))
        return (
            f"Simulated {self.hours} hours from {self.first_hour:%Y-%m-%d %H:%M} UTC: "
            f"{self.placed} orders placed, {self.new_customers} new customers; {steps or 'no steps'}."
        )


def floor_hour(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def poisson(rng: Random, mean: float) -> int:
    """Knuth's method; fine for the small hourly means here."""
    limit, k, p = math.exp(-mean), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def shop_date(t: datetime) -> date:
    return t.astimezone(SHOP_TZ).date()


def capacities(knobs: Knobs) -> dict[int, int]:
    """Orders a day each warehouse packs before a backlog builds: its expected share plus headroom."""
    total = sum(c.weight for c in CITIES)
    share = Counter()
    for city in CITIES:
        share[nearest_warehouse(city.lat, city.lon).warehouse_id] += city.weight / total
    spread = knobs.overflow_share / len(WAREHOUSES)
    return {
        w.warehouse_id: round(
            knobs.orders_per_day
            * ((1 - knobs.overflow_share) * share[w.warehouse_id] + spread)
            * knobs.capacity_headroom
        )
        for w in WAREHOUSES
    }


class Shop:
    def __init__(self, conn: psycopg.Connection, seed: int, knobs: Knobs):
        self.conn = conn
        self.seed = seed
        self.knobs = knobs
        self.capacity = capacities(knobs)
        self.warehouses = {w.warehouse_id: w for w in WAREHOUSES}
        self.customers: list[Customer] = []
        self.products: list[Product] = []

    # -- setup -------------------------------------------------------------

    def seed_reference(self, start: datetime) -> None:
        """Warehouses, the catalog and a starting customer base, once."""
        if self.conn.execute("select exists (select from shop.warehouses)").fetchone()[0]:
            return
        rng = Random(f"{self.seed}:reference")
        cur = self.conn.cursor()
        cur.executemany(
            "insert into shop.warehouses values (%s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    w.warehouse_id,
                    w.code,
                    w.place.city,
                    w.place.state,
                    w.place.lat,
                    w.place.lon,
                    self.capacity[w.warehouse_id],
                )
                for w in WAREHOUSES
            ],
        )
        cur.executemany(
            "insert into shop.products (sku, name, category, price_cents, weight_g, updated_at) "
            "values (%s, %s, %s, %s, %s, %s)",
            [(p.sku, p.name, p.category, p.price_cents, p.weight_g, start) for p in make_catalog(rng)],
        )
        starters = round(self.knobs.orders_per_day * 2)
        rows = []
        for i in range(starters):
            name, email = make_person(rng, f"s{i}")
            city = self._pick_city(rng)
            lat, lon = self._jitter(rng, city)
            segment = rng.choices(("new", "regular", "vip"), (0.5, 0.4, 0.1))[0]
            rows.append((email, name, city.city, city.state, lat, lon, segment, start, start))
        cur.executemany(
            "insert into shop.customers (email, full_name, city, state, lat, lon, segment, created_at, "
            "updated_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )

    def load(self) -> None:
        paid = dict(
            self.conn.execute(
                "select customer_id, count(*) from shop.orders where paid_at is not null group by 1"
            ).fetchall()
        )
        self.customers = [
            Customer(cid, name, lat, lon, segment, paid.get(cid, 0))
            for cid, name, lat, lon, segment in self.conn.execute(
                "select customer_id, full_name, lat, lon, segment from shop.customers order by customer_id"
            )
        ]
        self.products = [
            Product(*row)
            for row in self.conn.execute(
                "select product_id, sku, name, category, price_cents, weight_g, active "
                "from shop.products where active order by product_id"
            )
        ]

    # -- one hour ------------------------------------------------------------

    def simulate_hour(self, hour: datetime, report: Report) -> None:
        rng = Random(f"{self.seed}:{hour.isoformat()}")
        end = hour + HOUR
        backlog = dict(
            self.conn.execute(
                "select warehouse_id, count(*) from shop.orders "
                "where placed_at >= %s and placed_at < %s group by 1",
                (hour - timedelta(days=1), hour),
            ).fetchall()
        )
        writes: list[tuple[str, tuple]] = []
        self._advance(hour, end, rng, backlog, writes, report)
        self._place_orders(hour, rng, backlog, writes, report)
        self._churn(hour, rng, writes)
        with self.conn.pipeline():
            for sql, params in writes:
                self.conn.execute(sql, params)

    def _advance(self, hour, end, rng, backlog, writes, report) -> None:
        due = self.conn.execute(
            "select d.order_id, d.next_step, d.due_at, o.customer_id, o.warehouse_id, o.ship_method, "
            "o.carrier, o.weight_g, c.lat, c.lon "
            "from sim.due d join shop.orders o using (order_id) join shop.customers c using (customer_id) "
            "where d.due_at < %s order by d.due_at, d.order_id",
            (end,),
        ).fetchall()
        by_id = {c.customer_id: c for c in self.customers}
        for order_id, step, at, customer_id, warehouse_id, method, carrier, weight_g, lat, lon in due:
            at = max(at, hour)
            report.steps[step] += 1
            if step == "pay":
                self._pay(order_id, by_id[customer_id], warehouse_id, method, at, rng, backlog, writes)
            elif step == "abandon":
                writes.append(("delete from shop.orders where order_id = %s", (order_id,)))
                writes.append(("delete from sim.due where order_id = %s", (order_id,)))
            elif step == "cancel":
                self._set_status(writes, order_id, "cancelled", "cancelled_at", at)
                writes.append(("delete from sim.due where order_id = %s", (order_id,)))
            elif step == "pack":
                self._set_status(writes, order_id, "packed", "packed_at", at)
                self._schedule(writes, order_id, "ship", at + timedelta(hours=rng.uniform(1, 8)))
            elif step == "ship":
                self._set_status(writes, order_id, "shipped", "shipped_at", at)
                origin = self.warehouses[warehouse_id].place
                km = distance_km(origin.lat, origin.lon, lat, lon)
                arrive = at + timedelta(days=self._transit_days(carrier, method, km, weight_g, at, rng))
                self._schedule(writes, order_id, "deliver", self._delivery_day(arrive, carrier, rng))
            elif step == "deliver":
                self._set_status(writes, order_id, "delivered", "delivered_at", at)
                writes.append(("delete from sim.due where order_id = %s", (order_id,)))

    def _place_orders(self, hour, rng, backlog, writes, report) -> None:
        local = hour.astimezone(SHOP_TZ)
        mean = (
            self.knobs.orders_per_day
            * HOURLY[local.hour]
            / sum(HOURLY)
            * WEEKDAY[local.weekday()]
            * 7
            / sum(WEEKDAY)
            * SEASON.get(local.month, 1.0)
            * self._promo(local.date())
        )
        count = poisson(rng, mean)
        if not count:
            return
        times = sorted(hour + timedelta(seconds=rng.uniform(0, 3599)) for _ in range(count))

        # New customers first: their orders need the ids Postgres hands out.
        buyers: list[Customer | None] = [
            None if rng.random() < self.knobs.new_customer_share else rng.choice(self.customers)
            for _ in times
        ]
        fresh = [i for i, b in enumerate(buyers) if b is None]
        if fresh:
            rows = []
            for i in fresh:
                name, email = make_person(rng, f"{hour:%y%m%d%H}n{i}")
                city = self._pick_city(rng)
                lat, lon = self._jitter(rng, city)
                rows.append((email, name, city.city, city.state, lat, lon, times[i], times[i]))
            cur = self.conn.cursor()
            cur.executemany(
                "insert into shop.customers (email, full_name, city, state, lat, lon, segment, created_at, "
                "updated_at) values (%s, %s, %s, %s, %s, %s, 'new', %s, %s) returning customer_id",
                rows,
                returning=True,
            )
            ids = []
            while True:
                ids.append(cur.fetchone()[0])
                if not cur.nextset():
                    break
            for i, customer_id, row in zip(fresh, ids, rows, strict=True):
                customer = Customer(customer_id, row[1], row[4], row[5], "new")
                self.customers.append(customer)
                buyers[i] = customer
            report.new_customers += len(fresh)

        orders, lines = [], []
        for placed_at, buyer in zip(times, buyers, strict=True):
            warehouse = self._route(buyer, rng)
            method = "express" if rng.random() < self._express_share(buyer) else "standard"
            carrier = self._weighted(rng, CARRIER_MIX[method])
            items = self._basket(rng)
            subtotal = sum(p.price_cents * qty for p, qty in items)
            shipping = (
                0 if method == "standard" and subtotal >= FREE_SHIPPING_CENTS else SHIPPING_CENTS[method]
            )
            promised = shop_date(placed_at) + timedelta(days=PROMISE_DAYS[method])
            orders.append(
                (
                    buyer.customer_id,
                    warehouse.warehouse_id,
                    method,
                    carrier,
                    sum(q for _, q in items),
                    sum(p.weight_g * q for p, q in items),
                    subtotal + shipping,
                    placed_at,
                    promised,
                    placed_at,
                )
            )
            lines.append(items)
        cur = self.conn.cursor()
        cur.executemany(
            "insert into shop.orders (customer_id, warehouse_id, status, ship_method, carrier, item_count, "
            "weight_g, total_cents, placed_at, promised_on, updated_at) "
            "values (%s, %s, 'pending', %s, %s, %s, %s, %s, %s, %s, %s) returning order_id",
            orders,
            returning=True,
        )
        order_ids = []
        while True:
            order_ids.append(cur.fetchone()[0])
            if not cur.nextset():
                break
        cur.executemany(
            "insert into shop.order_items values (%s, %s, %s, %s, %s)",
            [
                (order_id, line_no, p.product_id, qty, p.price_cents)
                for order_id, items in zip(order_ids, lines, strict=True)
                for line_no, (p, qty) in enumerate(items, start=1)
            ],
        )
        report.placed += len(order_ids)
        for order_id, row, buyer in zip(order_ids, orders, buyers, strict=True):
            placed_at, warehouse_id, method = row[7], row[1], row[2]
            backlog[warehouse_id] = backlog.get(warehouse_id, 0) + 1
            if rng.random() < self.knobs.abandon_share:
                self._schedule(writes, order_id, "abandon", placed_at + timedelta(hours=2))
                continue
            pay_at = placed_at + timedelta(minutes=rng.uniform(1, 40))
            if pay_at < hour + HOUR:
                report.steps["pay"] += 1
                self._pay(order_id, buyer, warehouse_id, method, pay_at, rng, backlog, writes)
            else:
                self._schedule(writes, order_id, "pay", pay_at)

    def _churn(self, hour, rng, writes) -> None:
        """Customers move and change emails; prices drift; products come and go.

        These run after the hour's orders, so they're stamped at its last moment:
        stamping them at the top of the hour let a customer's updated_at go
        backwards when a payment earlier in the same hour had already bumped it.
        """
        at = hour + HOUR - timedelta(microseconds=1)
        if rng.random() < 0.05:
            customer = rng.choice(self.customers)
            city = self._pick_city(rng)
            customer.lat, customer.lon = self._jitter(rng, city)
            writes.append(
                (
                    "update shop.customers set city = %s, state = %s, lat = %s, lon = %s, updated_at = %s "
                    "where customer_id = %s",
                    (city.city, city.state, customer.lat, customer.lon, at, customer.customer_id),
                )
            )
        if rng.random() < 0.03:
            customer = rng.choice(self.customers)
            email = make_email(rng, customer.full_name, f"{hour:%y%m%d%H}e")
            writes.append(
                (
                    "update shop.customers set email = %s, updated_at = %s where customer_id = %s",
                    (email, at, customer.customer_id),
                )
            )
        if rng.random() < 0.1:
            product = rng.choice(self.products)
            change = rng.choice((-1, 1)) * rng.uniform(0.05, 0.15)
            product.price_cents = max(99, round(product.price_cents * (1 + change) / 100) * 100 - 1)
            writes.append(
                (
                    "update shop.products set price_cents = %s, updated_at = %s where product_id = %s",
                    (product.price_cents, at, product.product_id),
                )
            )
        if rng.random() < 0.004 and len(self.products) > 60:
            product = self.products.pop(rng.randrange(len(self.products)))
            writes.append(
                (
                    "update shop.products set active = false, updated_at = %s where product_id = %s",
                    (at, product.product_id),
                )
            )
        if rng.random() < 0.006:
            category = rng.choice(list(CATEGORIES))
            options = [
                p
                for p in make_catalog(Random(f"{self.seed}:{hour.isoformat()}:launch"), 1)
                if p.category == category
            ]
            product = rng.choice(options)
            product.sku = f"{category[:3].upper()}-L{hour:%y%m%d%H}"
            row = self.conn.execute(
                "insert into shop.products (sku, name, category, price_cents, weight_g, updated_at) "
                "values (%s, %s, %s, %s, %s, %s) returning product_id",
                (product.sku, product.name, product.category, product.price_cents, product.weight_g, at),
            ).fetchone()
            product.product_id = row[0]
            self.products.append(product)

    # -- the rules -----------------------------------------------------------

    def _pay(self, order_id, customer, warehouse_id, method, at, rng, backlog, writes) -> None:
        self._set_status(writes, order_id, "paid", "paid_at", at)
        customer.paid_orders += 1
        earned = next((seg for threshold, seg in SEGMENT_AT if customer.paid_orders >= threshold), "new")
        if SEGMENT_RANK[earned] > SEGMENT_RANK[customer.segment]:  # segments only go up
            customer.segment = earned
            writes.append(
                (
                    "update shop.customers set segment = %s, updated_at = %s where customer_id = %s",
                    (earned, at, customer.customer_id),
                )
            )
        if rng.random() < self.knobs.cancel_share:
            self._schedule(writes, order_id, "cancel", at + timedelta(hours=rng.uniform(1, 20)))
        else:
            self._schedule(writes, order_id, "pack", self._pack_at(at, warehouse_id, method, backlog, rng))

    def _pack_at(self, at, warehouse_id, method, backlog, rng) -> datetime:
        # Express jumps the queue: packed sooner and hit half as hard by a backlog.
        express = method == "express"
        hours = rng.uniform(1, 6) if express else rng.uniform(3, 18)
        load = backlog.get(warehouse_id, 0) / self.capacity[warehouse_id]
        if load > 1:
            hours += (load - 1) * (24 if express else 48) * rng.uniform(0.5, 1.5)
        packed = at + timedelta(hours=hours)
        local = packed.astimezone(SHOP_TZ)
        if local.weekday() == 6:  # no Sunday shift
            monday = (local + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
            packed = monday + timedelta(hours=rng.uniform(0, 4))
        return packed

    def _transit_days(self, carrier, method, km, weight_g, at, rng) -> float:
        if method == "express":
            days = max(0.8, CARRIER_DAYS[carrier] * 0.5 + km / 2500)
        else:
            days = CARRIER_DAYS[carrier] + km / 1100
        if weight_g > HEAVY_G:
            days += 0.8
        if at.astimezone(SHOP_TZ).month in (12, 1, 2) and rng.random() < 0.12:
            days += rng.uniform(1, 3)
        return days * math.exp(rng.gauss(0, 0.25))

    def _delivery_day(self, arrive: datetime, carrier: str, rng: Random) -> datetime:
        local = arrive.astimezone(SHOP_TZ)
        if local.weekday() == 6 and carrier != "Swiftline":
            monday = (local + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            return monday + timedelta(hours=rng.uniform(0, 8))
        return arrive

    def _route(self, customer: Customer, rng: Random) -> Warehouse:
        nearest = nearest_warehouse(customer.lat, customer.lon)
        if rng.random() < self.knobs.overflow_share:
            return rng.choice([w for w in WAREHOUSES if w is not nearest])
        return nearest

    def _express_share(self, customer: Customer) -> float:
        return self.knobs.express_share * (1.75 if customer.segment == "vip" else 1.0)

    def _basket(self, rng: Random) -> list[tuple[Product, int]]:
        lines = 1
        while lines < 5 and rng.random() < 0.45:
            lines += 1
        picked = rng.sample(self.products, lines)
        return [(p, 1 if rng.random() < 0.85 else rng.randint(2, 3)) for p in picked]

    def _promo(self, day: date) -> float:
        hit = Random(f"{self.seed}:promo:{day.isoformat()}").random()
        return 2.5 if hit < self.knobs.promo_days_per_month / 30 else 1.0

    @staticmethod
    def _pick_city(rng: Random) -> Place:
        return rng.choices(CITIES, weights=[c.weight for c in CITIES])[0]

    @staticmethod
    def _jitter(rng: Random, city: Place) -> tuple[float, float]:
        return round(city.lat + rng.uniform(-0.25, 0.25), 4), round(city.lon + rng.uniform(-0.25, 0.25), 4)

    @staticmethod
    def _weighted(rng: Random, options) -> str:
        names, weights = zip(*options, strict=True)
        return rng.choices(names, weights=weights)[0]

    @staticmethod
    def _set_status(writes, order_id, status, column, at) -> None:
        writes.append(
            (
                f"update shop.orders set status = %s, {column} = %s, updated_at = %s where order_id = %s",
                (status, at, at, order_id),
            )
        )

    @staticmethod
    def _schedule(writes, order_id, step, at) -> None:
        writes.append(
            (
                "insert into sim.due values (%s, %s, %s) "
                "on conflict (order_id) do update "
                "set next_step = excluded.next_step, due_at = excluded.due_at",
                (order_id, step, at),
            )
        )


def run(
    conn: psycopg.Connection,
    until: datetime,
    seed: int = 7,
    knobs: Knobs | None = None,
    start: datetime | None = None,
) -> Report:
    """Simulate every full hour from where the last run stopped up to `until`.

    On the very first run the shop opens at `start`, or `backfill_days` before `until`.
    """
    knobs = knobs or Knobs()
    until = floor_hour(until).astimezone(UTC)
    report = Report()
    shop = Shop(conn, seed, knobs)
    row = conn.execute("select next_hour from sim.clock").fetchone()
    # Always UTC: the hour seeds the random generator, so 01:00Z and 20:00-05 must not differ.
    hour = (row[0] if row else floor_hour(start or until - timedelta(days=knobs.backfill_days))).astimezone(
        UTC
    )
    if hour >= until:
        return report
    with conn.transaction():
        shop.seed_reference(hour)
    shop.load()
    report.first_hour = hour
    while hour < until:
        # One transaction per simulated hour: a crash loses at most an hour, and
        # the clock moves with the data so a rerun picks up exactly where it was.
        with conn.transaction():
            shop.simulate_hour(hour, report)
            conn.execute(
                "insert into sim.clock (next_hour) values (%s) "
                "on conflict (id) do update set next_hour = excluded.next_hour",
                (hour + HOUR,),
            )
        hour += HOUR
        report.hours += 1
    return report
