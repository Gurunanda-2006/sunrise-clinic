"""FastAPI app: the required endpoints plus the web page."""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT, db_path, get_now, load_config

load_dotenv(ROOT / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

from .agent import Agent  # noqa: E402
from .bookings import BookingService  # noqa: E402
from .gcal import calendar_from_env  # noqa: E402
from .llm import LLMChain  # noqa: E402
from .slots import norm_date  # noqa: E402

cfg = load_config()
service = BookingService(cfg, db_path())
agent = Agent(cfg, service, LLMChain.from_env())
calendar = calendar_from_env(cfg)   # None unless Google Calendar is set up in .env
if calendar:
    service.listeners.append(calendar.sync_async)

log = logging.getLogger("clinic")


def _reminder_job(stop: threading.Event) -> None:
    """Every 30 seconds, create reminder messages for appointments starting within the hour."""
    while not stop.is_set():
        try:
            count = service.create_due_reminders()
            if count:
                log.info("created %d reminder(s)", count)
        except Exception:
            log.exception("reminder job failed")
        stop.wait(30)


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = threading.Event()
    threading.Thread(target=_reminder_job, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="Sunrise Clinic booking assistant", lifespan=lifespan)
STATIC = Path(ROOT / "static")


class ChatIn(BaseModel):
    session_id: str
    message: str
    language: str | None = None   # optional hint from the page: en | te | hi


# Plain `def` endpoints run in FastAPI's thread pool, so slow LLM calls do not block other patients.

@app.post("/chat")
def chat(body: ChatIn):
    if not body.session_id.strip():
        raise HTTPException(400, "session_id is required")
    return agent.chat(body.session_id.strip(), body.message, body.language)


@app.get("/slots")
def slots(doctor: str = Query(...), date: str = Query(...)):
    doctor_id = doctor.strip().lower()
    if doctor_id not in cfg.doctors:
        raise HTTPException(404, f"Unknown doctor. Use one of: {', '.join(cfg.doctors)}")
    try:
        day = norm_date(date)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    d = cfg.doctors[doctor_id]
    return {"doctor": doctor_id, "doctor_name": d.name, "date": day, "slots": service.free_slots(doctor_id, day)}


@app.get("/bookings")
def bookings():
    return service.list_bookings()


@app.get("/bookings/{booking_id}/ics")
def booking_ics(booking_id: str):
    ics = service.ics_for(booking_id)
    if ics is None:
        raise HTTPException(404, "Booking not found")
    return Response(
        ics, media_type="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{booking_id.upper()}.ics"'},
    )


@app.get("/messages")
def messages():
    return service.list_messages()


@app.post("/reset")
def reset():
    service.reset()
    agent.reset()
    calendar_events_removed = None
    if calendar:
        # The database is the truth; the calendar is a copy. Clear the copy too, so it never
        # shows bookings that no longer exist. A Google failure must not break the reset.
        try:
            calendar_events_removed = calendar.clear()
        except Exception:
            log.exception("calendar clear failed")
    return {"ok": True, "bookings": len(service.list_bookings()), "calendar_events_removed": calendar_events_removed}


@app.post("/reminders/run")
def run_reminders():
    """Run the reminder job now (it also runs by itself every 30 seconds)."""
    return {"created": service.create_due_reminders()}


@app.get("/doctors")
def doctors():
    return {
        "now": get_now(cfg).strftime("%Y-%m-%d %H:%M"),
        "dates": list(cfg.window),
        "doctors": [
            {"id": d.id, "name": d.name, "department": d.department, "days": list(d.days),
             "start": d.start, "end": d.end, "slot_minutes": d.slot_minutes}
            for d in cfg.doctors.values()
        ],
    }


@app.get("/metrics")
def metrics():
    return {**agent.metrics(), "models": [t.label for t in agent.chain.targets]}


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
