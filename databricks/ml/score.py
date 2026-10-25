"""Score every open order with the champion model.

`ml.late_risk` holds the latest score per open order; `ml.late_risk_history`
keeps every score ever given, so predictions can later be checked against what
actually happened, not just against a test set.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime

# Job tasks don't always run with their own folder on the path; the helpers live beside them.
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
except NameError:
    sys.path.insert(0, os.getcwd())

import mlflow
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

import common
import late_model

OPEN = "status IN ('paid', 'packed', 'shipped')"


def main() -> None:
    args = common.arguments()
    session = common.spark()
    name = common.model_name(args.catalog)
    mlflow.set_registry_uri("databricks-uc")

    try:
        version = MlflowClient().get_model_version_by_alias(name, "champion").version
    except MlflowException:
        print("No champion model yet; nothing to score. The train job sets one.")
        return
    model = mlflow.sklearn.load_model(f"models:/{name}@champion")

    orders = common.read_facts(session, args.catalog, OPEN)
    if orders.empty:
        print("No open orders.")
        return
    scored = orders[["order_id", "status", "carrier", "ship_method", "warehouse", "promised_on"]].copy()
    scored["p_late"] = model.predict_proba(late_model.features(orders))[:, 1].round(4)
    scored["risk_band"] = scored["p_late"].map(late_model.risk_band)
    scored["model_version"] = str(version)
    scored["scored_at"] = datetime.now(UTC)

    frame = session.createDataFrame(scored)
    frame.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(
        f"{args.catalog}.ml.late_risk"
    )
    frame.write.mode("append").option("mergeSchema", "true").saveAsTable(
        f"{args.catalog}.ml.late_risk_history"
    )
    bands = scored["risk_band"].value_counts().to_dict()
    print(f"Scored {len(scored)} open orders with version {version}: {bands}")


if __name__ == "__main__":
    main()
