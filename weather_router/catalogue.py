from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .db import Database, utcnow


PRODUCT_DIRS = {
    "pressure": None,
    "rain-radar": "rain-radar",
    "rain-5day": "rain-5day",
    "satellite-tasman-infrared": "satellite-tasman-infrared",
    "marine-high-seas": None,
}

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
STAMP = re.compile(r"(20\d{6})[-_](\d{4})")


def product_for(relative: Path) -> str | None:
    if not relative.parts:
        return None
    directory = relative.parts[0]
    stem = relative.stem
    if directory == "pressure":
        return stem
    if directory == "marine-high-seas":
        return "marine-high-seas-chart" if relative.suffix.lower() in IMAGE_SUFFIXES else f"marine-{stem}"
    return PRODUCT_DIRS.get(directory)


def source_time(path: Path) -> str | None:
    match = STAMP.search(path.name)
    if not match:
        return None
    try:
        value = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        return value.isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


class Catalogue:
    def __init__(self, db: Database, asset_root: Path):
        self.db = db
        self.asset_root = asset_root
        asset_root.mkdir(parents=True, exist_ok=True)

    def ingest_tree(self, source_root: Path) -> dict:
        if not source_root.exists():
            return {"ok": False, "error": f"Import directory does not exist: {source_root}"}
        dated = sorted((path for path in source_root.iterdir() if path.is_dir()), reverse=True)
        if not dated:
            return {"ok": False, "error": "No dated weather directories found"}
        imported = skipped = 0
        for source in dated[0].rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(dated[0])
            product = product_for(relative)
            if not product:
                skipped += 1
                continue
            if self.ingest_file(product, source):
                imported += 1
        return {"ok": True, "source": str(dated[0]), "imported": imported, "skipped": skipped}

    def ingest_file(self, product: str, source: Path) -> int:
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        suffix = source.suffix.lower()
        target_dir = self.asset_root / product
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{digest[:16]}{suffix}"
        if not target.exists():
            shutil.copy2(source, target)

        media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        width = height = None
        if suffix in IMAGE_SUFFIXES:
            try:
                with Image.open(target) as image:
                    image.verify()
                with Image.open(target) as image:
                    width, height = image.size
                    media_type = Image.MIME.get(image.format, media_type)
            except (UnidentifiedImageError, OSError):
                return 0

        with self.db.connect() as connection:
            existing = connection.execute("SELECT id FROM assets WHERE path=?", (str(target),)).fetchone()
            if existing:
                return 0
            cursor = connection.execute(
                """INSERT INTO assets
                   (product,path,media_type,size,sha256,observed_at,source_time,width,height,metadata_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    product,
                    str(target),
                    media_type,
                    len(content),
                    digest,
                    utcnow(),
                    source_time(source),
                    width,
                    height,
                    json.dumps({"original_name": source.name}),
                ),
            )
            return cursor.lastrowid

    def latest(self, product: str) -> dict | None:
        with self.db.connect() as connection:
            row = connection.execute(
                """SELECT * FROM assets WHERE product=?
                   ORDER BY COALESCE(source_time, observed_at) DESC, id DESC LIMIT 1""",
                (product,),
            ).fetchone()
            return dict(row) if row else None

    def products(self) -> list[dict]:
        with self.db.connect() as connection:
            return self.db.rows(
                connection.execute(
                    """SELECT a.* FROM assets a
                       JOIN (SELECT product,max(id) id FROM assets GROUP BY product) latest
                         ON latest.id=a.id ORDER BY a.product"""
                ).fetchall()
            )
