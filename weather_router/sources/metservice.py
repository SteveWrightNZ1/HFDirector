from __future__ import annotations

import subprocess

from ..catalogue import Catalogue
from ..config import Config


class MetServiceSource:
    """Run the existing downloader, then catalogue its latest dated output."""

    def __init__(self, config: Config, catalogue: Catalogue):
        self.config = config
        self.catalogue = catalogue

    def refresh(self) -> dict:
        scraper = self.config.root.parent / "metservice_maps.py"
        if not scraper.exists():
            return {"ok": False, "error": f"Scraper not found: {scraper}"}
        completed = subprocess.run(
            ["python3", str(scraper)],
            cwd=str(self.config.root.parent),
            capture_output=True,
            text=True,
            timeout=300,
        )
        imported = self.catalogue.ingest_tree(self.config.import_root)
        return {
            "ok": completed.returncode == 0 and imported.get("ok", False),
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "imported": imported.get("imported", 0),
            "error": imported.get("error"),
        }
