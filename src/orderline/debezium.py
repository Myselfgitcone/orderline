"""Running Debezium Server in Docker for one sync.

The container is started, streams from the replication slot to the receiver,
and is stopped once the sync has what it needs. Offsets live inside the
container and die with it on purpose: with no stored offset, Debezium resumes
from the slot's confirmed position, so the slot is the only bookmark there is.
"""

from __future__ import annotations

import os
import subprocess
from importlib.resources import files

from psycopg.conninfo import conninfo_to_dict

CONFIG = files("orderline").joinpath("conf/debezium.properties")


def source_env(database_url: str, slot: str, publication: str) -> dict[str, str]:
    """Connection settings as the environment variables Debezium Server reads."""
    info = conninfo_to_dict(database_url)
    return {
        "DEBEZIUM_SOURCE_DATABASE_HOSTNAME": info.get("host", "localhost"),
        "DEBEZIUM_SOURCE_DATABASE_PORT": str(info.get("port", 5432)),
        "DEBEZIUM_SOURCE_DATABASE_USER": info.get("user", "postgres"),
        "DEBEZIUM_SOURCE_DATABASE_PASSWORD": info.get("password", ""),
        "DEBEZIUM_SOURCE_DATABASE_DBNAME": info.get("dbname", "postgres"),
        "DEBEZIUM_SOURCE_DATABASE_SSLMODE": info.get("sslmode", "disable"),
        "DEBEZIUM_SOURCE_SLOT_NAME": slot,
        "DEBEZIUM_SOURCE_PUBLICATION_NAME": publication,
    }


def start(image: str, name: str, database_url: str, slot: str, publication: str, receiver_url: str) -> None:
    env = source_env(database_url, slot, publication) | {"DEBEZIUM_SINK_HTTP_URL": receiver_url}
    command = ["docker", "run", "-d", "--rm", "--name", name, "--network", "host"]
    for key in env:
        command += ["-e", key]  # values come from the environment, so no password on the command line
    command += ["-v", f"{CONFIG}:/debezium/config/application.properties:ro", image]
    subprocess.run(command, check=True, capture_output=True, text=True, env=os.environ | env)


def running(name: str) -> bool:
    out = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name], capture_output=True, text=True
    )
    return out.stdout.strip() == "true"


def logs(name: str, tail: int = 80) -> str:
    out = subprocess.run(["docker", "logs", "--tail", str(tail), name], capture_output=True, text=True)
    return out.stdout + out.stderr


def stop(name: str) -> None:
    """Graceful stop: Debezium commits its offsets, which confirms the LSN to the slot."""
    subprocess.run(["docker", "stop", "-t", "60", name], check=True, capture_output=True)


def kill(name: str) -> None:
    """No commit, so the slot keeps everything and the next sync sends it again."""
    subprocess.run(["docker", "kill", name], capture_output=True)
