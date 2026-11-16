import threading

import pytest

from app.bookings import BookingError, BookingService
from app.config import load_config
from app.safety import is_emergency, normalize_phone

from .conftest import MON, TUE, WED, phone


def book(svc, doctor, date, time, n=1, age=30, name="Test Patient"):
    return svc.book(doctor, date, time, name, age, phone(n))


# ---------- free slots from the starting data ----------

def test_starting_slots_exclude_prebooked(svc):
    rao_mon = svc.free_slots("rao", MON)
    assert "09:00" not in rao_mon and "09:20" not in rao_mon
    assert rao_mon == ["09:40", "10:00", "10:20", "10:40", "11:00", "11:20", "11:40"]
    assert svc.free_slots("varma", MON) == ["11:30", "12:00", "12:30"]
    assert svc.free_slots("iyer", WED) == ["14:30", "15:00", "15:30", "16:00", "16:30"]


def test_doctor_not_working_that_day(svc):
    assert svc.free_slots("varma", TUE) == []
    assert svc.free_slots("iyer", MON) == []


def test_outside_booking_window(svc):
    assert svc.free_slots("rao", "2026-12-10") == []
    with pytest.raises(BookingError) as e:
        book(svc, "rao", "2026-12-10", "09:00")
    assert e.value.code == "SLOT_NOT_AVAILABLE"


def test_blocked_time(svc):
    assert "15:30" not in svc.free_slots("iyer", TUE)
    assert "15:30" in svc.free_slots("iyer", WED)
    with pytest.raises(BookingError) as e:
        book(svc, "iyer", TUE, "15:30")
    assert e.value.code == "SLOT_NOT_AVAILABLE"


def test_slot_must_be_on_the_grid(svc):
    with pytest.raises(BookingError):
        book(svc, "rao", MON, "09:50")
    with pytest.raises(BookingError):
        book(svc, "rao", MON, "12:00")


# ---------- past slots ----------

def test_past_slots_are_never_offered(svc, monkeypatch):
    monkeypatch.setenv("CLINIC_NOW", "2026-12-07 10:30")
    assert svc.free_slots("rao", MON) == ["10:40", "11:00", "11:20", "11:40"]
    with pytest.raises(BookingError) as e:
        book(svc, "rao", MON, "10:20")
    assert e.value.code == "SLOT_NOT_AVAILABLE"
    monkeypatch.setenv("CLINIC_NOW", "2026-12-08 08:00")
    assert svc.free_slots("rao", MON) == []


# ---------- daily maximum ----------

def test_daily_max(svc):
    # Dr. Varma: max 4 a day, 3 already booked on Monday -> exactly one more.
    book(svc, "varma", MON, "11:30", n=1)
    assert svc.free_slots("varma", MON) == []
    with pytest.raises(BookingError) as e:
        book(svc, "varma", MON, "12:00", n=2)
    assert e.value.code == "DAY_FULL"


def test_daily_max_rao(svc):
    # Dr. Rao: 9 slots on Tuesday, max 8, one pre-booked -> 7 more, then the last slot is not offered.
    for i, t in enumerate(svc.free_slots("rao", TUE)[:7]):
        book(svc, "rao", TUE, t, n=i)
    assert svc.free_slots("rao", TUE) == []


def test_cancel_reopens_a_full_day(svc):
    b = book(svc, "varma", MON, "11:30", n=1)
    svc.cancel(b["id"])
    assert svc.free_slots("varma", MON) == ["11:30", "12:00", "12:30"]


# ---------- age rule ----------

def test_age_rule(svc):
    with pytest.raises(BookingError) as e:
        book(svc, "iyer", TUE, "14:00", age=11)
    assert e.value.code == "AGE_RULE"
    assert book(svc, "iyer", TUE, "14:00", age=12)["id"].startswith("SC-")
    assert book(svc, "rao", TUE, "09:00", age=5, n=2)["doctor_id"] == "rao"


# ---------- phone ----------

def test_phone_validation(svc):
    assert normalize_phone("98765 43210") == "9876543210"
    assert normalize_phone("+91-9876543210") == "9876543210"
    assert normalize_phone("09876543210") == "9876543210"
    for bad in ["12345", "5876543210", "98765432101", "abcdefghij", ""]:
        assert normalize_phone(bad) is None
    with pytest.raises(BookingError) as e:
        svc.book("rao", MON, "09:40", "A Patient", 30, "12345")
    assert e.value.code == "BAD_PHONE"


def test_one_active_booking_per_phone_per_doctor(svc):
    first = book(svc, "rao", MON, "09:40", n=1)
    with pytest.raises(BookingError) as e:
        book(svc, "rao", TUE, "09:00", n=1)
    assert e.value.code == "ALREADY_BOOKED"
    assert e.value.extra["existing_booking"]["id"] == first["id"]
    # A different doctor is fine, and so is the same doctor after cancelling.
    book(svc, "varma", WED, "10:00", n=1)
    svc.cancel(first["id"])
    book(svc, "rao", TUE, "09:00", n=1)


# ---------- double booking ----------

def test_no_double_booking(svc):
    book(svc, "rao", MON, "09:40", n=1)
    assert "09:40" not in svc.free_slots("rao", MON)
    with pytest.raises(BookingError) as e:
        book(svc, "rao", MON, "09:40", n=2)
    assert e.value.code == "SLOT_TAKEN"


def test_two_simultaneous_bookings_of_the_same_slot(svc):
    """Many patients confirm the same slot at the same moment: exactly one wins."""
    n = 8
    barrier = threading.Barrier(n)
    results = []

    def attempt(i):
        # Each thread has its own service object and its own DB connections, like separate requests.
        mine = BookingService(svc.cfg, svc.db_file)
        barrier.wait()
        try:
            results.append(("ok", mine.book("rao", MON, "10:00", f"Patient {i}", 30, phone(i))))
        except BookingError as err:
            results.append((err.code, None))

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert [r[0] for r in results].count("ok") == 1
    assert all(code in ("ok", "SLOT_TAKEN") for code, _ in results)
    at_slot = [b for b in svc.list_bookings() if (b["doctor_id"], b["date"], b["time"]) == ("rao", MON, "10:00")]
    assert len(at_slot) == 1


def test_concurrent_bookings_cannot_exceed_daily_max(svc):
    """Three patients race for the three different free slots, but only one place is left."""
    times = ["11:30", "12:00", "12:30"]
    barrier = threading.Barrier(len(times))
    codes = []

    def attempt(i, t):
        mine = BookingService(svc.cfg, svc.db_file)
        barrier.wait()
        try:
            mine.book("varma", MON, t, f"Patient {i}", 30, phone(i))
            codes.append("ok")
        except BookingError as err:
            codes.append(err.code)

    threads = [threading.Thread(target=attempt, args=(i, t)) for i, t in enumerate(times)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes.count("ok") == 1 and codes.count("DAY_FULL") == 2


# ---------- reschedule / cancel ----------

def test_reschedule_frees_the_old_slot(svc):
    b = book(svc, "rao", MON, "09:40")
    moved = svc.reschedule(b["id"], TUE, "09:20")
    assert moved["id"] == b["id"] and (moved["date"], moved["time"]) == (TUE, "09:20")
    assert "09:40" in svc.free_slots("rao", MON)
    assert "09:20" not in svc.free_slots("rao", TUE)
    assert len([x for x in svc.list_bookings() if x["id"] == b["id"]]) == 1


def test_failed_reschedule_keeps_the_old_slot(svc):
    b = book(svc, "rao", MON, "09:40")
    with pytest.raises(BookingError) as e:
        svc.reschedule(b["id"], TUE, "10:00")  # pre-booked
    assert e.value.code == "SLOT_TAKEN"
    assert svc.get(b["id"])["time"] == "09:40"
    assert "09:40" not in svc.free_slots("rao", MON)


def test_reschedule_within_a_full_day(svc):
    b = book(svc, "varma", MON, "11:30")  # the day is now at its maximum
    moved = svc.reschedule(b["id"], MON, "12:30")
    assert moved["time"] == "12:30"


def test_cancel_frees_the_slot(svc):
    b = book(svc, "iyer", TUE, "14:00")
    svc.cancel(b["id"])
    assert "14:00" in svc.free_slots("iyer", TUE)
    assert svc.get(b["id"]) is None
    with pytest.raises(BookingError) as e:
        svc.cancel(b["id"])
    assert e.value.code == "NOT_FOUND"


def test_prebooked_rows_cannot_be_changed(svc):
    with pytest.raises(BookingError):
        svc.cancel("PRE-01")
    with pytest.raises(BookingError):
        svc.reschedule("PRE-01", TUE, "09:00")


def test_lookup_by_id_and_phone(svc):
    b1 = book(svc, "rao", MON, "09:40", n=7)
    b2 = book(svc, "varma", WED, "10:00", n=7)
    assert svc.lookup(booking_id=b1["id"].lower())[0]["id"] == b1["id"]
    assert {b["id"] for b in svc.lookup(phone=phone(7))} == {b1["id"], b2["id"]}
    assert svc.lookup(phone=phone(8)) == []


# ---------- messages, .ics, persistence, reset ----------

def test_messages_and_ics(svc):
    b = book(svc, "rao", MON, "09:40")
    svc.reschedule(b["id"], TUE, "09:20")
    svc.cancel(b["id"])
    msgs = svc.list_messages()
    assert [(m["event"], m["recipient"]) for m in msgs] == [
        ("booked", "patient"), ("booked", "doctor_desk"),
        ("rescheduled", "patient"), ("rescheduled", "doctor_desk"),
        ("cancelled", "patient"), ("cancelled", "doctor_desk"),
    ]
    assert all(b["id"] in m["body"] for m in msgs)
    ics = svc.ics_for(b["id"])
    assert "BEGIN:VEVENT" in ics and "STATUS:CANCELLED" in ics
    assert (svc.ics_dir / f"{b['id']}.ics").exists()


def test_ics_time_is_utc(svc):
    b = book(svc, "rao", MON, "09:40")
    assert "DTSTART:20261207T041000Z" in svc.ics_for(b["id"])  # 09:40 IST
    assert "DTEND:20261207T043000Z" in svc.ics_for(b["id"])


def test_bookings_survive_a_restart(svc):
    b = book(svc, "rao", MON, "09:40")
    restarted = BookingService(load_config(), svc.db_file)
    assert restarted.get(b["id"])["time"] == "09:40"
    assert "09:40" not in restarted.free_slots("rao", MON)


def test_reset(svc):
    book(svc, "rao", MON, "09:40")
    svc.reset()
    assert len(svc.list_bookings()) == 7
    assert svc.list_messages() == []
    assert "09:40" in svc.free_slots("rao", MON)


# ---------- Google Calendar hook ----------

def test_listeners_are_told_about_every_change(svc):
    seen = []
    svc.listeners.append(lambda event, b: seen.append((event, b["id"], b["time"])))
    svc.listeners.append(lambda event, b: 1 / 0)       # a broken add-on must not break bookings
    b = book(svc, "rao", MON, "09:40")
    svc.reschedule(b["id"], MON, "10:20")
    svc.cancel(b["id"])
    assert seen == [("booked", b["id"], "09:40"), ("rescheduled", b["id"], "10:20"), ("cancelled", b["id"], "10:20")]


def test_calendar_event_shape():
    from app.gcal import event_body, event_id
    cfg = load_config()
    b = {"id": "SC-4F7K", "doctor_id": "iyer", "date": "2026-12-08", "time": "14:30", "patient_name": "Asha", "age": 30, "reason": "rash"}
    body = event_body(cfg, b)
    assert body["start"] == {"dateTime": "2026-12-08T14:30:00", "timeZone": "Asia/Kolkata"}
    assert body["end"]["dateTime"] == "2026-12-08T15:00:00"
    assert set(event_id("SC-4F7K")) <= set("0123456789abcdefghijklmnopqrstuv")


# ---------- reminders ----------

def test_reminder_one_hour_before(svc, monkeypatch):
    b = book(svc, "rao", MON, "09:40")
    assert svc.create_due_reminders() == 0            # 08:00: more than an hour away
    monkeypatch.setenv("CLINIC_NOW", "2026-12-07 08:45")
    assert svc.create_due_reminders() == 1
    assert svc.create_due_reminders() == 0            # only once
    reminders = [m for m in svc.list_messages() if m["event"] == "reminder"]
    assert [m["recipient"] for m in reminders] == ["patient", "doctor_desk"]
    assert b["id"] in reminders[0]["body"]
    monkeypatch.setenv("CLINIC_NOW", "2026-12-07 09:45")
    assert svc.create_due_reminders() == 0            # already started


# ---------- emergency pre-filter ----------

def test_emergency_filter():
    cfg = load_config()
    for text in ["I have chest pain since morning", "my father can't breathe", "She fainted and is not responding",
                 "నాకు ఛాతీ నొప్పి వస్తోంది", "मुझे सीने में दर्द है"]:
        assert is_emergency(cfg, text), text
    for text in ["I have fever and a cough", "knee pain for a week", "skin rash on my arm"]:
        assert not is_emergency(cfg, text), text
