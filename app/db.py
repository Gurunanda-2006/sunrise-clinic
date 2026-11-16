"""SQLite setup. One short-lived connection per operation; WAL mode; explicit transactions."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
  id            TEXT PRIMARY KEY,
  doctor_id     TEXT NOT NULL,
  date          TEXT NOT NULL,              -- YYYY-MM-DD
  time          TEXT NOT NULL,              -- HH:MM
  patient_name  TEXT NOT NULL,
  age           INTEGER,
  phone         TEXT NOT NULL DEFAULT '',
  reason        TEXT NOT NULL DEFAULT '',   -- short English note of the problem
  status        TEXT NOT NULL DEFAULT 'active',   -- active | cancelled
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

-- No double booking: one ACTIVE booking per doctor slot.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_slot
  ON bookings(doctor_id, date, time) WHERE status = 'active';

-- One ACTIVE booking per phone per doctor (pre-booked rows have no phone).
CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_phone_doctor
  ON bookings(phone, doctor_id) WHERE status = 'active' AND phone <> '';

CREATE TABLE IF NOT EXISTS messages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  booking_id  TEXT NOT NULL,
  event       TEXT NOT NULL,     -- booked | rescheduled | cancelled | reminder
  recipient   TEXT NOT NULL,     -- patient | doctor_desk
  sent_to     TEXT NOT NULL,     -- phone number or doctor name
  body        TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: we issue BEGIN IMMEDIATE / COMMIT ourselves.
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db(path: str | Path) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()
