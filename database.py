"""MySQL helpers for the existing DripFerti schema."""
import os
from contextlib import contextmanager
import mysql.connector
from mysql.connector import pooling

_pool = None


def init_pool():
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="dripferti_pool",
            pool_size=5,
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", "3306")),
            database=os.getenv("DB_NAME", "dripferti"),
            user=os.getenv("DB_USER", "dripferti_app"),
            password=os.getenv("DB_PASSWORD", ""),
            connection_timeout=5,
            autocommit=False,
        )
    return _pool


@contextmanager
def connection():
    pool = init_pool()
    conn = pool.get_connection()
    try:
        yield conn
    finally:
        conn.close()


def ensure_status_row():
    with connection() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id FROM system_status ORDER BY id LIMIT 1")
        row = cur.fetchone()
        if not row:
            cur.execute("INSERT INTO system_status (valve_status, control_mode, moisture_threshold) VALUES ('OFF','manual',30.00)")
        conn.commit()
        cur.close()


def get_status():
    with connection() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, valve_status, control_mode, moisture_threshold, updated_at FROM system_status ORDER BY id LIMIT 1")
        row = cur.fetchone()
        cur.close()
        if row:
            row["moisture_threshold"] = float(row["moisture_threshold"])
            row["updated_at"] = row["updated_at"].isoformat() if row["updated_at"] else None
        return row


def update_status(**fields):
    allowed = {"valve_status", "control_mode", "moisture_threshold"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    with connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM system_status ORDER BY id LIMIT 1")
        row = cur.fetchone()
        if row:
            sql = "UPDATE system_status SET " + ", ".join(f"{k}=%s" for k in updates) + " WHERE id=%s"
            cur.execute(sql, list(updates.values()) + [row[0]])
        else:
            cur.execute("INSERT INTO system_status (valve_status, control_mode, moisture_threshold) VALUES (%s,%s,%s)", (
                updates.get("valve_status", "OFF"), updates.get("control_mode", "manual"), updates.get("moisture_threshold", 30.0)))
        conn.commit()
        cur.close()


def save_sensor(moisture, sensor_status="normal"):
    with connection() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO sensor_readings (moisture_percent, sensor_status) VALUES (%s,%s)", (round(float(moisture), 2), sensor_status))
        conn.commit()
        cur.close()


def save_flow(flow_lpm, total_liters):
    with connection() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO flow_readings (flow_lpm, total_liters) VALUES (%s,%s)", (round(float(flow_lpm), 3), round(float(total_liters), 3)))
        conn.commit()
        cur.close()


def start_event(source, notes=None):
    with connection() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO irrigation_events (action, source, notes) VALUES ('ON',%s,%s)", (source, notes))
        event_id = cur.lastrowid
        conn.commit()
        cur.close()
        return event_id


def end_event(source, notes=None):
    with connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE irrigation_events SET ended_at=CURRENT_TIMESTAMP, notes=COALESCE(%s, notes) WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1", (notes,))
        conn.commit()
        cur.close()


def dashboard_data(limit=120):
    limit = max(10, min(int(limit), 500))
    with connection() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute(f"SELECT id, moisture_percent, sensor_status, created_at FROM sensor_readings ORDER BY id DESC LIMIT {limit}")
        soil = cur.fetchall()[::-1]
        cur.execute(f"SELECT id, flow_lpm, total_liters, created_at FROM flow_readings ORDER BY id DESC LIMIT {limit}")
        flow = cur.fetchall()[::-1]
        cur.execute("SELECT id, action, source, started_at, ended_at, notes FROM irrigation_events ORDER BY id DESC LIMIT 50")
        events = cur.fetchall()
        cur.close()
    for rows in (soil, flow):
        for r in rows:
            r["created_at"] = r["created_at"].isoformat() if r.get("created_at") else None
            for key, value in list(r.items()):
                if key in ("moisture_percent", "flow_lpm", "total_liters") and value is not None:
                    r[key] = float(value)
    for e in events:
        for key in ("started_at", "ended_at"):
            e[key] = e[key].isoformat() if e.get(key) else None
    return {"soil": soil, "flow": flow, "events": events}
