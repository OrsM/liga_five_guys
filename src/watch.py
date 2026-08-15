"""Diff this snapshot against the last one and fire alerts.

Keeps its own state in data/state/watch_prev.json, because data/tidy is
disposable and only ever holds the current view.

Writes the alert body to a scratch file outside the repo (default
$TMPDIR/ff_alerts.md, override with ALERTS_FILE). The GitHub issue is the
real artifact, so nothing here needs committing. Exits 0 always; the
workflow reads the `fired` and `path` outputs to decide whether to notify.

MIGRATED: ownership now comes from ffcore.league. This file used to import
read_initial/apply_transactions out of squads.py, which broke the moment
those moved — a report script importing another report script was the wrong
dependency direction, and this is why. Thresholds come from
inputs/league.ini rather than constants at the top of the file.
"""

import datetime as dt
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.league import League, load_config  # noqa: E402
from ffcore.parse import fmt_money, fmt_pct  # noqa: E402
from ffcore.tidy import load_players  # noqa: E402

STATE = os.path.join("data", "state", "watch_prev.json")
ALERTS = (os.environ.get("ALERTS_FILE")
          or os.path.join(tempfile.gettempdir(), "ff_alerts.md"))

# Set to an int (euros) to ignore keepers you can't afford. Everything else
# lives in inputs/league.ini: start_cross, riser_pct, keeper_start.
MAX_KEEPER_VALUE = None


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
    """Everyone owned by any manager right now, via the shared replay.

    A missing rosters_initial.txt means we cannot tell owned from free, so
    every player is treated as free rather than as owned — noisier, but it
    never hides an alert about someone you could actually sign.
    """
    try:
        return set(League.load(with_market=False).owner)
    except SystemExit:
        return set()


def main():
    cfg = load_config()
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
            if old_start < cfg.start_cross <= start:
                alerts.append("**%s** (%s) start%% %s → %s — crossed %d%%"
                              % (name, team, fmt_pct(old_start),
                                 fmt_pct(start), int(cfg.start_cross)))
            elif old_start >= cfg.start_cross > start:
                alerts.append("%s (%s) dropped below %d%% (%s)"
                              % (name, team, int(cfg.start_cross),
                                 fmt_pct(start)))

        if value and delta and value > 0:
            pct = 100.0 * delta / value
            if pct >= cfg.riser_pct:
                alerts.append("**%s** (%s) +%.1f%% in 24h (%s, value %s)"
                              % (name, team, pct, fmt_money(delta),
                                 fmt_money(value)))

        if ((rec.get("pos") or "").lower() == "portero"
                and start is not None and start >= cfg.keeper_start
                and (MAX_KEEPER_VALUE is None
                     or (value or 0) <= MAX_KEEPER_VALUE)
                and (old_start is None or old_start < cfg.keeper_start)):
            alerts.append("KEEPER: **%s** (%s) at %s, value %s"
                          % (name, team, fmt_pct(start), fmt_money(value)))

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = ["# Alerts — %s" % stamp, ""]
    if not prev:
        lines += ["First run — baseline stored, no comparison possible yet.",
                  ""]
    elif alerts:
        lines += ["- " + a for a in sorted(set(alerts))] + [""]
    else:
        lines += ["Nothing crossed a threshold.", ""]

    parent = os.path.dirname(ALERTS)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(ALERTS, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    save_state(players)
    print("\n".join(lines))
    print("wrote %s (not committed — the issue is the artifact)" % ALERTS)

    # Signal to the workflow whether to notify, and where the body lives.
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write("path=%s\n" % ALERTS)
            if prev and alerts:
                fh.write("fired=true\n")


if __name__ == "__main__":
    main()
