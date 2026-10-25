"""Will this order arrive late? A model over gold.order_facts, using only what's
known at checkout.

Plain pandas and scikit-learn, no Spark or MLflow, so the same code trains in a
Databricks job and in the tests.

Two things this file is careful about:

- Censoring. An order placed yesterday that hasn't arrived isn't "on time", it's
  unknown. Only orders whose promise date has passed are used: by then the
  outcome is known whether or not the parcel has turned up (still out = late).
- Ground truth. The simulator planted the causes of lateness, so besides
  scoring well the model has to rank them the right way round. `signal_checks`
  asks it, one cause at a time, holding everything else fixed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

CATEGORICAL = ["ship_method", "carrier", "warehouse", "customer_segment"]
NUMERIC = [
    "item_count",
    "weight_g",
    "total_usd",
    "placed_dow",
    "placed_hour",
    "warehouse_load",
    "distance_km",
    "from_nearest_warehouse",
]
FEATURES = CATEGORICAL + NUMERIC
TEST_DAYS = 14


def labelled(facts: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Orders whose outcome is known on `as_of`, with a boolean `late` label.

    Cancelled orders never had a delivery to be late for. An order still out
    after its promise date is late even though it has no delivered_at yet.
    """
    promised = pd.to_datetime(facts["promised_on"]).dt.date
    known = facts[facts["cancelled_at"].isna() & (promised < as_of)].copy()
    known["late"] = known["is_late"].astype("boolean").fillna(True).astype(bool)
    return known


def features(facts: pd.DataFrame) -> pd.DataFrame:
    x = facts[FEATURES].copy()
    x["from_nearest_warehouse"] = x["from_nearest_warehouse"].astype(float)
    for column in NUMERIC:
        x[column] = pd.to_numeric(x[column], errors="coerce").astype(float)
    for column in CATEGORICAL:
        x[column] = x[column].astype(str)
    return x


def build_model(seed: int = 0) -> Pipeline:
    encode = ColumnTransformer(
        [
            (
                "categories",
                OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1),
                CATEGORICAL,
            )
        ],
        remainder="passthrough",
        verbose_feature_names_out=False,
    )
    boost = HistGradientBoostingClassifier(
        categorical_features=list(range(len(CATEGORICAL))),
        learning_rate=0.05,
        max_iter=400,
        max_leaf_nodes=31,
        l2_regularization=1.0,
        early_stopping=True,
        validation_fraction=0.15,
        random_state=seed,
    )
    return Pipeline([("encode", encode), ("boost", boost)])


def split_by_time(known: pd.DataFrame, test_days: int = TEST_DAYS) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Train on older orders, test on the most recent `test_days` of them. Never shuffled:
    the model is always graded on weeks it hasn't seen, as it would be in production."""
    placed = pd.to_datetime(known["placed_at"], utc=True)
    cutoff = placed.max() - pd.Timedelta(days=test_days)
    return known[placed < cutoff], known[placed >= cutoff]


def evaluate(probabilities: np.ndarray, truth: pd.Series) -> dict[str, float]:
    return {
        "auc": float(roc_auc_score(truth, probabilities)),
        "average_precision": float(average_precision_score(truth, probabilities)),
        "brier": float(brier_score_loss(truth, probabilities)),
        "late_share": float(truth.mean()),
        "rows": float(len(truth)),
    }


def baseline_probabilities(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    """What ops already knows without a model: the late rate per carrier and method."""
    rates = train.groupby(["carrier", "ship_method"])["late"].mean()
    keys = list(zip(test["carrier"], test["ship_method"], strict=True))
    return np.array([rates.get(k, train["late"].mean()) for k in keys])


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def _average_risk(model: Pipeline, x: pd.DataFrame, **fixed) -> float:
    """Mean predicted risk if every order in `x` had these values, all else unchanged."""
    what_if = x.copy()
    for column, value in fixed.items():
        what_if[column] = value
    return float(model.predict_proba(what_if)[:, 1].mean())


def signal_checks(model: Pipeline, x: pd.DataFrame) -> list[Check]:
    standard = x[x["ship_method"] == "standard"]
    risk = {c: _average_risk(model, standard, carrier=c) for c in ("Parcelo", "Northpost", "Swiftline")}
    # Overflow routing makes orders late by sending them further, so the planted
    # cause shows up as distance; the nearest-warehouse flag adds little on top.
    near, far = (_average_risk(model, x, distance_km=v) for v in (300.0, 3_000.0))
    calm, busy = (_average_risk(model, x, warehouse_load=v) for v in (0.6, 1.8))
    light, heavy = (_average_risk(model, x, weight_g=v) for v in (2_000.0, 15_000.0))
    return [
        Check(
            "carrier: Parcelo > Northpost > Swiftline",
            risk["Parcelo"] > risk["Northpost"] > risk["Swiftline"],
            ", ".join(f"{c} {p:.2f}" for c, p in risk.items()),
        ),
        Check("far from the warehouse is riskier", far > near, f"300 km {near:.2f}, 3,000 km {far:.2f}"),
        Check("busy warehouse is riskier", busy > calm, f"load 0.6 {calm:.2f}, load 1.8 {busy:.2f}"),
        Check("heavy parcels are riskier", heavy > light, f"2 kg {light:.2f}, 15 kg {heavy:.2f}"),
    ]


def importances(model: Pipeline, x: pd.DataFrame, y: pd.Series, seed: int = 0) -> dict[str, float]:
    """How much AUC drops when each feature is shuffled, biggest first."""
    result = permutation_importance(model, x, y, scoring="roc_auc", n_repeats=5, random_state=seed)
    pairs = sorted(zip(x.columns, result.importances_mean, strict=True), key=lambda p: -p[1])
    return {name: round(float(value), 4) for name, value in pairs}


@dataclass
class Trained:
    model: Pipeline
    as_of: date
    cutoff: str
    metrics: dict[str, float]
    baseline: dict[str, float]
    checks: list[Check]
    importances: dict[str, float] = field(default_factory=dict)
    x_test: pd.DataFrame | None = None
    y_test: pd.Series | None = None

    @property
    def checks_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def summary(self) -> str:
        lines = [
            f"as of {self.as_of}, test from {self.cutoff}: "
            f"AUC {self.metrics['auc']:.3f} vs {self.baseline['auc']:.3f} for carrier+method rates; "
            f"AP {self.metrics['average_precision']:.3f}, Brier {self.metrics['brier']:.3f}, "
            f"{self.metrics['rows']:.0f} test orders, {self.metrics['late_share']:.1%} late",
        ]
        lines += [f"  {'ok ' if c.passed else 'BAD'} {c.name}: {c.detail}" for c in self.checks]
        top = ", ".join(f"{k} {v:.3f}" for k, v in list(self.importances.items())[:6])
        lines.append(f"  top features (AUC drop when shuffled): {top}")
        return "\n".join(lines)


def train(facts: pd.DataFrame, as_of: date | None = None, seed: int = 0) -> Trained:
    if as_of is None:
        # The newest order is effectively "now" in the data.
        as_of = pd.to_datetime(facts["placed_at"], utc=True).max().date()
    known = labelled(facts, as_of)
    train_rows, test_rows = split_by_time(known)
    if train_rows["late"].nunique() < 2 or test_rows["late"].nunique() < 2:
        raise ValueError("not enough history with both outcomes to train on yet")

    model = build_model(seed).fit(features(train_rows), train_rows["late"])
    x_test, y_test = features(test_rows), test_rows["late"]
    cutoff = pd.to_datetime(test_rows["placed_at"], utc=True).min()
    return Trained(
        model=model,
        as_of=as_of,
        cutoff=(cutoff.date() if not pd.isna(cutoff) else as_of - timedelta(days=TEST_DAYS)).isoformat(),
        metrics=evaluate(model.predict_proba(x_test)[:, 1], y_test),
        baseline=evaluate(baseline_probabilities(train_rows, test_rows), y_test),
        checks=signal_checks(model, x_test),
        importances=importances(model, x_test, y_test, seed),
        x_test=x_test,
        y_test=y_test,
    )


def should_promote(
    new_auc: float, champion_auc: float | None, checks_passed: bool, tolerance: float = 0.002
) -> bool:
    """A challenger replaces the champion only if it gets the planted causes right
    and is at least as good on the same test orders (within noise)."""
    if not checks_passed:
        return False
    return champion_auc is None or new_auc >= champion_auc - tolerance


def risk_band(p: float) -> str:
    return "high" if p >= 0.5 else "medium" if p >= 0.25 else "low"
