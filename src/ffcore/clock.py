"""The wall clock (pinnable with LFG_NOW), for crawl stamps, run timing and
the t a live run sets (set_now: cut(), the one moment the run reads the world as of); snapshot stamps, kickoff
times, and Madrid time for people."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone, tzinfo
from ffcore.parse import snapshot_stamp, stamp

__all__ = ["MADRID", "run_now", "shown", "variant", "set_variant", "cut", "set_now"]


def _madrid() -> tzinfo:
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Madrid")
    except Exception:                                    # pragma: no cover
        return timezone(timedelta(hours=2))


MADRID = _madrid()


def run_now() -> datetime:
    """The wall clock (LFG_NOW when pinned): what a live run sets t to."""
    pinned = os.environ.get("LFG_NOW", "").strip()
    when = snapshot_stamp(pinned) if pinned else datetime.now(timezone.utc)
    if when is None:
        raise ValueError("LFG_NOW=%r is not a time (2026-10-07T0934Z)" % pinned)
    return when


_VARIANT: list[str] = ["live"]


def variant() -> str:
    """The set of model constants in force (variants): live unless a replay
    of a challenger says otherwise."""
    return _VARIANT[0]


def set_variant(name: str) -> None:
    _VARIANT[:] = [name]


LOOK: dict[str, datetime | None] = {"t": None}


def cut() -> str:
    """t as a stamp: this process's run start (the wall clock when first asked, run_now) unless set_now set it."""
    t = LOOK["t"] or run_now()
    LOOK["t"] = t
    return stamp(t)


def set_now(when: datetime | None) -> None:
    """Set t: a replay's past time, or None for the wall clock now."""
    LOOK["t"] = when or run_now()


def shown(t: datetime, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return t.astimezone(MADRID).strftime(fmt + " %Z")


def _selftest() -> None:
    assert run_now().tzinfo is timezone.utc
    os.environ["LFG_NOW"] = "2026-08-20T0900Z"
    assert run_now() == snapshot_stamp("2026-08-20T0900Z")
    del os.environ["LFG_NOW"]
    assert run_now().year >= 2026

    set_now(snapshot_stamp("2026-08-20T0900Z"))
    assert cut() == "2026-08-20T0900Z", "t is what set_now set"
    set_now(None)
    assert cut() == cut(), "no pin: t is the wall clock at first asking, then stays"

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
