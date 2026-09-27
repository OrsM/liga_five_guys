"""The one clock every stage reads: now (pinnable with LFG_NOW), snapshot
stamps, kickoff times, and Madrid time for people. Modules that cache what
depends on now register a reset with on_reset; set_now calls them."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Callable

__all__ = ["MADRID", "run_now", "set_now", "on_reset", "shown",
           "snapshot_stamp", "kickoff_stamp"]


def _madrid():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Madrid")
    except Exception:                                    # pragma: no cover
        return timezone(timedelta(hours=2))


MADRID = _madrid()


@lru_cache(maxsize=4096)
def _digits_to_dt(s: str, tz):
    digits = re.sub(r"\D", "", s or "")
    if len(digits) < 8:
        return None
    try:
        return datetime(
            int(digits[:4]), int(digits[4:6]), int(digits[6:8]),
            int(digits[8:10]) if len(digits) >= 10 else 0,
            int(digits[10:12]) if len(digits) >= 12 else 0,
            tzinfo=tz)
    except ValueError:
        return None


def snapshot_stamp(s: str):
    return _digits_to_dt(s, timezone.utc)


def kickoff_stamp(s: str):
    try:
        when = datetime.fromisoformat((s or "").strip())
    except ValueError:
        return None
    return (when.replace(tzinfo=timezone.utc) if when.tzinfo is None
            else when.astimezone(timezone.utc))


_NOW: list = []

_RESETS: list[Callable[[], None]] = []


def on_reset(fn: Callable[[], None]) -> None:
    _RESETS.append(fn)


def run_now() -> datetime:
    if not _NOW:
        pinned = os.environ.get("LFG_NOW", "").strip()
        _NOW.append(snapshot_stamp(pinned) if pinned
                    else datetime.now(timezone.utc))
    return _NOW[0]


def set_now(when: datetime | None) -> None:
    _NOW[:] = [when] if when is not None else []
    for reset in _RESETS:
        reset()


def shown(t=None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    when = run_now() if t is None else t
    return when.astimezone(MADRID).strftime(fmt + " %Z")


def _selftest() -> None:
    assert run_now() is run_now()
    assert run_now().tzinfo is timezone.utc
    _NOW.clear()
    os.environ["LFG_NOW"] = "2026-08-20T0900Z"
    assert run_now() == snapshot_stamp("2026-08-20T0900Z")
    del os.environ["LFG_NOW"]
    _NOW.clear()
    assert run_now().year >= 2026

    cleared = []
    on_reset(lambda: cleared.append(True))
    set_now(snapshot_stamp("2026-08-01T1000Z"))
    assert run_now() == snapshot_stamp("2026-08-01T1000Z") and cleared
    set_now(None)
    assert run_now() != snapshot_stamp("2026-08-01T1000Z")
    _RESETS.pop()

    assert kickoff_stamp("2026-08-15T19:30:00+00:00") == datetime(
        2026, 8, 15, 19, 30, tzinfo=timezone.utc)
    assert kickoff_stamp("2026-08-15T21:30:00+02:00") == datetime(
        2026, 8, 15, 19, 30, tzinfo=timezone.utc)
    assert kickoff_stamp("2026-08-15T19:30:00") == datetime(
        2026, 8, 15, 19, 30, tzinfo=timezone.utc)
    assert kickoff_stamp("") is None and kickoff_stamp("soon") is None

    summer = datetime(2026, 9, 18, 16, 40, tzinfo=timezone.utc)
    winter = datetime(2026, 12, 18, 16, 40, tzinfo=timezone.utc)
    assert shown(summer) == "2026-09-18 18:40 CEST", shown(summer)
    assert shown(winter) == "2026-12-18 17:40 CET", shown(winter)
    assert shown(summer, "%d %b %H:%M") == "18 Sep 18:40 CEST"
    assert snapshot_stamp("2026-09-18T1640Z") == summer, \
        snapshot_stamp("2026-09-18T1640Z")
    print("ffcore.clock self-test OK")


if __name__ == "__main__":
    _selftest()
