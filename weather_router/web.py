from __future__ import annotations

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, url_for

from .catalogue import Catalogue
from .config import Config
from .db import Database, utcnow
from .director import Director
from .qsstv import QSSTVClient
from .sources.metservice import MetServiceSource


def create_app(config: Config | None = None) -> Flask:
    config = config or Config.from_env()
    db = Database(config.database)
    db.initialise()
    catalogue = Catalogue(db, config.asset_root)
    qsstv = QSSTVClient(config.qsstv_url)
    director = Director(db, catalogue, qsstv, config.timezone)
    source = MetServiceSource(config, catalogue)

    app = Flask(__name__)
    app.secret_key = "weather-router-local-operator"
    app.config.update(ROUTER_CONFIG=config, DB=db, CATALOGUE=catalogue, QSSTV=qsstv,
                      DIRECTOR=director, WEATHER_SOURCE=source)

    @app.get("/")
    def dashboard():
        try:
            modem = qsstv.status()
            modem_error = None
        except Exception as exc:
            modem = {"busy": False, "queued": 0}
            modem_error = str(exc)
        try:
            pending_bsr = qsstv.bsr("pending")
        except Exception:
            pending_bsr = []
        return render_template(
            "dashboard.html",
            inhibited=director.inhibited,
            modem=modem,
            modem_error=modem_error,
            products=catalogue.products(),
            schedules=director.schedules(),
            runs=director.runs(),
            pending_bsr=pending_bsr,
            timezone=config.timezone,
        )

    @app.post("/control/inhibit")
    def inhibit():
        director.set_inhibit(request.form.get("inhibited") == "1")
        flash("Transmission inhibited" if director.inhibited else "Transmission enabled")
        return redirect(url_for("dashboard"))

    @app.post("/catalogue/import")
    def import_catalogue():
        result = catalogue.ingest_tree(config.import_root)
        flash(f"Imported {result.get('imported', 0)} new assets" if result.get("ok") else result["error"])
        return redirect(url_for("dashboard"))

    @app.post("/catalogue/fetch")
    def fetch_weather():
        result = source.refresh()
        flash(
            f"Weather refreshed; imported {result.get('imported', 0)} new assets"
            if result.get("ok") else "Weather fetch completed with errors; see service log"
        )
        return redirect(url_for("dashboard"))

    @app.get("/assets/<int:asset_id>")
    def asset(asset_id: int):
        with db.connect() as connection:
            row = connection.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            abort(404)
        return send_file(row["path"], mimetype=row["media_type"], conditional=True)

    @app.post("/schedules/<int:schedule_id>")
    def save_schedule(schedule_id: int):
        try:
            director.save_schedule(schedule_id, request.form.to_dict())
            flash("Schedule saved")
        except (ValueError, KeyError) as exc:
            flash(str(exc))
        return redirect(url_for("dashboard"))

    @app.post("/schedules/<int:schedule_id>/run")
    def run_now(schedule_id: int):
        try:
            run = director.create_run(schedule_id)
            flash(f"Run {run['id']} created in state {run['state']}")
        except Exception as exc:
            flash(str(exc))
        return redirect(url_for("dashboard"))

    @app.post("/runs/<int:run_id>/submit")
    def submit_run(run_id: int):
        try:
            run = director.submit_run(run_id)
            flash(f"Run {run_id} is {run['state']}")
        except Exception as exc:
            flash(str(exc))
        return redirect(url_for("run_detail", run_id=run_id))

    @app.get("/runs/<int:run_id>")
    def run_detail(run_id: int):
        director.refresh_run(run_id)
        return render_template("run.html", run=director.run(run_id), inhibited=director.inhibited)

    @app.get("/bsr")
    def bsr_list():
        error = None
        try:
            requests = qsstv.bsr("")
        except Exception as exc:
            requests, error = [], str(exc)
        return render_template("bsr.html", requests=requests, error=error)

    @app.post("/bsr/<bsr_id>/approve")
    def approve_bsr(bsr_id: str):
        result = qsstv.approve_bsr(bsr_id)
        if result.get("ok"):
            with db.connect() as connection:
                connection.execute(
                    """INSERT INTO bsr_decisions(bsr_id,decision,note,decided_at) VALUES(?,?,?,?)
                       ON CONFLICT(bsr_id) DO UPDATE SET decision=excluded.decision,note=excluded.note,
                       decided_at=excluded.decided_at""",
                    (bsr_id, "approved", request.form.get("note", ""), utcnow()),
                )
        flash(result.get("state", result.get("message", "Approval requested")))
        return redirect(url_for("bsr_list"))

    @app.post("/bsr/<bsr_id>/reject")
    def reject_bsr(bsr_id: str):
        note = request.form.get("note", "operator rejected")
        result = qsstv.reject_bsr(bsr_id, note)
        if result.get("ok"):
            with db.connect() as connection:
                connection.execute(
                    """INSERT INTO bsr_decisions(bsr_id,decision,note,decided_at) VALUES(?,?,?,?)
                       ON CONFLICT(bsr_id) DO UPDATE SET decision=excluded.decision,note=excluded.note,
                       decided_at=excluded.decided_at""",
                    (bsr_id, "rejected", note, utcnow()),
                )
        flash(result.get("state", result.get("message", "Rejection requested")))
        return redirect(url_for("bsr_list"))

    @app.get("/api/status")
    def api_status():
        try:
            modem = qsstv.status()
        except Exception as exc:
            modem = {"ok": False, "message": str(exc)}
        return jsonify({"ok": True, "inhibited": director.inhibited, "modem": modem, "runs": director.runs(10)})

    return app
