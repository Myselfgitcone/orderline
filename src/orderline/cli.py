"""Command line for the shop side of Orderline."""

from __future__ import annotations

from datetime import UTC, datetime

import typer

from orderline import db, simulate
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


if __name__ == "__main__":
    app()
