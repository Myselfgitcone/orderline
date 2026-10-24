from datetime import UTC, datetime, timedelta
from random import Random

from orderline import simulate
from orderline.catalog import make_catalog, make_person
from orderline.geo import CITIES, WAREHOUSES, distance_km, nearest_warehouse


def test_distance_is_roughly_right():
    # Newark to Ontario, CA is about 3,900 km as the crow flies.
    newark, ontario = WAREHOUSES[0].place, WAREHOUSES[3].place
    assert 3800 < distance_km(newark.lat, newark.lon, ontario.lat, ontario.lon) < 4000
    assert distance_km(40.0, -74.0, 40.0, -74.0) == 0


def test_every_city_has_a_nearest_warehouse_and_all_warehouses_get_used():
    used = {nearest_warehouse(c.lat, c.lon).code for c in CITIES}
    assert used == {w.code for w in WAREHOUSES}
    assert nearest_warehouse(34.05, -118.24).code == "ONT"  # Los Angeles
    assert nearest_warehouse(40.71, -74.0).code == "EWR"  # New York


def test_capacity_covers_expected_volume_with_headroom():
    knobs = simulate.Knobs(orders_per_day=200, capacity_headroom=1.3)
    total = sum(simulate.capacities(knobs).values())
    assert abs(total - 200 * 1.3) <= len(WAREHOUSES)


def test_poisson_mean():
    rng = Random(1)
    draws = [simulate.poisson(rng, 6.0) for _ in range(20_000)]
    assert abs(sum(draws) / len(draws) - 6.0) < 0.1
    assert simulate.poisson(rng, 0.0) == 0


def test_catalog_is_unique_and_sane():
    products = make_catalog(Random(3))
    assert len({p.sku for p in products}) == len(products)
    assert all(p.price_cents > 0 and p.weight_g > 0 for p in products)
    assert make_catalog(Random(3)) == products  # deterministic


def test_people_get_distinct_emails_from_distinct_tokens():
    rng = Random(5)
    emails = {make_person(rng, f"t{i}")[1] for i in range(500)}
    assert len(emails) == 500


def test_floor_hour_and_shop_date():
    t = datetime(2026, 10, 24, 3, 59, 59, tzinfo=UTC)
    assert simulate.floor_hour(t) == datetime(2026, 10, 24, 3, tzinfo=UTC)
    # 03:59 UTC is still the evening before in New York.
    assert simulate.shop_date(t).isoformat() == "2026-10-23"
    assert simulate.shop_date(t + timedelta(hours=1)).isoformat() == "2026-10-24"
