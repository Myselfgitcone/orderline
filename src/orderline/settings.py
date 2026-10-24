from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORDERLINE_", env_file=".env", extra="ignore")

    # The shop's Postgres. Neon in production; anything with wal_level=logical locally.
    database_url: str = "postgresql://postgres@localhost:5432/orderline"
    publication: str = "orderline"
    slot: str = "orderline"
    seed: int = 7

    # Capture
    debezium_image: str = "quay.io/debezium/server:3.6.2.Final"
    receiver_port: int = 8089
    spool_dir: Path = Path("out/spool")

    # Databricks
    databricks_host: str = ""
    databricks_token: SecretStr = SecretStr("")
    volume: str = "/Volumes/orderline/raw/cdc"
