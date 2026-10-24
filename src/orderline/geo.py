"""Where customers live and where orders ship from."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Place:
    city: str
    state: str
    lat: float
    lon: float
    weight: float = 1.0  # relative share of customers


@dataclass(frozen=True)
class Warehouse:
    warehouse_id: int
    code: str
    place: Place


WAREHOUSES = (
    Warehouse(1, "EWR", Place("Newark", "NJ", 40.7357, -74.1724)),
    Warehouse(2, "ATL", Place("Atlanta", "GA", 33.7490, -84.3880)),
    Warehouse(3, "DFW", Place("Dallas", "TX", 32.7767, -96.7970)),
    Warehouse(4, "ONT", Place("Ontario", "CA", 34.0633, -117.6509)),
)

# Weights are rough metro sizes, so the East Coast and California dominate the
# way they would for a real US retailer.
CITIES = (
    Place("New York", "NY", 40.7128, -74.0060, 19.0),
    Place("Los Angeles", "CA", 34.0522, -118.2437, 13.0),
    Place("Chicago", "IL", 41.8781, -87.6298, 9.4),
    Place("Dallas", "TX", 32.7767, -96.7970, 7.6),
    Place("Houston", "TX", 29.7604, -95.3698, 7.1),
    Place("Washington", "DC", 38.9072, -77.0369, 6.3),
    Place("Philadelphia", "PA", 39.9526, -75.1652, 6.2),
    Place("Miami", "FL", 25.7617, -80.1918, 6.1),
    Place("Atlanta", "GA", 33.7490, -84.3880, 6.1),
    Place("Boston", "MA", 42.3601, -71.0589, 4.9),
    Place("Phoenix", "AZ", 33.4484, -112.0740, 4.9),
    Place("San Francisco", "CA", 37.7749, -122.4194, 4.7),
    Place("Riverside", "CA", 33.9533, -117.3962, 4.6),
    Place("Detroit", "MI", 42.3314, -83.0458, 4.3),
    Place("Seattle", "WA", 47.6062, -122.3321, 4.0),
    Place("Minneapolis", "MN", 44.9778, -93.2650, 3.7),
    Place("San Diego", "CA", 32.7157, -117.1611, 3.3),
    Place("Tampa", "FL", 27.9506, -82.4572, 3.2),
    Place("Denver", "CO", 39.7392, -104.9903, 3.0),
    Place("Baltimore", "MD", 39.2904, -76.6122, 2.8),
    Place("St. Louis", "MO", 38.6270, -90.1994, 2.8),
    Place("Orlando", "FL", 28.5383, -81.3792, 2.7),
    Place("Charlotte", "NC", 35.2271, -80.8431, 2.7),
    Place("San Antonio", "TX", 29.4241, -98.4936, 2.6),
    Place("Portland", "OR", 45.5152, -122.6784, 2.5),
    Place("Sacramento", "CA", 38.5816, -121.4944, 2.4),
    Place("Pittsburgh", "PA", 40.4406, -79.9959, 2.4),
    Place("Austin", "TX", 30.2672, -97.7431, 2.4),
    Place("Las Vegas", "NV", 36.1699, -115.1398, 2.3),
    Place("Cincinnati", "OH", 39.1031, -84.5120, 2.3),
    Place("Kansas City", "MO", 39.0997, -94.5786, 2.2),
    Place("Columbus", "OH", 39.9612, -82.9988, 2.1),
    Place("Indianapolis", "IN", 39.7684, -86.1581, 2.1),
    Place("Nashville", "TN", 36.1627, -86.7816, 2.0),
    Place("Salt Lake City", "UT", 40.7608, -111.8910, 1.3),
    Place("Albuquerque", "NM", 35.0844, -106.6504, 0.9),
    Place("Boise", "ID", 43.6150, -116.2023, 0.8),
    Place("Omaha", "NE", 41.2565, -95.9345, 0.9),
    Place("Billings", "MT", 45.7833, -108.5007, 0.2),
    Place("Fargo", "ND", 46.8772, -96.7898, 0.3),
)


def distance_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Great-circle distance, which is close enough to road distance for routing."""
    radius = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def nearest_warehouse(lat: float, lon: float) -> Warehouse:
    return min(WAREHOUSES, key=lambda w: distance_km(lat, lon, w.place.lat, w.place.lon))
