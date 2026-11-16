"""Loads the clinic data file and reads settings from the environment."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_FILE = ROOT / "data" / "clinic.json"
DEFAULT_DB_FILE = ROOT / "data" / "clinic.db"

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass(frozen=True)
class Doctor:
    id: str
    name: str
    department: str
    days: tuple[str, ...]
    start: str
    end: str
    slot_minutes: int
    max_per_day: int
    min_age: int | None
    blocked: tuple[dict, ...]


@dataclass(frozen=True)
class ClinicConfig:
    name: str
    emergency_number: str
    window: tuple[str, ...]
    default_now: str
    doctors: dict[str, Doctor]
    prebooked: tuple[dict, ...]
    routing: tuple[dict, ...]
    emergency_signs: str
    emergency_keywords: tuple[str, ...]
    emergency_reply: dict[str, str]


def load_config(path: str | Path | None = None) -> ClinicConfig:
    path = Path(path or os.getenv("CLINIC_DATA") or DEFAULT_DATA_FILE)
    raw = json.loads(path.read_text(encoding="utf-8"))
    doctors = {
        d["id"]: Doctor(
            id=d["id"],
            name=d["name"],
            department=d["department"],
            days=tuple(d["days"]),
            start=d["start"],
            end=d["end"],
            slot_minutes=int(d["slot_minutes"]),
            max_per_day=int(d["max_per_day"]),
            min_age=d.get("min_age"),
            blocked=tuple(d.get("blocked", [])),
        )
        for d in raw["doctors"]
    }
    return ClinicConfig(
        name=raw["clinic"]["name"],
        emergency_number=raw["clinic"]["emergency_number"],
        window=tuple(raw["booking_window"]),
        default_now=raw["default_now"],
        doctors=doctors,
        prebooked=tuple(raw.get("prebooked", [])),
        routing=tuple(raw.get("routing", [])),
        emergency_signs=raw.get("emergency_signs", ""),
        emergency_keywords=tuple(k.lower() for k in raw.get("emergency_keywords", [])),
        emergency_reply=raw.get("emergency_reply", {}),
    )


def get_now(cfg: ClinicConfig) -> datetime:
    """The clinic's "now". Read on every call so CLINIC_NOW can be changed without a restart."""
    value = os.getenv("CLINIC_NOW") or cfg.default_now
    return datetime.strptime(value.strip().replace("T", " ")[:16], "%Y-%m-%d %H:%M")


def db_path() -> Path:
    return Path(os.getenv("CLINIC_DB") or DEFAULT_DB_FILE)
