from __future__ import annotations

import re
import tempfile
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

from ..catalogue import Catalogue


API = "https://charts.ecmwf.int/opencharts-api/v1/products"
CHARTS = {
    "pressure-analysis": "medium-mslp-wind850",
    "rain-forecast": "medium-mslp-rain",
    "rain-accumulation": "medium-rain-acc",
    "surface-wind": "medium-wind-10m",
    "waves": "medium-swh-mwd",
    "swell": "medium-tssh-mwd",
}
PROJECTIONS = {
    "australasia": "opencharts_australasia",
    "equatorial-pacific": "opencharts_equatorial_pacific",
    "pacific": "opencharts_pacific",
}
VALID_TIME = re.compile(r"Valid time:\s+(.+?)(?:\s+\(|\s+Area\s*:)")


class ECMWFOpenChartsSource:
    def __init__(self, catalogue: Catalogue, projection: str = "australasia", width: int = 1000):
        self.catalogue = catalogue
        self.projection_name = projection
        self.projection = PROJECTIONS[projection]
        self.width = width
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "WeatherRouter/0.1 (OpenCharts CC-BY-4.0 client)"

    def products(self) -> list[dict]:
        return [
            {"product": logical, "source_product": source, "projection": self.projection}
            for logical, source in CHARTS.items()
        ]

    def fetch_one(self, product: str) -> dict:
        source_product = CHARTS[product]
        response = self.session.get(
            f"{API}/{source_product}/", params={"projection": self.projection}, timeout=45
        )
        if response.status_code == 429:
            return {"ok": False, "product": product, "error": "ECMWF rate limit", "retry": True}
        response.raise_for_status()
        document = response.json()
        chart = document["data"]
        meta = document["meta"]
        href = chart["link"]["href"]
        image_response = self.session.get(href, timeout=60)
        image_response.raise_for_status()
        with Image.open(BytesIO(image_response.content)) as opened:
            image = opened.convert("RGB")
            if image.width > self.width:
                height = round(image.height * self.width / image.width)
                image = image.resize((self.width, height), Image.Resampling.LANCZOS)
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temporary:
                path = Path(temporary.name)
            try:
                image.save(path, "JPEG", quality=82, optimize=True, progressive=True)
                description = chart["attributes"].get("description", "")
                match = VALID_TIME.search(description)
                valid_time = None
                if match:
                    try:
                        valid_time = datetime.strptime(match.group(1), "%a %d %b %Y %H UTC").replace(
                            tzinfo=timezone.utc
                        ).isoformat().replace("+00:00", "Z")
                    except ValueError:
                        pass
                asset_id = self.catalogue.ingest_file(
                    product,
                    path,
                    "ecmwf",
                    {
                        "source_product": source_product,
                        "projection": self.projection,
                        "description": description,
                        "licence": meta.get("licence", "CC-BY-4.0"),
                        "attribution": "ECMWF OpenCharts",
                        "source_url": href,
                        "api_url": response.url,
                    },
                    valid_time,
                )
            finally:
                path.unlink(missing_ok=True)
        return {"ok": True, "product": product, "asset_id": asset_id, "source_url": href}

    def refresh(self, products=None) -> dict:
        results = []
        for index, product in enumerate(products or CHARTS):
            if index:
                time.sleep(1)
            try:
                result = self.fetch_one(product)
            except Exception as exc:
                result = {"ok": False, "product": product, "error": str(exc)}
            results.append(result)
            if result.get("retry"):
                break
        return {
            "ok": bool(results) and all(result.get("ok") for result in results),
            "imported": sum(bool(result.get("asset_id")) for result in results),
            "results": results,
        }
