"""Diff this snapshot against the last one and fire alerts.

Keeps its own state in data/state/watch_prev.json, because data/tidy is
disposable and only ever holds the current view.

Writes reports/alerts.md. Exits 0 always; the workflow checks whether the
file has any triggers in it before it bothers you.
"""

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import norm, load_players, fmt_money, fmt_pct  # noqa: E402

STATE = os.path.join("data", "state", "watch_prev.json")

START_CROSS = 70.0      # alert when someone crosses this, either way
RISER_PCT = 2.0         # 24h move as % of value
KEEPER_START = 80.0     # a nailed-on keeper appearing is the standing need
MAX_KEEPER_VALUE = None # set to an int to ignore keepers you can't afford


def load_state():
    if not os.path.exists(STATE):
        return {}
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return {}


def save_state(players):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    slim = {k: {"start": v.get("start"), "value": v.get("value"),
                "name": v.get("name")}
            for k, v in players.items()}
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(slim, fh, ensure_ascii=False, sort_keys=True, indent=0)


def owned_keys():
    """Everyone owned by any manager right now.

    Reuses squads.py rather than re-parsing, so the two can never drift.
    """
    try:
        from squads import (read_initial, read_transactions,
                            apply_transactions)
        owner, _ = apply_transactions(read_initial(), read_transactions())
        return set(owner)
    except SystemExit:
        return set()


def main():
    players = load_players()
    prev = load_state()
    owned = owned_keys()
    alerts = []

    for key, rec in players.items():
        if key in owned:
            continue
        name = rec.get("name", key)
        team = rec.get("team", "—")
        start = rec.get("start")
        value = rec.get("value")
        delta = rec.get("delta_1d")
        old = prev.get(key, {})
        old_start = old.get("start")

        if start is not None and old_start is not None:
            if old_start < START_CROSS <= start:
                alerts.append("**%s** (%s) start%% %s → %s — crossed %d%%"
                              % (name, team, fmt_pct(old_start),
                                 fmt_pct(start), int(START_CROSS)))
            elif old_start >= START_CROSS > start:
                alerts.append("%s (%s) dropped below %d%% (%s)"
                              % (name, team, int(START_CROSS), fmt_pct(start)))

        if value and delta and value > 0:
            pct = 100.0 * delta / value
            if pct >= RISER_PCT:
                alerts.append("**%s** (%s) +%.1f%% in 24h (%s, value %s)"
                              % (name, team, pct, fmt_money(delta),
                                 fmt_money(value)))

        if ((rec.get("pos") or "").lower() == "portero"
                and start is not None and start >= KEEPER_START
                and (MAX_KEEPER_VALUE is None
                     or (value or 0) <= MAX_KEEPER_VALUE)
                and (old_start is None or old_start < KEEPER_START)):
            alerts.append("KEEPER: **%s** (%s) at %s, value %s"
                          % (name, team, fmt_pct(start), fmt_money(value)))

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = ["# Alerts — %s" % stamp, ""]
    if not prev:
        lines += ["First run — baseline stored, no comparison possible yet.", ""]
    elif alerts:
        lines += ["- " + a for a in sorted(set(alerts))] + [""]
    else:
        lines += ["Nothing crossed a threshold.", ""]

    os.makedirs("reports", exist_ok=True)
    with open("reports/alerts.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    save_state(players)
    print("\n".join(lines))
    # Signal to the workflow whether to notify.
    if os.environ.get("GITHUB_OUTPUT") and prev and alerts:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write("fired=true\n")


if __name__ == "__main__":
    main()
