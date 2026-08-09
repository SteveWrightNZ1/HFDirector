from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from weather_router.catalogue import Catalogue
from weather_router.db import Database
from weather_router.director import Director


class FakeQSSTV:
    def __init__(self):
        self.items = {}

    def send_file(self, request_id, path, profile):
        queue_id = f"tx-{len(self.items) + 1}"
        self.items[queue_id] = "queued"
        return {"ok": True, "id": queue_id, "state": "queued"}

    def tx_get(self, queue_id):
        return {"ok": True, "id": queue_id, "state": self.items[queue_id]}


class RouterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = Database(root / "router.sqlite3")
        self.db.initialise()
        self.catalogue = Catalogue(self.db, root / "assets")
        source = root / "metservice-maps" / "2026-08-09" / "rain-radar"
        source.mkdir(parents=True)
        Image.new("RGB", (32, 20), "navy").save(source / "radar-nz-20260809-0900-00.png")
        self.assertEqual(self.catalogue.ingest_tree(root / "metservice-maps")["imported"], 1)
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE schedules SET products='rain-radar',profile='1',enabled=1 WHERE id=1"
            )
        self.modem = FakeQSSTV()
        self.director = Director(self.db, self.catalogue, self.modem, "Pacific/Auckland")

    def tearDown(self):
        self.temp.cleanup()

    def test_run_is_durable_and_inhibited_by_default(self):
        run = self.director.create_run(1, "2026-08-09T09:00+1200")
        self.assertEqual(run["state"], "ready")
        self.assertIsNone(run["items"][0]["qsstv_queue_id"])
        with self.assertRaisesRegex(RuntimeError, "inhibited"):
            self.director.submit_run(run["id"])

    def test_submit_and_reconcile(self):
        run = self.director.create_run(1, "manual:test")
        self.director.set_inhibit(False)
        submitted = self.director.submit_run(run["id"])
        queue_id = submitted["items"][0]["qsstv_queue_id"]
        self.assertEqual(submitted["state"], "queued")
        self.modem.items[queue_id] = "sending"
        self.director.refresh_run(run["id"])
        self.assertEqual(self.director.run(run["id"])["state"], "sending")
        self.modem.items[queue_id] = "sent"
        self.director.refresh_run(run["id"])
        self.assertEqual(self.director.run(run["id"])["state"], "sent")

    def test_schedule_slot_is_idempotent(self):
        first = self.director.create_run(1, "2026-08-09T09:00+1200")
        second = self.director.create_run(1, "2026-08-09T09:00+1200")
        self.assertEqual(first["id"], second["id"])


if __name__ == "__main__":
    unittest.main()
