"""Command line for the shop side of Orderline."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer

from orderline import db, simulate
from orderline import sync as syncing
from orderline import verify as verifying
from orderline.settings import Settings

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command()
def setup() -> None:
    """Create the shop tables, the publication and the replication slot."""
    settings = Settings()
    with db.connect(settings.database_url) as conn:
        for line in db.setup(conn, settings.publication, settings.slot):
            typer.echo(line)


@app.command()
def tick(
    until: datetime = typer.Option(None, help="Simulate up to this time (UTC). Default: now."),
    backfill_days: int = typer.Option(60, help="History to build on the very first run."),
    orders_per_day: float = typer.Option(160.0),
) -> None:
    """Run the shop forward from where it last stopped."""
    settings = Settings()
    end = until.replace(tzinfo=UTC) if until else datetime.now(UTC)
    knobs = simulate.Knobs(orders_per_day=orders_per_day, backfill_days=backfill_days)
    with db.connect(settings.database_url) as conn:
        typer.echo(simulate.run(conn, end, settings.seed, knobs))


@app.command()
def stats() -> None:
    """A quick look at the shop."""
    settings = Settings()
    with db.connect(settings.database_url) as conn:
        for metric, value in db.stats(conn):
            typer.echo(f"{metric:>18}  {value}")


@app.command()
def sync(
    upload: bool = typer.Option(True, help="Push the batch to the Databricks volume."),
    timeout: float = typer.Option(900, help="Seconds to wait for Debezium to catch up."),
) -> None:
    """Drain the replication slot through Debezium into the Databricks volume."""
    settings = Settings()
    with db.connect(settings.database_url) as conn:
        report = syncing.run(conn, settings, upload=upload, timeout_s=timeout)
    typer.echo(report)
    if not report.slot_advanced:
        raise typer.Exit(1)


@app.command()
def verify(
    spool: Path = typer.Argument(Path("out/spool"), help="Folder of captured JSONL files."),
    allow_duplicates: bool = typer.Option(True, help="Resent events are harmless; fail on them if not."),
) -> None:
    """Replay captured events and check they rebuild the source tables exactly."""
    settings = Settings()
    with db.connect(settings.database_url) as conn:
        check = verifying.verify(conn, spool)
    typer.echo(f"{check.events} events replayed, {check.duplicates} duplicates skipped")
    for table, (rows, bad) in check.tables.items():
        typer.echo(f"{table:>12}  {rows} rows  {'ok' if not bad else f'{bad} mismatched'}")
    for example in check.examples:
        typer.echo(f"  {example}")
    if not check.ok or (check.duplicates and not allow_duplicates):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
