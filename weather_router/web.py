from __future__ import annotations

import sqlite3
import secrets

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .catalogue import Catalogue
from .config import Config
from .db import Database, utcnow
from .director import Director
from .qsstv import QSSTVClient
from .registry import PRODUCTS, SOURCES
from .sources.ecmwf import ECMWFOpenChartsSource
from .sources.manager import SourceManager
from .sources.metservice import MetServiceSource


def create_app(config: Config | None = None, qsstv=None, modem_supervisor=None) -> Flask:
    config = config or Config.from_env()
    db = Database(config.database)
    db.initialise()
    catalogue = Catalogue(db, config.asset_root)
    qsstv = qsstv or QSSTVClient(config.qsstv_url)
    director = Director(db, catalogue, qsstv, config.timezone)
    metservice = MetServiceSource(config, catalogue)
    ecmwf = ECMWFOpenChartsSource(catalogue)
    source = SourceManager(metservice, ecmwf)

    app = Flask(__name__)
    session_secret = db.setting("session_secret")
    if not session_secret:
        session_secret = secrets.token_hex(32)
        db.set_setting("session_secret", session_secret)
    app.secret_key = session_secret
    app.config.update(ROUTER_CONFIG=config, DB=db, CATALOGUE=catalogue, QSSTV=qsstv,
                      DIRECTOR=director, WEATHER_SOURCE=source, MODEM_SUPERVISOR=modem_supervisor)

    @app.before_request
    def require_login():
        if request.endpoint in {"login", "static"}:
            return None
        user_id = session.get("user_id")
        if user_id:
            with db.connect() as connection:
                row = connection.execute(
                    "SELECT * FROM operator_users WHERE id=? AND enabled=1", (user_id,)
                ).fetchone()
            if row:
                g.user = dict(row)
                return None
            session.clear()
        return redirect(url_for("login", next=request.full_path if request.method == "GET" else ""))

    @app.get("/login")
    @app.post("/login")
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip().lower()
            with db.connect() as connection:
                row = connection.execute(
                    "SELECT * FROM operator_users WHERE username=? AND enabled=1", (username,)
                ).fetchone()
            if row and check_password_hash(row["password_hash"], request.form.get("password", "")):
                session.clear()
                session["user_id"] = row["id"]
                destination = request.args.get("next", "")
                safe_destination = destination.startswith("/") and not destination.startswith("//")
                return redirect(destination if safe_destination else url_for("dashboard"))
            flash("Invalid username or password")
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def dashboard():
        supervised = modem_supervisor.state if modem_supervisor else None
        if supervised and supervised["name"] != "qsstv":
            modem, modem_error, pending_bsr = {"busy": False, "queued": 0}, None, []
        else:
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
            bsr_policy=director.bsr_policy,
            bsr_callsigns="\n".join(director.bsr_callsigns),
            modem=modem,
            modem_error=modem_error,
            products=catalogue.products(),
            schedules=director.schedules(),
            runs=director.runs(),
            pending_bsr=pending_bsr,
            timezone=config.timezone,
            source_registry=SOURCES,
            product_registry=PRODUCTS,
            modem_supervisor=supervised,
        )

    @app.get("/sources")
    def source_viewer():
        latest = catalogue.products()
        for item in latest:
            try:
                import json
                item["metadata"] = json.loads(item.get("metadata_json") or "{}")
            except ValueError:
                item["metadata"] = {}
        return render_template(
            "sources.html", sources=SOURCES, products=PRODUCTS, assets=latest,
            ecmwf_products=ecmwf.products(),
        )

    @app.get("/radios")
    def radios_page():
        with db.connect() as connection:
            radios = db.rows(connection.execute("SELECT * FROM radios ORDER BY name,id").fetchall())
        return render_template("radios.html", radios=radios)

    @app.post("/radios/<int:radio_id>")
    def save_radio(radio_id: int):
        values = request.form
        now = utcnow()
        data = (
            values.get("name", "").strip(), values.get("control_type", "rigctld"),
            values.get("control_endpoint", "").strip(), values.get("model", "").strip(),
            values.get("ptt_type", "serial"), values.get("ptt_device", "").strip(),
            1 if values.get("enabled") else 0, values.get("notes", "").strip(),
        )
        if not data[0]:
            flash("Radio name is required")
        else:
            with db.connect() as connection:
                if radio_id:
                    connection.execute(
                        """UPDATE radios SET name=?,control_type=?,control_endpoint=?,model=?,ptt_type=?,
                           ptt_device=?,enabled=?,notes=?,updated_at=? WHERE id=?""", (*data, now, radio_id)
                    )
                else:
                    connection.execute(
                        """INSERT INTO radios(name,control_type,control_endpoint,model,ptt_type,ptt_device,
                           enabled,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (*data, now, now),
                    )
            flash("Radio saved")
        return redirect(url_for("radios_page"))

    @app.get("/audio")
    def audio_page():
        with db.connect() as connection:
            interfaces = db.rows(connection.execute(
                """SELECT a.*,r.name radio_name FROM audio_interfaces a
                   LEFT JOIN radios r ON r.id=a.radio_id ORDER BY a.name,a.id"""
            ).fetchall())
            radios = db.rows(connection.execute("SELECT id,name FROM radios ORDER BY name").fetchall())
        return render_template("audio.html", interfaces=interfaces, radios=radios)

    @app.post("/audio/<int:interface_id>")
    def save_audio(interface_id: int):
        values = request.form
        try:
            capture = max(0, min(150, int(values.get("capture_gain", "100"))))
            playback = max(0, min(150, int(values.get("playback_gain", "100"))))
        except ValueError:
            flash("Audio gains must be numbers")
            return redirect(url_for("audio_page"))
        name, device = values.get("name", "").strip(), values.get("device", "").strip()
        if not name or not device:
            flash("Audio name and device are required")
            return redirect(url_for("audio_page"))
        radio_id = int(values["radio_id"]) if values.get("radio_id") else None
        data = (name, device, radio_id, capture, playback, 1 if values.get("enabled") else 0,
                values.get("notes", "").strip())
        now = utcnow()
        with db.connect() as connection:
            if interface_id:
                connection.execute(
                    """UPDATE audio_interfaces SET name=?,device=?,radio_id=?,capture_gain=?,playback_gain=?,
                       enabled=?,notes=?,updated_at=? WHERE id=?""", (*data, now, interface_id)
                )
            else:
                connection.execute(
                    """INSERT INTO audio_interfaces(name,device,radio_id,capture_gain,playback_gain,enabled,
                       notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""", (*data, now, now)
                )
        flash("Audio interface saved")
        return redirect(url_for("audio_page"))

    @app.get("/users")
    def users_page():
        if g.user["role"] != "administrator":
            abort(403)
        with db.connect() as connection:
            users = db.rows(connection.execute("SELECT * FROM operator_users ORDER BY username").fetchall())
        return render_template("users.html", users=users)

    @app.post("/users/<int:user_id>")
    def save_user(user_id: int):
        if g.user["role"] != "administrator":
            abort(403)
        values = request.form
        username = values.get("username", "").strip().lower()
        if not username:
            flash("Username is required")
            return redirect(url_for("users_page"))
        role = values.get("role", "operator")
        if role not in {"viewer", "operator", "administrator"}:
            role = "operator"
        password = values.get("password", "")
        data = (username, values.get("display_name", "").strip(),
                values.get("callsign", "").strip().upper(), role,
                1 if values.get("enabled") else 0)
        now = utcnow()
        try:
            with db.connect() as connection:
                if user_id:
                    connection.execute(
                        """UPDATE operator_users SET username=?,display_name=?,callsign=?,role=?,enabled=?,
                           updated_at=? WHERE id=?""", (*data, now, user_id)
                    )
                    if password:
                        connection.execute(
                            "UPDATE operator_users SET password_hash=?,updated_at=? WHERE id=?",
                            (generate_password_hash(password), now, user_id),
                        )
                else:
                    if not password:
                        flash("A password is required for a new user")
                        return redirect(url_for("users_page"))
                    connection.execute(
                        """INSERT INTO operator_users(username,display_name,callsign,role,enabled,password_hash,
                           created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                        (*data, generate_password_hash(password), now, now)
                    )
            flash("User saved")
        except sqlite3.IntegrityError:
            flash("That username is already in use")
        return redirect(url_for("users_page"))

    @app.get("/schedules")
    def schedules_page():
        return render_template(
            "schedules.html", schedules=director.schedules(), sources=SOURCES,
            products=PRODUCTS, timezone=config.timezone,
        )

    @app.post("/sources/<provider>/fetch")
    def fetch_source(provider: str):
        product = request.form.get("product") or None
        result = source.refresh_provider(provider, [product] if product else None)
        flash(
            f"{SOURCES.get(provider, {}).get('name', provider)} refreshed; "
            f"imported {result.get('imported', 0)} assets"
            if result.get("ok") else result.get("error", "Source refresh completed with errors")
        )
        return redirect(url_for("source_viewer"))

    @app.post("/control/inhibit")
    def inhibit():
        director.set_inhibit(request.form.get("inhibited") == "1")
        flash("Transmission inhibited" if director.inhibited else "Transmission enabled")
        return redirect(url_for("dashboard"))

    @app.post("/control/bsr-policy")
    def bsr_policy():
        try:
            director.set_bsr_policy(
                request.form.get("policy", "off"), request.form.get("callsigns", "")
            )
            flash(f"BSR/FIX policy set to {director.bsr_policy}")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for("dashboard"))

    @app.post("/catalogue/import")
    def import_catalogue():
        result = catalogue.ingest_tree(config.import_root)
        flash(f"Imported {result.get('imported', 0)} new assets" if result.get("ok") else result["error"])
        return redirect(url_for("dashboard"))

    @app.post("/catalogue/fetch")
    def fetch_weather():
        result = source.refresh_provider("metservice")
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
        return redirect(url_for("schedules_page"))

    @app.post("/schedules/<int:schedule_id>/run")
    def run_now(schedule_id: int):
        try:
            run = director.create_run(schedule_id)
            flash(f"Run {run['id']} created in state {run['state']}")
        except Exception as exc:
            flash(str(exc))
        return redirect(url_for("schedules_page"))

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
