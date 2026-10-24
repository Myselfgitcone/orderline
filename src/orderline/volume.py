"""Pushing spool files into a Unity Catalog volume with the Databricks Files API.

Free Edition can't reach out to our database, so data goes in, not out.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}


class Volume:
    def __init__(self, host: str, token: str, path: str, client: httpx.Client | None = None):
        self.base = host.rstrip("/")
        if not self.base.startswith("http"):
            self.base = f"https://{self.base}"
        self.path = "/" + path.strip("/")
        self.client = client or httpx.Client(timeout=120)
        self.headers = {"Authorization": f"Bearer {token}"}

    def upload(self, local: Path, relative: str, attempts: int = 5) -> str:
        """PUT one file; never overwrites, so a rerun can't clobber what landed already."""
        target = f"{self.path}/{relative}"
        url = f"{self.base}/api/2.0/fs/files{target}"
        for attempt in range(1, attempts + 1):
            response = self.client.put(
                url,
                params={"overwrite": "false"},
                headers=self.headers | {"Content-Type": "application/octet-stream"},
                content=local.read_bytes(),
            )
            if response.status_code < 300:
                return target
            if response.status_code == 409:
                # Same run, same name, same bytes: an earlier attempt got through.
                return target
            if response.status_code in RETRY_STATUS and attempt < attempts:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"upload of {target} failed: {response.status_code} {response.text[:300]}")
        raise AssertionError("unreachable")

    def upload_all(self, root: Path, files: list[Path]) -> list[str]:
        return [self.upload(f, f.relative_to(root).as_posix()) for f in files]
