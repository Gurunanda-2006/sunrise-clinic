import pytest

from app.bookings import BookingService
from app.config import load_config


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """A fresh clinic (starting data only) in a temporary database, at the default 'now'."""
    monkeypatch.delenv("CLINIC_NOW", raising=False)
    return BookingService(load_config(), tmp_path / "clinic.db")


MON, TUE, WED = "2026-12-07", "2026-12-08", "2026-12-09"


def phone(n: int) -> str:
    return f"98765{n:05d}"
