"""Booking service: book / reschedule / cancel / lookup. Every rule is enforced here, in code."""
from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from . import db
from .config import ClinicConfig, get_now
from .notifications import build_ics, build_messages
from .safety import normalize_phone
from .slots import free_slots, norm_date, norm_time

ID_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L


class BookingError(Exception):
    """A rule was broken. `code` is machine-readable, `message` is safe to show."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra

    def to_dict(self) -> dict:
        return {"ok": False, "error": self.code, "message": self.message, **self.extra}


def _row(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "doctor_id": r["doctor_id"],
        "date": r["date"],
        "time": r["time"],
        "patient_name": r["patient_name"],
        "phone": r["phone"],
        "age": r["age"],
        "reason": r["reason"],
        "status": r["status"],
    }


class BookingService:
    def __init__(self, cfg: ClinicConfig, db_file: str | Path):
        self.cfg = cfg
        self.db_file = Path(db_file)
        self.ics_dir = self.db_file.parent / "ics"
        self.listeners: list = []   # called as fn(event, booking) after each committed change
        db.init_db(self.db_file)
        conn = db.connect(self.db_file)
        try:
            empty = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 0
        finally:
            conn.close()
        if empty:
            self.reset()

    # ---------- helpers ----------

    def _public(self, b: dict) -> dict:
        d = self.cfg.doctors[b["doctor_id"]]
        return {**b, "doctor": d.name, "department": d.department}

    def _doctor(self, doctor_id: str):
        doctor = self.cfg.doctors.get(str(doctor_id).strip().lower())
        if doctor is None:
            raise BookingError("UNKNOWN_DOCTOR", f"Unknown doctor id. Use one of: {', '.join(self.cfg.doctors)}.")
        return doctor

    @staticmethod
    def _parse_slot(date: str, time: str) -> tuple[str, str]:
        try:
            return norm_date(date), norm_time(time)
        except (ValueError, AttributeError):
            raise BookingError("BAD_DATETIME", "Date must be YYYY-MM-DD and time must be HH:MM (24-hour).")

    def _why_not_free(self, conn, doctor, date: str, time: str, exclude_id: str | None = None) -> BookingError:
        """The slot is not in the free list. Work out why, for a useful message."""
        rows = conn.execute(
            "SELECT id, time FROM bookings WHERE doctor_id=? AND date=? AND status='active'",
            (doctor.id, date),
        ).fetchall()
        taken = {r["time"] for r in rows if r["id"] != exclude_id}
        if time in taken:
            return BookingError("SLOT_TAKEN", "That slot has just been taken by another patient.")
        if date in self.cfg.window and len(taken) >= doctor.max_per_day:
            return BookingError("DAY_FULL", f"{doctor.name} is fully booked on {date}.")
        return BookingError(
            "SLOT_NOT_AVAILABLE",
            f"{doctor.name} has no bookable slot at {time} on {date} "
            "(outside working hours, blocked, in the past, or outside the booking window).",
        )

    def _validate_patient(self, doctor, patient_name: str, age, phone: str) -> tuple[str, int, str]:
        name = " ".join(str(patient_name or "").split())
        if len(name) < 2 or not any(c.isalpha() for c in name):
            raise BookingError("BAD_NAME", "The patient's name is missing.")
        try:
            age = int(age)
        except (TypeError, ValueError):
            raise BookingError("BAD_AGE", "Age must be a whole number.")
        if not 0 <= age <= 120:
            raise BookingError("BAD_AGE", "Age must be between 0 and 120.")
        valid_phone = normalize_phone(phone)
        if valid_phone is None:
            raise BookingError("BAD_PHONE", "The phone number must be a 10-digit Indian mobile number starting with 6, 7, 8 or 9.")
        if doctor.min_age is not None and age < doctor.min_age:
            raise BookingError(
                "AGE_RULE",
                f"{doctor.name} ({doctor.department}) does not see patients under {doctor.min_age}. "
                "Book the General Physician (doctor id 'rao') instead.",
            )
        return name[:80], age, valid_phone

    def _store_messages(self, conn, event: str, b: dict, old: dict | None = None) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        for m in build_messages(self.cfg, event, b, old):
            conn.execute(
                "INSERT INTO messages (booking_id, event, recipient, sent_to, body, created_at) VALUES (?,?,?,?,?,?)",
                (b["id"], event, m["recipient"], m["sent_to"], m["body"], now),
            )

    def _write_ics(self, b: dict, sequence: int = 0) -> None:
        try:
            self.ics_dir.mkdir(parents=True, exist_ok=True)
            (self.ics_dir / f"{b['id']}.ics").write_text(build_ics(self.cfg, b, sequence), encoding="utf-8", newline="")
        except OSError:
            pass  # the invite can always be regenerated from the database

    def _notify(self, event: str, booking: dict) -> None:
        """Tell optional add-ons (Google Calendar) about a change. They can never break a booking."""
        for listener in self.listeners:
            try:
                listener(event, booking)
            except Exception:
                pass

    # ---------- reads ----------

    def free_slots(self, doctor_id: str, date: str) -> list[str]:
        conn = db.connect(self.db_file)
        try:
            return free_slots(self.cfg, conn, doctor_id, date, get_now(self.cfg))
        finally:
            conn.close()

    def list_bookings(self) -> list[dict]:
        conn = db.connect(self.db_file)
        try:
            rows = conn.execute("SELECT * FROM bookings WHERE status='active' ORDER BY date, time, doctor_id").fetchall()
            return [self._public(_row(r)) for r in rows]
        finally:
            conn.close()

    def get(self, booking_id: str, include_cancelled: bool = False) -> dict | None:
        conn = db.connect(self.db_file)
        try:
            r = conn.execute("SELECT * FROM bookings WHERE id=?", (str(booking_id).strip().upper(),)).fetchone()
        finally:
            conn.close()
        if r is None or (r["status"] != "active" and not include_cancelled):
            return None
        return self._public(_row(r))

    def lookup(self, booking_id: str | None = None, phone: str | None = None) -> list[dict]:
        """Active bookings for a booking ID or a phone number."""
        if booking_id:
            b = self.get(booking_id)
            return [b] if b else []
        valid = normalize_phone(phone or "")
        if valid is None:
            raise BookingError("BAD_PHONE", "The phone number must be a 10-digit Indian mobile number starting with 6, 7, 8 or 9.")
        conn = db.connect(self.db_file)
        try:
            rows = conn.execute(
                "SELECT * FROM bookings WHERE phone=? AND status='active' ORDER BY date, time", (valid,)
            ).fetchall()
            return [self._public(_row(r)) for r in rows]
        finally:
            conn.close()

    def list_messages(self) -> list[dict]:
        conn = db.connect(self.db_file)
        try:
            rows = conn.execute("SELECT * FROM messages ORDER BY id").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def ics_for(self, booking_id: str) -> str | None:
        b = self.get(booking_id, include_cancelled=True)
        return build_ics(self.cfg, b) if b and b["phone"] else None

    # ---------- checks without writing ----------

    def check_bookable(self, doctor_id: str, date: str, time: str, patient_name: str, age, phone: str) -> dict:
        """Validate a booking request without making it. Raises BookingError if it would fail."""
        doctor = self._doctor(doctor_id)
        date, time = self._parse_slot(date, time)
        name, age, phone = self._validate_patient(doctor, patient_name, age, phone)
        conn = db.connect(self.db_file)
        try:
            self._check_existing(conn, doctor, phone)
            if time not in free_slots(self.cfg, conn, doctor.id, date, get_now(self.cfg)):
                raise self._why_not_free(conn, doctor, date, time)
        finally:
            conn.close()
        return {"doctor_id": doctor.id, "date": date, "time": time, "patient_name": name, "age": age, "phone": phone}

    def _check_existing(self, conn, doctor, phone: str) -> None:
        r = conn.execute(
            "SELECT * FROM bookings WHERE phone=? AND doctor_id=? AND status='active'", (phone, doctor.id)
        ).fetchone()
        if r is not None:
            raise BookingError(
                "ALREADY_BOOKED",
                f"This phone number already has an active booking with {doctor.name}: "
                f"{r['id']} on {r['date']} at {r['time']}. Offer to reschedule it instead.",
                existing_booking=self._public(_row(r)),
            )

    # ---------- writes ----------

    def book(self, doctor_id: str, date: str, time: str, patient_name: str, age, phone: str, reason: str = "") -> dict:
        doctor = self._doctor(doctor_id)
        date, time = self._parse_slot(date, time)
        name, age, phone = self._validate_patient(doctor, patient_name, age, phone)
        stamp = datetime.now().isoformat(timespec="seconds")
        conn = db.connect(self.db_file)
        try:
            # BEGIN IMMEDIATE takes the write lock now, so the checks below and the
            # insert are one atomic step. A second request waits, then sees our row.
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._check_existing(conn, doctor, phone)
                if time not in free_slots(self.cfg, conn, doctor.id, date, get_now(self.cfg)):
                    raise self._why_not_free(conn, doctor, date, time)
                booking = None
                for _ in range(20):
                    bid = "SC-" + "".join(secrets.choice(ID_ALPHABET) for _ in range(4))
                    if conn.execute("SELECT 1 FROM bookings WHERE id=?", (bid,)).fetchone():
                        continue
                    conn.execute(
                        "INSERT INTO bookings (id, doctor_id, date, time, patient_name, age, phone, reason, status, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?, 'active', ?, ?)",
                        (bid, doctor.id, date, time, name, age, phone, str(reason or "")[:200], stamp, stamp),
                    )
                    booking = {
                        "id": bid, "doctor_id": doctor.id, "date": date, "time": time, "patient_name": name,
                        "phone": phone, "age": age, "reason": str(reason or "")[:200], "status": "active",
                    }
                    break
                if booking is None:
                    raise BookingError("INTERNAL", "Could not create a booking ID. Please try again.")
                self._store_messages(conn, "booked", booking)
                conn.execute("COMMIT")
            except sqlite3.IntegrityError:
                # The unique indexes are the last line of defence.
                conn.execute("ROLLBACK")
                raise BookingError("SLOT_TAKEN", "That slot has just been taken by another patient.")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        self._write_ics(booking)
        self._notify("booked", booking)
        return self._public(booking)

    def reschedule(self, booking_id: str, new_date: str, new_time: str) -> dict:
        """Move a booking to another slot with the same doctor. Keeps the booking ID.
        If the new slot is not available nothing changes, so the old slot is never lost."""
        new_date, new_time = self._parse_slot(new_date, new_time)
        stamp = datetime.now().isoformat(timespec="seconds")
        conn = db.connect(self.db_file)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                r = conn.execute(
                    "SELECT * FROM bookings WHERE id=? AND status='active'", (str(booking_id).strip().upper(),)
                ).fetchone()
                if r is None or not r["phone"]:
                    raise BookingError("NOT_FOUND", "No active booking was found with that booking ID.")
                old = _row(r)
                doctor = self.cfg.doctors[old["doctor_id"]]
                if (old["date"], old["time"]) == (new_date, new_time):
                    raise BookingError("SAME_SLOT", "The booking is already at that date and time.")
                free = free_slots(self.cfg, conn, doctor.id, new_date, get_now(self.cfg), exclude_booking_id=old["id"])
                if new_time not in free:
                    raise self._why_not_free(conn, doctor, new_date, new_time, exclude_id=old["id"])
                conn.execute(
                    "UPDATE bookings SET date=?, time=?, updated_at=? WHERE id=?", (new_date, new_time, stamp, old["id"])
                )
                booking = {**old, "date": new_date, "time": new_time}
                self._store_messages(conn, "rescheduled", booking, old)
                conn.execute("COMMIT")
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                raise BookingError("SLOT_TAKEN", "That slot has just been taken by another patient.")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        self._write_ics(booking, sequence=1)
        self._notify("rescheduled", booking)
        return {**self._public(booking), "previous": {"date": old["date"], "time": old["time"]}}

    def cancel(self, booking_id: str) -> dict:
        stamp = datetime.now().isoformat(timespec="seconds")
        conn = db.connect(self.db_file)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                r = conn.execute(
                    "SELECT * FROM bookings WHERE id=? AND status='active'", (str(booking_id).strip().upper(),)
                ).fetchone()
                if r is None or not r["phone"]:
                    raise BookingError("NOT_FOUND", "No active booking was found with that booking ID.")
                conn.execute("UPDATE bookings SET status='cancelled', updated_at=? WHERE id=?", (stamp, r["id"]))
                booking = {**_row(r), "status": "cancelled"}
                self._store_messages(conn, "cancelled", booking)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        self._write_ics(booking, sequence=2)
        self._notify("cancelled", booking)
        return self._public(booking)

    def create_due_reminders(self) -> int:
        """Create a reminder message for every booking that starts within the next hour.
        Each booking is reminded once per slot. Returns how many reminders were created."""
        now = get_now(self.cfg)
        created = 0
        conn = db.connect(self.db_file)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute("SELECT * FROM bookings WHERE status='active' AND phone <> ''").fetchall()
                for r in rows:
                    start = datetime.strptime(f"{r['date']} {r['time']}", "%Y-%m-%d %H:%M")
                    minutes = (start - now).total_seconds() / 60
                    if not 0 < minutes <= 60:
                        continue
                    already = conn.execute(
                        "SELECT 1 FROM messages WHERE booking_id=? AND event='reminder' AND body LIKE ?",
                        (r["id"], f"%at {r['time']} today%"),
                    ).fetchone()
                    if already:
                        continue
                    self._store_messages(conn, "reminder", _row(r))
                    created += 1
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        return created

    def reset(self) -> None:
        """Back to the starting data: only the pre-booked slots from the clinic file."""
        stamp = datetime.now().isoformat(timespec="seconds")
        conn = db.connect(self.db_file)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM bookings")
                conn.execute("DELETE FROM messages")
                for i, p in enumerate(self.cfg.prebooked, start=1):
                    conn.execute(
                        "INSERT INTO bookings (id, doctor_id, date, time, patient_name, age, phone, status, created_at, updated_at) "
                        "VALUES (?,?,?,?, 'Pre-booked', NULL, '', 'active', ?, ?)",
                        (f"PRE-{i:02d}", p["doctor"], p["date"], norm_time(p["time"]), stamp, stamp),
                    )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        if self.ics_dir.is_dir():
            for f in self.ics_dir.glob("*.ics"):
                try:
                    f.unlink()
                except OSError:
                    pass
