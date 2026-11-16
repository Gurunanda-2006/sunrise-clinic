"""Demo for the video: several patients confirm the SAME slot at the SAME moment.

    python scripts/race_demo.py                    # 8 patients race for Dr. Rao, Mon 10:00
    python scripts/race_demo.py varma 2026-12-09 10:30 5

It uses the real database (the same file the server uses), so afterwards
http://localhost:8000/bookings shows exactly one new booking for that slot.
Exactly one patient wins; the others are told the slot was just taken.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from app.bookings import BookingError, BookingService  # noqa: E402
from app.config import ROOT, db_path, load_config  # noqa: E402

load_dotenv(ROOT / ".env")
doctor = sys.argv[1] if len(sys.argv) > 1 else "rao"
date = sys.argv[2] if len(sys.argv) > 2 else "2026-12-07"
time = sys.argv[3] if len(sys.argv) > 3 else "10:00"
count = int(sys.argv[4]) if len(sys.argv) > 4 else 8

cfg = load_config()
print(f"Free slots before: {BookingService(cfg, db_path()).free_slots(doctor, date)}")
print(f"\n{count} patients all confirm {cfg.doctors[doctor].name}, {date} {time} at the same moment...\n")

start = threading.Barrier(count)   # holds every thread, then releases them together
lines: list[str] = []


def patient(i: int) -> None:
    service = BookingService(cfg, db_path())   # own connection, like a separate request
    name, phone = f"Race Patient {i}", f"90000{i:05d}"
    start.wait()
    try:
        b = service.book(doctor, date, time, name, 30, phone)
        lines.append(f"  WON   {name}: booked, booking ID {b['id']}")
    except BookingError as err:
        left = service.free_slots(doctor, date)[:3]
        lines.append(f"  LOST  {name}: {err.code} - {err.message} Alternatives: {left}")


threads = [threading.Thread(target=patient, args=(i,)) for i in range(1, count + 1)]
for t in threads:
    t.start()
for t in threads:
    t.join()

print("\n".join(sorted(lines, reverse=True)))
service = BookingService(cfg, db_path())
at_slot = [b for b in service.list_bookings() if (b["doctor_id"], b["date"], b["time"]) == (doctor, date, time)]
print(f"\nBookings now at that slot: {len(at_slot)}  ->  {[b['id'] + ' ' + b['patient_name'] for b in at_slot]}")
print(f"Free slots after:  {service.free_slots(doctor, date)}")
