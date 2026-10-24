from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORDERLINE_", env_file=".env", extra="ignore")

    # The shop's Postgres. Neon in production; anything with wal_level=logical locally.
    database_url: str = "postgresql://postgres@localhost:5432/orderline"
    publication: str = "orderline"
    slot: str = "orderline"
    seed: int = 7
