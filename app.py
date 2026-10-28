import os
import threading
import time
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, redirect, url_for, session, abort

load_dotenv()

import database as db
from hardware import Hardware

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "")
if len(app.secret_key) < 32 or app.secret_key.startswith("CHANGE_TO_"):
    raise RuntimeError("Set FLASK_SECRET_KEY to a unique random secret of at least 32 characters in .env")
if os.getenv("WEB_PASSWORD", "").startswith("CHANGE_") or len(os.getenv("WEB_PASSWORD", "")) < 14:
    raise RuntimeError("Set a strong WEB_PASSWORD (at least 14 characters) in .env before starting")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=False)

hardware = None
worker_thread = None
stop_event = threading.Event()
state_lock = threading.RLock()
state = {"soil": None, "flow": {"flow_lpm": 0.0, "total_liters": 0.0}, "last_error": None, "last_saved": 0.0, "cycle_liters": 0.0, "event_source": None, "event_id": None, "last_control_reason": "startup"}


def authenticated(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Login required"}), 401
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def parse_hhmm(value):
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def schedule_active(now=None):
    if os.getenv("AUTO_SCHEDULE_ENABLED", "false").lower() not in ("true", "1", "yes"):
        return False
    now = now or datetime.now()
    start = parse_hhmm(os.getenv("AUTO_SCHEDULE_START", "06:00"))
    end = parse_hhmm(os.getenv("AUTO_SCHEDULE_END", "06:05"))
    if not start or not end or start == end:
        return False
    current = now.time().replace(tzinfo=None)
    return start <= current < end if start < end else (current >= start or current < end)


def set_valve(on, source, note=None):
    """Single path for valve transitions and event logging."""
    desired = bool(on)
    current = hardware.valve_status == "ON"
    if current == desired:
        return
    if desired:
        hardware.valve_on()
        with state_lock:
            state["cycle_liters"] = 0.0
            state["event_source"] = source
            state["event_id"] = None
        try:
            event_id = db.start_event(source, note)
            with state_lock:
                state["event_id"] = event_id
        except Exception:
            hardware.valve_off()
            raise
    else:
        hardware.valve_off()
        try:
            db.end_event(source, note)
        finally:
            with state_lock:
                state["event_source"] = None
                state["event_id"] = None


def control_step():
    status = db.get_status()
    if not status:
        return
    mode = status["control_mode"]
    if mode != "automatic":
        return
    with state_lock:
        soil = state["soil"]
        flow = dict(state["flow"])
        cycle_liters = float(state["cycle_liters"])
    if not soil:
        return
    threshold = float(status["moisture_threshold"])
    hysteresis = float(os.getenv("SOIL_HYSTERESIS_PERCENT", "5"))
    target = max(0.05, float(os.getenv("AUTO_TARGET_LITERS", "1.0")))
    moisture = float(soil["moisture_percent"])
    scheduled = schedule_active()
    reason = ""
    if hardware.valve_status == "OFF":
        if moisture < threshold or scheduled:
            reason = "soil below threshold" if moisture < threshold else "scheduled irrigation"
            set_valve(True, "automatic", reason)
            with state_lock:
                state["last_control_reason"] = reason
    else:
        if cycle_liters >= target:
            reason = f"volume target reached ({target:.2f} L)"
            set_valve(False, "automatic", reason)
        elif not scheduled and moisture >= threshold + hysteresis:
            reason = "soil moisture recovered"
            set_valve(False, "automatic", reason)


def worker():
    poll = max(1.0, float(os.getenv("SENSOR_POLL_SECONDS", "2")))
    db_interval = max(poll, float(os.getenv("DB_WRITE_SECONDS", "10")))
    last_db = 0.0
    last_tick = time.monotonic()
    while not stop_event.is_set():
        now = time.monotonic()
        elapsed = max(0.1, now - last_tick)
        last_tick = now
        try:
            soil = hardware.read_soil()
            flow = hardware.sample_flow(elapsed)
            with state_lock:
                state["soil"] = soil
                state["flow"] = flow
                state["cycle_liters"] += float(flow["flow_lpm"]) * elapsed / 60.0
                state["last_error"] = None
            if hardware.flow_is_stale():
                set_valve(False, "automatic", "FAIL-SAFE: no flow pulses detected; valve closed")
                with state_lock:
                    state["last_error"] = "No flow pulses detected while valve was ON; valve closed for safety. Check water supply/sensor calibration."
            if now - last_db >= db_interval:
                status_text = "normal"
                db.save_sensor(soil["moisture_percent"], status_text)
                db.save_flow(flow["flow_lpm"], flow["total_liters"])
                last_db = now
                with state_lock:
                    state["last_saved"] = time.time()
            control_step()
        except Exception as exc:
            with state_lock:
                state["last_error"] = f"{type(exc).__name__}: {exc}"
            # Fail safe: sensor/DB/control errors must never leave a valve energized.
            try:
                set_valve(False, "automatic", f"FAIL-SAFE: {type(exc).__name__}")
            except Exception:
                hardware.valve_off()
        stop_event.wait(poll)


@app.get("/login")
def login():
    return render_template("login.html", error=None)

@app.post("/login")
def login_post():
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if username == os.getenv("WEB_USERNAME", "admin") and password == os.getenv("WEB_PASSWORD", ""):
        session.clear()
        session["logged_in"] = True
        session["csrf"] = os.urandom(24).hex()
        return redirect(url_for("index"))
    return render_template("login.html", error="Username or password is incorrect."), 401

@app.post("/logout")
@authenticated
def logout():
    if request.form.get("csrf") != session.get("csrf"):
        abort(400)
    session.clear()
    return redirect(url_for("login"))

@app.get("/")
@authenticated
def index():
    return render_template("index.html", csrf=session.get("csrf", ""))

@app.get("/api/data")
@authenticated
def api_data():
    try:
        data = db.dashboard_data()
        status = db.get_status()
        with state_lock:
            live = {"soil": state["soil"], "flow": state["flow"], "last_error": state["last_error"], "last_saved": state["last_saved"], "cycle_liters": round(state["cycle_liters"], 3), "last_control_reason": state["last_control_reason"]}
        return jsonify({"ok": True, "status": status, "live": live, "history": data, "hardware_mode": "simulation" if hardware and hardware.simulation else "real"})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Database/API error: {type(exc).__name__}: {exc}"}), 503

@app.post("/api/control")
@authenticated
def api_control():
    payload = request.get_json(silent=True) or {}
    if payload.get("csrf") != session.get("csrf"):
        return jsonify({"ok": False, "error": "Invalid CSRF token"}), 400
    action = payload.get("action")
    try:
        status = db.get_status()
        if action == "mode":
            mode = payload.get("mode")
            if mode not in ("manual", "automatic"):
                return jsonify({"ok": False, "error": "Mode must be manual or automatic"}), 400
            if mode == "automatic" and state.get("soil") is None:
                return jsonify({"ok": False, "error": "Waiting for a valid sensor reading"}), 409
            db.update_status(control_mode=mode)
            if mode == "manual":
                set_valve(False, "manual", "Switched to manual mode; valve set OFF")
        elif action in ("on", "off"):
            if status and status["control_mode"] != "manual":
                return jsonify({"ok": False, "error": "Switch to manual mode before using manual valve controls"}), 409
            if action == "on":
                set_valve(True, "manual", "Manual ON from dashboard")
            else:
                set_valve(False, "manual", "Manual OFF from dashboard")
        elif action == "threshold":
            value = float(payload.get("value"))
            if not 5 <= value <= 80:
                return jsonify({"ok": False, "error": "Threshold must be between 5 and 80 percent"}), 400
            db.update_status(moisture_threshold=value)
        else:
            return jsonify({"ok": False, "error": "Unknown action"}), 400
        return jsonify({"ok": True, "status": db.get_status()})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Control failed: {type(exc).__name__}: {exc}"}), 503

@app.get("/health")
def health():
    return jsonify({"ok": True, "hardware_mode": "simulation" if hardware and hardware.simulation else "real"})


def main():
    global hardware, worker_thread
    db.init_pool()
    db.ensure_status_row()
    hardware = Hardware()
    # Always enforce OFF on startup before starting the sensor/control worker.
    hardware.valve_off()
    worker_thread = threading.Thread(target=worker, name="dripferti-sensor", daemon=True)
    worker_thread.start()
    try:
        app.run(host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "5000")), threaded=True, use_reloader=False)
    finally:
        stop_event.set()
        try:
            set_valve(False, "manual", "Application shutdown; valve set OFF")
        except Exception:
            hardware.valve_off()
        hardware.close()

if __name__ == "__main__":
    main()
