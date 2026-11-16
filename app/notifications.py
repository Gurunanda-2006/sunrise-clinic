"""Mock confirmation messages (patient SMS + doctor's desk) and .ics calendar invites."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import ClinicConfig


def pretty_when(date_str: str, time_str: str) -> str:
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    return f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b %Y')}, {time_str}"


def build_messages(cfg: ClinicConfig, event: str, b: dict, old: dict | None = None) -> list[dict]:
    """Return the patient message and the doctor's-desk message for one event."""
    doctor = cfg.doctors[b["doctor_id"]]
    when = pretty_when(b["date"], b["time"])
    who = f"{b['patient_name']} ({b['age']} yrs, {b['phone']})"
    if event == "booked":
        patient = (
            f"{cfg.name}: Hi {b['patient_name']}, your appointment with {doctor.name} "
            f"({doctor.department}) is confirmed for {when}. Booking ID {b['id']}. "
            f"To reschedule or cancel, share this ID."
        )
        desk = f"New booking {b['id']}: {who} with {doctor.name} on {when}."
    elif event == "rescheduled":
        was = pretty_when(old["date"], old["time"])
        patient = (
            f"{cfg.name}: Hi {b['patient_name']}, your appointment with {doctor.name} "
            f"has moved from {was} to {when}. Booking ID {b['id']}."
        )
        desk = f"Rescheduled {b['id']}: {who} with {doctor.name} moved from {was} to {when}. The old slot is free."
    elif event == "cancelled":
        patient = (
            f"{cfg.name}: Hi {b['patient_name']}, your appointment with {doctor.name} "
            f"on {when} (Booking ID {b['id']}) has been cancelled."
        )
        desk = f"Cancelled {b['id']}: {who} with {doctor.name} on {when}. The slot is free again."
    elif event == "reminder":
        patient = (
            f"{cfg.name}: Reminder, {b['patient_name']} - your appointment with {doctor.name} "
            f"is in 1 hour, at {b['time']} today. Booking ID {b['id']}."
        )
        desk = f"Reminder sent for {b['id']}: {who} with {doctor.name} at {b['time']}."
    else:
        raise ValueError(event)
    return [
        {"recipient": "patient", "sent_to": b["phone"], "body": patient},
        {"recipient": "doctor_desk", "sent_to": f"{doctor.name} desk", "body": desk},
    ]


def build_ics(cfg: ClinicConfig, b: dict, sequence: int = 0) -> str:
    """A calendar invite for one booking. Clinic time is IST (UTC+05:30); the file uses UTC."""
    doctor = cfg.doctors[b["doctor_id"]]
    start = datetime.strptime(f"{b['date']} {b['time']}", "%Y-%m-%d %H:%M") - timedelta(hours=5, minutes=30)
    end = start + timedelta(minutes=doctor.slot_minutes)
    fmt = "%Y%m%dT%H%M%SZ"
    cancelled = b.get("status") == "cancelled"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Sunrise Clinic//Booking Assistant//EN",
        f"METHOD:{'CANCEL' if cancelled else 'PUBLISH'}",
        "BEGIN:VEVENT",
        f"UID:{b['id']}@sunriseclinic.example",
        f"SEQUENCE:{sequence}",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime(fmt)}",
        f"DTSTART:{start.strftime(fmt)}",
        f"DTEND:{end.strftime(fmt)}",
        f"SUMMARY:{doctor.name} ({doctor.department}) - {cfg.name}",
        f"DESCRIPTION:Patient: {b['patient_name']}. Booking ID: {b['id']}.",
        f"LOCATION:{cfg.name}",
        f"STATUS:{'CANCELLED' if cancelled else 'CONFIRMED'}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"
