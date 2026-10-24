"""The things the shop sells and the people who buy them, made up but plausible."""

from __future__ import annotations

from dataclasses import dataclass
from random import Random

# category -> (product nouns, price range in dollars, weight range in grams)
CATEGORIES: dict[str, tuple[tuple[str, ...], tuple[float, float], tuple[int, int]]] = {
    "apparel": (
        ("Rain Shell", "Fleece Pullover", "Merino Tee", "Hiking Pant", "Wool Socks"),
        (18, 160),
        (120, 900),
    ),
    "footwear": (("Trail Runner", "Hiking Boot", "Camp Sandal", "Approach Shoe"), (45, 210), (600, 1800)),
    "camping": (
        ("Two-Person Tent", "Sleeping Bag", "Sleeping Pad", "Camp Chair", "Headlamp"),
        (25, 420),
        (150, 4200),
    ),
    "kitchen": (
        ("Cast Iron Skillet", "Dutch Oven", "Pour-Over Kettle", "Knife Set", "Stand Mixer"),
        (22, 380),
        (500, 11000),
    ),
    "fitness": (
        ("Yoga Mat", "Kettlebell", "Adjustable Dumbbell", "Foam Roller", "Jump Rope"),
        (12, 340),
        (200, 20000),
    ),
    "home": (
        ("Floor Lamp", "Throw Blanket", "Air Purifier", "Desk Organizer", "Area Rug"),
        (15, 290),
        (300, 9000),
    ),
}

ADJECTIVES = ("Summit Ridge Basin Harbor Cedar Granite Prairie Alpine Canyon Delta").split()

FIRST_NAMES = (
    "James Mary Robert Patricia John Jennifer Michael Linda David Elizabeth William Barbara "
    "Richard Susan Joseph Jessica Thomas Sarah Carlos Karen Daniel Lisa Matthew Nancy Anthony "
    "Sandra Priya Ashley Wei Maria Kevin Emily Jorge Aisha Ravi Fatima Brian Michelle Hiro "
    "Grace"
).split()
LAST_NAMES = (
    "Smith Johnson Williams Brown Jones Garcia Miller Davis Rodriguez Martinez Hernandez "
    "Lopez Gonzalez Wilson Anderson Thomas Taylor Moore Jackson Martin Lee Perez Thompson "
    "White Harris Patel Nguyen Kim Chen Reddy"
).split()
EMAIL_DOMAINS = ("example.com", "example.net", "example.org")


@dataclass
class Product:
    product_id: int | None
    sku: str
    name: str
    category: str
    price_cents: int
    weight_g: int
    active: bool = True


def make_catalog(rng: Random, per_noun: int = 4) -> list[Product]:
    products = []
    for category, (nouns, (low, high), (light, heavy)) in CATEGORIES.items():
        for noun_index, noun in enumerate(nouns):
            for variant in range(per_noun):
                adjective = ADJECTIVES[(noun_index * per_noun + variant) % len(ADJECTIVES)]
                # Heavier variants of a thing cost more, which keeps price and weight correlated.
                share = rng.random()
                products.append(
                    Product(
                        product_id=None,
                        sku=f"{category[:3].upper()}-{noun_index:02d}{variant:02d}",
                        name=f"{adjective} {noun}",
                        category=category,
                        price_cents=round((low + share * (high - low)) * 100) - 1,
                        weight_g=round(light + (0.6 * share + 0.4 * rng.random()) * (heavy - light)),
                    )
                )
    return products


def make_person(rng: Random, token: str) -> tuple[str, str]:
    """A name and an email; `token` must be unique across the run to keep emails unique."""
    first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
    return f"{first} {last}", make_email(rng, f"{first} {last}", token)


def make_email(rng: Random, full_name: str, token: str) -> str:
    first, last = full_name.lower().split(" ", 1)
    return f"{first}.{last}.{token}@{rng.choice(EMAIL_DOMAINS)}"
