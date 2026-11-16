"""Optional Google Calendar sync (bonus). Each booking becomes an event on one test calendar.

It is switched on only when GOOGLE_CALENDAR_ID and GOOGLE_SERVICE_ACCOUNT_FILE are set in .env.
The sync runs in a background thread after the booking is already saved, so a slow or
failing Google call can never delay or block a booking. Failures are only logged.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .config import ROOT, ClinicConfig

log = logging.getLogger("clinic.gcal")
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
API = "https://www.googleapis.com/calendar/v3/calendars/{cal}/events"


def event_id(booking_id: str) -> str:
    """Google event ids may only use 0-9 and a-v. Hex of the booking id fits and is stable,
    so the same booking always maps to the same event."""
    return "sunrise" + booking_id.encode().hex()


def event_body(cfg: ClinicConfig, b: dict) -> dict:
    doctor = cfg.doctors[b["doctor_id"]]
    start = datetime.strptime(f"{b['date']} {b['time']}", "%Y-%m-%d %H:%M")
    end = start + timedelta(minutes=doctor.slot_minutes)
    return {
        "id": event_id(b["id"]),
        "summary": f"{b['patient_name']} - {doctor.name} ({doctor.department})",
        "description": f"Booking ID: {b['id']}\nPatient: {b['patient_name']}, {b['age']} yrs\nReason: {b.get('reason') or '-'}",
        "location": cfg.name,
        "status": "confirmed",
        "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "Asia/Kolkata"},
        "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "Asia/Kolkata"},
    }


class CalendarSync:
    def __init__(self, cfg: ClinicConfig, calendar_id: str, key_file: Path):
        # Imported here so the app runs without these packages when the bonus is off.
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(str(key_file), scopes=SCOPES)
        self.session = AuthorizedSession(creds)
        self.cfg = cfg
        self.url = API.format(cal=calendar_id)
        self._lock = threading.Lock()

    def sync(self, event: str, booking: dict) -> None:
        """event is 'booked', 'rescheduled' or 'cancelled'."""
        url = f"{self.url}/{event_id(booking['id'])}"
        with self._lock:
            if event == "cancelled":
                r = self.session.delete(url, timeout=15)
                ok = r.status_code in (200, 204, 404, 410)
            else:
                body = event_body(self.cfg, booking)
                r = self.session.put(url, json=body, timeout=15)        # update if it exists
                if r.status_code == 404:
                    r = self.session.post(self.url, json=body, timeout=15)  # otherwise create
                ok = r.status_code in (200, 201)
        if ok:
            log.info("calendar: %s %s", event, booking["id"])
        else:
            log.warning("calendar: %s %s failed: %s %s", event, booking["id"], r.status_code, r.text[:200])

    def clear(self) -> int:
        """Delete every event this app created (their ids start with 'sunrise'). Used by /reset,
        so the calendar never shows bookings that no longer exist in the database."""
        removed = 0
        with self._lock:
            page = None
            ids: list[str] = []
            while True:
                params = {"maxResults": 250, "fields": "items(id),nextPageToken"}
                if page:
                    params["pageToken"] = page
                r = self.session.get(self.url, params=params, timeout=20)
                if r.status_code != 200:
                    log.warning("calendar: listing events failed: %s %s", r.status_code, r.text[:200])
                    break
                data = r.json()
                ids += [e["id"] for e in data.get("items", []) if e.get("id", "").startswith("sunrise")]
                page = data.get("nextPageToken")
                if not page:
                    break
            for eid in ids:
                r = self.session.delete(f"{self.url}/{eid}", timeout=15)
                removed += r.status_code in (200, 204)
        log.info("calendar: removed %d event(s) on reset", removed)
        return removed

    def sync_async(self, event: str, booking: dict) -> None:
        def run() -> None:
            try:
                self.sync(event, booking)
            except Exception:
                log.exception("calendar sync failed for %s", booking.get("id"))

        threading.Thread(target=run, daemon=True).start()


def calendar_from_env(cfg: ClinicConfig) -> CalendarSync | None:
    calendar_id = (os.getenv("GOOGLE_CALENDAR_ID") or "").strip()
    key_file = (os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or "").strip()
    if not calendar_id or not key_file:
        return None
    path = Path(key_file) if Path(key_file).is_absolute() else ROOT / key_file
    if not path.is_file():
        log.warning("Google Calendar is off: key file %s was not found", path)
        return None
    try:
        sync = CalendarSync(cfg, calendar_id, path)
    except Exception as err:
        log.warning("Google Calendar is off: %s", err)
        return None
    log.info("Google Calendar sync is on")
    return sync
