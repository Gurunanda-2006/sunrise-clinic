"""Slot engine. Pure code: free slots = clinic data + current bookings, computed every time."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from .config import WEEKDAYS, ClinicConfig, Doctor


def norm_time(value: str) -> str:
    """'9:00' -> '09:00'. Raises ValueError if it is not a time."""
    return datetime.strptime(value.strip(), "%H:%M").strftime("%H:%M")


def norm_date(value: str) -> str:
    return datetime.strptime(value.strip(), "%Y-%m-%d").strftime("%Y-%m-%d")


def weekday_of(date_str: str) -> str:
    return WEEKDAYS[date.fromisoformat(date_str).weekday()]


def candidate_slots(doctor: Doctor, date_str: str) -> list[str]:
    """All slot start times the doctor works on that date, ignoring bookings."""
    day = weekday_of(date_str)
    if day not in doctor.days:
        return []
    cur = datetime.strptime(doctor.start, "%H:%M")
    end = datetime.strptime(doctor.end, "%H:%M")
    step = timedelta(minutes=doctor.slot_minutes)
    out = []
    while cur + step <= end:
        t = cur.strftime("%H:%M")
        t_end = (cur + step).strftime("%H:%M")
        blocked = any(
            b["day"] == day and t < b["end"] and t_end > b["start"] for b in doctor.blocked
        )
        if not blocked:
            out.append(t)
        cur += step
    return out


def free_slots(
    cfg: ClinicConfig,
    conn: sqlite3.Connection,
    doctor_id: str,
    date_str: str,
    now: datetime,
    exclude_booking_id: str | None = None,
) -> list[str]:
    """Free slot times for one doctor on one date, right now.

    Applies: booking window, working days/hours, slot length, blocked times,
    past times, already-booked slots and the doctor's daily maximum.
    `exclude_booking_id` ignores one booking (used when rescheduling it).
    """
    doctor = cfg.doctors.get(doctor_id)
    if doctor is None or date_str not in cfg.window:
        return []
    rows = conn.execute(
        "SELECT id, time FROM bookings WHERE doctor_id=? AND date=? AND status='active'",
        (doctor_id, date_str),
    ).fetchall()
    taken = {r["time"] for r in rows if r["id"] != exclude_booking_id}
    if len(taken) >= doctor.max_per_day:
        return []
    out = []
    for t in candidate_slots(doctor, date_str):
        if t in taken:
            continue
        if datetime.strptime(f"{date_str} {t}", "%Y-%m-%d %H:%M") <= now:
            continue
        out.append(t)
    return out
