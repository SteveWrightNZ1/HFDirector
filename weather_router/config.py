from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    root: Path
    database: Path
    asset_root: Path
    import_root: Path
    qsstv_url: str
    bind_host: str
    bind_port: int
    timezone: str
    poll_seconds: float
    fetch_seconds: int

    @classmethod
    def from_env(cls) -> "Config":
        root = Path(os.environ.get("WEATHER_ROUTER_ROOT", Path.cwd())).resolve()
        return cls(
            root=root,
            database=Path(os.environ.get("WEATHER_ROUTER_DB", root / "var/router.sqlite3")),
            asset_root=Path(os.environ.get("WEATHER_ROUTER_ASSETS", root / "var/assets")),
            import_root=Path(os.environ.get("WEATHER_ROUTER_IMPORT", root.parent / "metservice-maps")),
            qsstv_url=os.environ.get("QSSTV_XMLRPC_URL", "http://127.0.0.1:7362"),
            bind_host=os.environ.get("WEATHER_ROUTER_HOST", "127.0.0.1"),
            bind_port=int(os.environ.get("WEATHER_ROUTER_PORT", "8080")),
            timezone=os.environ.get("WEATHER_ROUTER_TIMEZONE", "Pacific/Auckland"),
            poll_seconds=float(os.environ.get("WEATHER_ROUTER_POLL_SECONDS", "1")),
            fetch_seconds=int(os.environ.get("WEATHER_ROUTER_FETCH_SECONDS", "1800")),
        )
