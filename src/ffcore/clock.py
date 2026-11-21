"""The wall clock (pinnable with LFG_NOW), for crawl stamps, run timing and
the t a live run sets (set_now: cut(), the one moment the run reads the world as of); snapshot stamps, kickoff
times, and Madrid time for people."""
from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import datetime, time, timedelta, timezone, tzinfo
from typing import NamedTuple
from ffcore.parse import snapshot_stamp, stamp

__all__ = ["MADRID", "CLOSE", "Clock", "run_now", "shown", "cut", "set_now", "closes_between", "clock_at"]

CLOSE = time(22, 24)


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


def closes_between(start: datetime, end: datetime) -> list[datetime]:
    """The daily auction closes (CLOSE, Madrid wall time, so the UTC instant follows the daylight-saving change) strictly after
    start and strictly before end, earliest first."""
    first, last = start.astimezone(MADRID).date(), end.astimezone(MADRID).date()
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    closes = [datetime.combine(d, CLOSE, tzinfo=MADRID) for d in days]
    return [c for c in closes if start < c < end]


class Clock(NamedTuple):
    """The closes left before the next lock: `n_closes` of them (the draws of an offer still to come), `lock` that lock and
    `last_close` the latest of them, both as stamps (None when no lock is ahead, and last_close None when no close is left)."""
    n_closes: int
    lock: str | None
    last_close: str | None


def clock_at(now: datetime, locks: Iterable[str]) -> Clock:
    """The Clock at now for the earliest of the lock stamps: the closes strictly after now and strictly before that lock, so
    0 on the lock's day once the last close has passed (and when no lock is ahead)."""
    ahead = sorted((when, s) for s in locks if (when := snapshot_stamp(s)) is not None and when > now)
    if not ahead:
        return Clock(0, None, None)
    lock, name = ahead[0]
    closes = closes_between(now, lock)
    return Clock(len(closes), name, stamp(closes[-1]) if closes else None)


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
    now = snapshot_stamp("2026-11-21T0629Z")
    assert now is not None
    got = clock_at(now, ["2026-11-27T2000Z", "2026-12-04T2000Z"])
    assert got == Clock(6, "2026-11-27T2000Z", "2026-11-26T2124Z"), got
    assert clock_at(now, ["2026-11-21T2100Z"]) == Clock(0, "2026-11-21T2100Z", None), "the lock before tonight's close: none left"
    assert clock_at(now, ["2026-11-21T2130Z"]).n_closes == 1, "a lock after the close counts it"
    assert clock_at(now, ["2026-11-21T2124Z"]).n_closes == 0, "a close at the lock is not before it"
    assert clock_at(now, []) == Clock(0, None, None) and clock_at(now, ["2026-11-01T2000Z"]) == Clock(0, None, None)
    for step in range(0, 24 * 40, 7):
        t = datetime(2026, 3, 1, tzinfo=timezone.utc) + timedelta(hours=step)
        cs = closes_between(t, t + timedelta(days=9))
        assert all(c.astimezone(MADRID).time() == CLOSE and t < c < t + timedelta(days=9) for c in cs)
        assert all(a < b for a, b in zip(cs, cs[1:])) and 8 <= len(cs) <= 10
        assert len(closes_between(t, t + timedelta(days=9, hours=2))) >= len(cs), "a later lock never has fewer closes"
        assert len(closes_between(t + timedelta(hours=1), t + timedelta(days=9))) <= len(cs), "later cut never has more"
    spring = closes_between(datetime(2026, 3, 27, tzinfo=timezone.utc), datetime(2026, 3, 31, tzinfo=timezone.utc))
    assert [c.astimezone(timezone.utc).strftime("%H:%M") for c in spring] == ["21:24", "21:24", "20:24", "20:24"], "the UTC instant moves with the DST change"
    print("ffcore.clock self-test OK")


if __name__ == "__main__":
    _selftest()
