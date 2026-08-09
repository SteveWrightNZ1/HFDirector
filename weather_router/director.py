from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .catalogue import Catalogue
from .db import Database, utcnow
from .qsstv import QSSTVClient

LOG = logging.getLogger(__name__)
TERMINAL = {"sent", "failed", "cancelled", "aborted", "invalidated"}


class Director:
    def __init__(self, db: Database, catalogue: Catalogue, qsstv: QSSTVClient, timezone: str):
        self.db = db
        self.catalogue = catalogue
        self.qsstv = qsstv
        self.zone = ZoneInfo(timezone)
        self._lock = threading.RLock()

    @property
    def inhibited(self) -> bool:
        return self.db.setting("tx_inhibit", "1") != "0"

    def set_inhibit(self, inhibited: bool) -> None:
        self.db.set_setting("tx_inhibit", "1" if inhibited else "0")

    @property
    def bsr_policy(self) -> str:
        value = self.db.setting("bsr_policy", "off")
        return value if value in {"off", "on", "whitelist", "blacklist"} else "off"

    @property
    def bsr_callsigns(self) -> list[str]:
        raw = self.db.setting("bsr_callsigns", "")
        return sorted({part.strip().upper() for part in raw.replace(",", "\n").splitlines() if part.strip()})

    def set_bsr_policy(self, policy: str, callsigns: str) -> None:
        if policy not in {"off", "on", "whitelist", "blacklist"}:
            raise ValueError("Unknown BSR/FIX policy")
        normalised = "\n".join(sorted({
            part.strip().upper() for part in callsigns.replace(",", "\n").splitlines() if part.strip()
        }))
        self.db.set_setting("bsr_policy", policy)
        self.db.set_setting("bsr_callsigns", normalised)

    def reconcile_bsr_policy(self) -> None:
        """Approve matching pending BSRs; never act while TX is inhibited."""
        policy = self.bsr_policy
        if self.inhibited or policy == "off":
            return
        listed = set(self.bsr_callsigns)
        for request in self.qsstv.bsr("pending"):
            callsign = request.get("callsign", "").strip().upper()
            approve = (
                policy == "on"
                or (policy == "whitelist" and callsign in listed)
                or (policy == "blacklist" and callsign not in listed)
            )
            if not approve:
                continue
            result = self.qsstv.approve_bsr(request["id"])
            if result.get("ok"):
                with self.db.connect() as connection:
                    connection.execute(
                        """INSERT INTO bsr_decisions(bsr_id,decision,note,decided_at) VALUES(?,?,?,?)
                           ON CONFLICT(bsr_id) DO UPDATE SET decision=excluded.decision,note=excluded.note,
                           decided_at=excluded.decided_at""",
                        (request["id"], "approved", f"automatic:{policy}", utcnow()),
                    )

    def schedules(self) -> list[dict]:
        with self.db.connect() as connection:
            return self.db.rows(connection.execute("SELECT * FROM schedules ORDER BY id").fetchall())

    def save_schedule(self, schedule_id: int, values: dict) -> None:
        local_time = values["local_time"].strip()
        datetime.strptime(local_time, "%H:%M")
        products = ",".join(part.strip() for part in values["products"].split(",") if part.strip())
        if not products:
            raise ValueError("At least one product is required")
        with self.db.connect() as connection:
            connection.execute(
                """UPDATE schedules SET name=?,enabled=?,local_time=?,weekdays=?,products=?,profile=?,updated_at=?
                   WHERE id=?""",
                (
                    values["name"].strip(),
                    1 if values.get("enabled") else 0,
                    local_time,
                    values.get("weekdays", "0,1,2,3,4,5,6"),
                    products,
                    values.get("profile", "1").strip(),
                    utcnow(),
                    schedule_id,
                ),
            )

    def create_run(self, schedule_id: int, slot: str | None = None) -> dict:
        with self._lock, self.db.connect() as connection:
            schedule = connection.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
            if not schedule:
                raise ValueError("Schedule not found")
            slot = slot or f"manual:{utcnow()}"
            existing = connection.execute(
                "SELECT * FROM broadcast_runs WHERE schedule_id=? AND slot=?", (schedule_id, slot)
            ).fetchone()
            if existing:
                return dict(existing)
            now = utcnow()
            cursor = connection.execute(
                """INSERT INTO broadcast_runs(schedule_id,slot,state,created_at,updated_at)
                   VALUES(?,?,?,?,?)""",
                (schedule_id, slot, "planning", now, now),
            )
            run_id = cursor.lastrowid
            missing = []
            for position, product in enumerate(schedule["products"].split(","), 1):
                asset = connection.execute(
                    """SELECT * FROM assets WHERE product=?
                       ORDER BY COALESCE(source_time,observed_at) DESC,id DESC LIMIT 1""",
                    (product,),
                ).fetchone()
                if not asset:
                    missing.append(product)
                    continue
                connection.execute(
                    """INSERT INTO broadcast_items
                       (run_id,position,asset_id,request_id,state,updated_at)
                       VALUES(?,?,?,?,?,?)""",
                    (run_id, position, asset["id"], f"weather-run-{run_id}-{position}", "ready", now),
                )
            state = "failed" if missing else "ready"
            reason = "Missing products: " + ", ".join(missing) if missing else None
            connection.execute(
                "UPDATE broadcast_runs SET state=?,reason=?,updated_at=? WHERE id=?",
                (state, reason, utcnow(), run_id),
            )
        run = self.run(run_id)
        if not self.inhibited and state == "ready":
            self.submit_run(run_id)
            run = self.run(run_id)
        return run

    def submit_run(self, run_id: int) -> dict:
        with self._lock:
            if self.inhibited:
                raise RuntimeError("Transmission is inhibited")
            with self.db.connect() as connection:
                run = connection.execute(
                    """SELECT r.*,s.profile FROM broadcast_runs r
                       JOIN schedules s ON s.id=r.schedule_id WHERE r.id=?""",
                    (run_id,),
                ).fetchone()
                if not run or run["state"] not in {"ready", "submitting", "queued", "sending"}:
                    raise RuntimeError("Run is not ready for submission")
                items = connection.execute(
                    """SELECT i.*,a.path FROM broadcast_items i JOIN assets a ON a.id=i.asset_id
                       WHERE i.run_id=? ORDER BY i.position""",
                    (run_id,),
                ).fetchall()
                connection.execute(
                    "UPDATE broadcast_runs SET state='submitting',updated_at=? WHERE id=?",
                    (utcnow(), run_id),
                )
            for item in items:
                if item["qsstv_queue_id"]:
                    continue
                result = self.qsstv.send_file(item["request_id"], item["path"], run["profile"])
                with self.db.connect() as connection:
                    if result.get("ok"):
                        connection.execute(
                            """UPDATE broadcast_items SET qsstv_queue_id=?,state=?,detail=NULL,updated_at=?
                               WHERE id=?""",
                            (result["id"], result["state"], utcnow(), item["id"]),
                        )
                    else:
                        connection.execute(
                            "UPDATE broadcast_items SET state='failed',detail=?,updated_at=? WHERE id=?",
                            (result.get("message", "QSSTV rejected request"), utcnow(), item["id"]),
                        )
            self.refresh_run(run_id)
            return self.run(run_id)

    def refresh_run(self, run_id: int) -> None:
        with self._lock, self.db.connect() as connection:
            items = connection.execute(
                "SELECT * FROM broadcast_items WHERE run_id=? ORDER BY position", (run_id,)
            ).fetchall()
            for item in items:
                if not item["qsstv_queue_id"] or item["state"] in TERMINAL:
                    continue
                try:
                    remote = self.qsstv.tx_get(item["qsstv_queue_id"])
                    if remote.get("ok"):
                        connection.execute(
                            "UPDATE broadcast_items SET state=?,detail=NULL,updated_at=? WHERE id=?",
                            (remote["state"], utcnow(), item["id"]),
                        )
                except OSError as exc:
                    LOG.warning("QSSTV status failed: %s", exc)
            states = [row[0] for row in connection.execute(
                "SELECT state FROM broadcast_items WHERE run_id=?", (run_id,)
            ).fetchall()]
            if states and all(state == "sent" for state in states):
                state = "sent"
            elif any(state == "sending" for state in states):
                state = "sending"
            elif any(state == "failed" for state in states):
                state = "failed"
            elif states and all(state in TERMINAL for state in states):
                state = "complete"
            elif any(state == "queued" for state in states):
                state = "queued"
            else:
                state = "ready"
            connection.execute(
                "UPDATE broadcast_runs SET state=?,updated_at=? WHERE id=?", (state, utcnow(), run_id)
            )

    def refresh_active(self) -> None:
        with self.db.connect() as connection:
            ids = [row[0] for row in connection.execute(
                "SELECT id FROM broadcast_runs WHERE state IN ('submitting','queued','sending')"
            ).fetchall()]
        for run_id in ids:
            self.refresh_run(run_id)

    def due_schedules(self) -> None:
        local = datetime.now(self.zone)
        minute = local.strftime("%H:%M")
        slot = local.strftime("%Y-%m-%dT%H:%M%z")
        for schedule in self.schedules():
            weekdays = {int(day) for day in schedule["weekdays"].split(",") if day}
            if not schedule["enabled"] or local.weekday() not in weekdays or schedule["local_time"] != minute:
                continue
            try:
                self.create_run(schedule["id"], slot)
            except Exception:
                LOG.exception("Could not create scheduled run %s", schedule["id"])

    def run(self, run_id: int) -> dict:
        with self.db.connect() as connection:
            row = connection.execute(
                """SELECT r.*,s.name schedule_name,s.profile FROM broadcast_runs r
                   LEFT JOIN schedules s ON s.id=r.schedule_id WHERE r.id=?""", (run_id,)
            ).fetchone()
            if not row:
                raise ValueError("Run not found")
            result = dict(row)
            result["items"] = self.db.rows(connection.execute(
                """SELECT i.*,a.product,a.path,a.media_type,a.size FROM broadcast_items i
                   JOIN assets a ON a.id=i.asset_id WHERE i.run_id=? ORDER BY i.position""",
                (run_id,),
            ).fetchall())
            return result

    def runs(self, limit: int = 30) -> list[dict]:
        with self.db.connect() as connection:
            return self.db.rows(connection.execute(
                """SELECT r.*,s.name schedule_name FROM broadcast_runs r
                   LEFT JOIN schedules s ON s.id=r.schedule_id ORDER BY r.id DESC LIMIT ?""",
                (limit,),
            ).fetchall())


class Scheduler(threading.Thread):
    def __init__(self, director: Director, poll_seconds: float, weather_source=None, fetch_seconds=1800):
        super().__init__(name="weather-router-scheduler", daemon=True)
        self.director = director
        self.poll_seconds = poll_seconds
        self.weather_source = weather_source
        self.fetch_seconds = fetch_seconds
        self.next_fetch = time.monotonic() + 5
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.wait(self.poll_seconds):
            try:
                self.director.refresh_active()
                self.director.due_schedules()
                self.director.reconcile_bsr_policy()
                if self.weather_source and time.monotonic() >= self.next_fetch:
                    result = self.weather_source.refresh()
                    if not result.get("ok"):
                        LOG.warning("Weather refresh failed: %s", result.get("error") or result.get("stderr"))
                    self.next_fetch = time.monotonic() + self.fetch_seconds
            except Exception:
                LOG.exception("Director reconciliation failed")
