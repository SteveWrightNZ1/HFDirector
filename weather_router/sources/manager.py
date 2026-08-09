from __future__ import annotations

from .ecmwf import ECMWFOpenChartsSource
from .metservice import MetServiceSource


class SourceManager:
    def __init__(self, metservice: MetServiceSource, ecmwf: ECMWFOpenChartsSource):
        self.metservice = metservice
        self.ecmwf = ecmwf

    def refresh(self) -> dict:
        results = {
            "metservice": self.metservice.refresh(),
            "ecmwf": self.ecmwf.refresh(),
        }
        return {"ok": all(item.get("ok") for item in results.values()), "providers": results}

    def refresh_provider(self, provider: str, products=None) -> dict:
        if provider == "metservice":
            return self.metservice.refresh()
        if provider == "ecmwf":
            return self.ecmwf.refresh(products)
        return {"ok": False, "error": f"Source is not implemented: {provider}"}
