"""Train the late-delivery model, register it, and promote it if it earns it.

Runs as a Databricks job task. Every run is logged to MLflow and registered in
Unity Catalog; the `champion` alias moves only when the new version gets the
planted causes right and does at least as well as the current champion on the
same, most recent test orders.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

# Job tasks don't always run with their own folder on the path; the helpers live beside them.
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
except NameError:
    sys.path.insert(0, os.getcwd())

import mlflow
import pandas as pd
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

import common
import late_model


def champion_auc(name: str, trained: late_model.Trained) -> tuple[float | None, str | None]:
    """The current champion re-scored on today's test orders, for a fair comparison."""
    client = MlflowClient()
    try:
        version = client.get_model_version_by_alias(name, "champion").version
    except MlflowException:
        return None, None
    champion = mlflow.sklearn.load_model(f"models:/{name}@champion")
    probabilities = champion.predict_proba(trained.x_test)[:, 1]
    return late_model.evaluate(probabilities, trained.y_test)["auc"], version


def main() -> None:
    args = common.arguments()
    session = common.spark()
    name = common.model_name(args.catalog)
    user = session.sql("SELECT current_user()").first()[0]

    mlflow.set_registry_uri("databricks-uc")
    mlflow.set_experiment(f"/Users/{user}/orderline-late-delivery")

    facts = common.read_facts(session, args.catalog)
    trained = late_model.train(facts)
    print(trained.summary())

    with mlflow.start_run(run_name=f"late-delivery {trained.as_of}") as run:
        mlflow.log_params(
            {
                "as_of": str(trained.as_of),
                "test_from": trained.cutoff,
                "features": ",".join(late_model.FEATURES),
            }
        )
        mlflow.log_metrics({f"test_{k}": v for k, v in trained.metrics.items()})
        mlflow.log_metrics({f"baseline_{k}": v for k, v in trained.baseline.items()})
        mlflow.log_metrics({f"check_{i}": float(c.passed) for i, c in enumerate(trained.checks)})
        mlflow.log_dict(
            {c.name: {"passed": c.passed, "detail": c.detail} for c in trained.checks}, "checks.json"
        )
        mlflow.log_dict(trained.importances, "importances.json")
        info = mlflow.sklearn.log_model(
            sk_model=trained.model,
            name="model",
            input_example=trained.x_test.head(5),
            registered_model_name=name,
        )
        version = str(info.registered_model_version)

    previous_auc, previous_version = champion_auc(name, trained)
    promoted = late_model.should_promote(trained.metrics["auc"], previous_auc, trained.checks_passed)
    if promoted:
        MlflowClient().set_registered_model_alias(name, "champion", version)
    verdict = "promoted to champion" if promoted else f"kept version {previous_version} as champion"
    print(f"version {version}: AUC {trained.metrics['auc']:.3f} vs champion {previous_auc}; {verdict}")

    # A plain table of every training run, for the dashboard and for anyone without MLflow open.
    row = pd.DataFrame(
        [
            {
                "trained_at": datetime.now(UTC),
                "as_of": str(trained.as_of),
                "test_from": trained.cutoff,
                "model_version": version,
                "mlflow_run_id": run.info.run_id,
                "auc": trained.metrics["auc"],
                "baseline_auc": trained.baseline["auc"],
                "average_precision": trained.metrics["average_precision"],
                "brier": trained.metrics["brier"],
                "late_share": trained.metrics["late_share"],
                "test_orders": int(trained.metrics["rows"]),
                "checks_passed": trained.checks_passed,
                "checks": json.dumps({c.name: c.detail for c in trained.checks}),
                "importances": json.dumps(trained.importances),
                "champion_before": previous_version,
                "champion_auc_before": previous_auc,
                "promoted": promoted,
            }
        ]
    )
    session.createDataFrame(row).write.mode("append").option("mergeSchema", "true").saveAsTable(
        f"{args.catalog}.ml.training_runs"
    )


if __name__ == "__main__":
    main()
