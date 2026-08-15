"""
ffcore.tidy — reading what ff_ingest wrote, and asking it about the past.

Paths, CSV IO and timestamp parsing were copy-pasted across report.py,
offers.py, find_slug.py and history.py. They are here once.

The part that matters is Market: an index over EVERY snapshot in
market.csv, not just the newest one. Three of the five things rivals.py
needs are questions about the past —

    what was this player worth when that transaction happened?   at()
    what did his value do in the fortnight after?                series()
    how stale is the reading I just used?                        Valuation.lag_h

— and none of them can be answered from latest_only(), which is all the
current code ever looks at.

TIMEZONES, the trap this module exists to close. ff_ingest stamps snapshots
in UTC ("2026-08-12T2100Z"). The app's Activity feed, and therefore every
date in inputs/transactions.csv, is Europe/Madrid wall-clock with no offset
written down ("2026-08-12T21:24"). In August that is two hours apart. Compare
them naively and a purchase gets matched to a snapshot taken two hours after
it, which is exactly the direction that makes an overpay look like a bargain.
Ledger strings go through ledger_stamp(); snapshot strings through
snapshot_stamp(); both come back as aware UTC.
"""

from __future__ import annotations

import csv
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

from ffcore.parse import money, pct100
from ffcore.text import norm, resolve

__all__ = ["ROOT", "TIDY", "SEASON", "DECISIONS", "REPORTS", "MADRID",
           "input_path", "read_csv", "write_csv", "append_csv", "write_lines",
           "snapshot_stamp", "ledger_stamp", "latest_only", "snapshots",
           "Market", "Valuation", "load_market", "load_lineups",
           "load_players", "read_ledger", "load_deadline", "LINEUP_SOURCE",
           "pick_source", "load_fixtures", "next_kickoff", "kickoff_stamp"]

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
TIDY = ROOT / "tidy"
SEASON = ROOT / "season"
DECISIONS = ROOT / "decisions"
REPORTS = Path("reports")


def _madrid():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Madrid")
    except Exception:                                    # pragma: no cover
        return timezone(timedelta(hours=2))  # CEST, good enough Mar-Oct


MADRID = _madrid()


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------

def input_path(name: str) -> Path:
    """Locate an editable input file. Prefers inputs/<name>; falls back to
    the repo root so a half-finished move doesn't break the run."""
    p = Path("inputs") / name
    return p if p.exists() else Path(name)


def read_csv(path) -> list[dict]:
    """Rows as dicts. Missing file is empty, not an error — a report that
    hasn't been fed yet should say so, not crash."""
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_csv(path, rows, fieldnames=None) -> None:
    path = Path(path)
    if not rows:
        return
    fieldnames = fieldnames or list(rows[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def append_csv(path, rows, fieldnames=None) -> None:
    """Append, writing the header only when creating the file.

    For the decision logs — squad_log.csv, and rival_log.csv when rivals.py
    lands. Estimates made today are not reconstructable later, which is the
    whole reason they get written down as they are made.
    """
    path = Path(path)
    if not rows:
        return
    fieldnames = fieldnames or list(rows[0])
    fresh = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore",
                           lineterminator="\n")
        if fresh:
            w.writeheader()
        w.writerows(rows)


def load_deadline(with_source: bool = False):
    """The next lock as aware UTC, or None. Fixtures first, the file second.

    Madrid wall-clock in the file, because that is what the app shows. Shared
    rather than copied: report.py stamps hours_to_lock into squad_log.csv and
    xi.py stamps it into xi_fielded.csv, and two readings of the same deadline
    that disagree would silently mis-order the two logs against each other.

    The derived value is the next kickoff in data/tidy/fixtures.csv. That is a
    conservative floor, not the whole truth: J1 2026-27 runs from 15 to 27
    August, and if the app locks each player at HIS match then most of your
    squad stays editable well past the round's first kickoff. It is still
    strictly better than the typed file, which was wrong the moment it expired
    and stayed wrong until you noticed. `with_source=True` returns
    (when, "fixtures"|"file") so the report can say which it used.
    """
    when = next_kickoff()
    if when is not None:
        return (when, "fixtures") if with_source else when

    path = input_path("deadline.txt")
    if not path.exists():
        return (None, "none") if with_source else None
    body = "\n".join(ln.split("#")[0] for ln in
                     path.read_text(encoding="utf-8").splitlines())
    m = re.search(r"\d{4}-\d{2}-\d{2}[T ]?\d{0,2}:?\d{0,2}", body)
    when = ledger_stamp(m.group(0)) if m else None
    return (when, "file" if when else "none") if with_source else when


def write_lines(path, lines) -> None:
    """Write a markdown report. Every report script had this inline."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print("wrote %s" % path)


# ---------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------

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
    """observed_at -> aware UTC. Tolerates 2026-08-12T2100Z, ...T21:00Z,
    and a bare date."""
    return _digits_to_dt(s, timezone.utc)


def ledger_stamp(s: str):
    """A transactions.csv date -> aware UTC.

    The string is Madrid wall-clock because that is what the app displayed
    when you copied it. Read as UTC it would be two hours early all summer.
    """
    local = _digits_to_dt(s, MADRID)
    return local.astimezone(timezone.utc) if local else None


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------

def latest_only(rows: list[dict]) -> list[dict]:
    """Just the newest snapshot. Correct for 'what can I buy now', wrong for
    anything historical — use Market for that."""
    if not rows:
        return []
    newest = max(r.get("observed_at", "") for r in rows)
    return [r for r in rows if r.get("observed_at") == newest]


def snapshots(rows: list[dict]) -> list[str]:
    """Distinct observed_at values, oldest first."""
    return sorted({r.get("observed_at", "") for r in rows if r.get("observed_at")})


def load_market() -> list[dict]:
    return read_csv(TIDY / "market.csv")


# Which probable-XI site the reports are built on. The lineups table can hold
# several; every reader gets exactly one, chosen here, because two sites that
# disagree about a starter must not be silently averaged or racing on file
# order. Compare them against outcomes before changing this.
LINEUP_SOURCE = "futbolfantasy"


def load_lineups(source: str = LINEUP_SOURCE) -> list[dict]:
    """The lineups table, one source only.

    The filter is here rather than in Scorer so that no reader can forget it:
    a caller that skipped it would get one player's row twice, from two sites,
    with different start percentages. Pass source="" to read every row — for
    comparing sources, which is the one job that wants them all.
    """
    return pick_source(read_csv(TIDY / "lineups.csv"), source)


def pick_source(rows: list[dict], source: str) -> list[dict]:
    """One source's rows. An empty `source` means all of them."""
    return rows if not source else [r for r in rows
                                    if r.get("source") == source]


# ---------------------------------------------------------------------------
# fixtures — what makes the deadline derivable
# ---------------------------------------------------------------------------

def kickoff_stamp(s: str):
    """A published kickoff -> aware UTC, or None.

    fromisoformat, not this module's digit parser: the value comes from
    someone else's page with an explicit offset on the end, and the digit
    parser would read a future "+02:00" as if it were UTC — an error of
    exactly the size that makes a locked squad look editable.
    """
    try:
        when = datetime.fromisoformat((s or "").strip())
    except ValueError:
        return None
    return (when.replace(tzinfo=timezone.utc) if when.tzinfo is None
            else when.astimezone(timezone.utc))


def load_fixtures() -> list[dict]:
    """The newest fixtures reading, earliest kickoff first."""
    rows = latest_only(read_csv(TIDY / "fixtures.csv"))
    return sorted(rows, key=lambda r: r.get("kickoff") or "")


def next_kickoff(now=None):
    """The first kickoff still ahead of us, or None if we cannot tell.

    None covers three cases that must all fall back rather than guess: no
    fixtures file yet, an unparseable kickoff, and every listed match already
    started (their page drops a match once it is under way, so a stale file
    goes quiet rather than stale-and-confident).
    """
    now = now or datetime.now(timezone.utc)
    ahead = [k for k in (kickoff_stamp(r.get("kickoff"))
                         for r in load_fixtures()) if k and k > now]
    return min(ahead) if ahead else None


# Which tidy column feeds which report field, and how to read it. Named
# explicitly, per source: the old common.py guessed from a list of thirty
# candidate header names against every CSV in data/tidy, so a renamed column
# went missing quietly instead of failing where you could see it.
MARKET_FIELDS = [("team", "team", None), ("pos", "position", None),
                 ("value", "value", money), ("delta_1d", "delta_1d", money)]
XI_FIELDS = [("team", "team_slug", None), ("start", "start_pct", pct100),
             ("status", "status", None)]


def _merge(players: dict, rows: list[dict], name_col: str, fields) -> dict:
    """Fold one source's rows into the player index. First writer of a field
    keeps it, so market's `team` beats the XI page's `team_slug` and a
    duplicated name inside one snapshot doesn't flap."""
    for r in rows:
        key = norm(r.get(name_col))
        if not key:
            continue
        rec = players.setdefault(key, {})
        rec.setdefault("name", (r.get(name_col) or "").strip())
        for field, col, parse in fields:
            if field in rec:
                continue
            raw = r.get(col)
            if raw in (None, ""):
                continue
            val = parse(raw) if parse else str(raw).strip()
            # A field that won't parse is left unset rather than set to None,
            # so fmt_money prints an em dash instead of a fake zero.
            if val is not None:
                rec[field] = val
    return players


def load_players() -> dict[str, dict]:
    """{normalised name: {name, team, pos, value, delta_1d, start, status}}.

    The NEWEST snapshot of each source, and only that. common.py used to
    take the newest non-empty value per field across all snapshots, which
    kept a player who had left the market alive forever on his last recorded
    value — and, worse, kept a stale `start` for anyone missing from the
    latest XI read. On the 29 snapshots stored when this changed, the two
    agreed on all 655 current players and differed only by five departed
    ones, none of which reached any report.
    """
    market, xi = latest_only(load_market()), latest_only(load_lineups())
    if not market and not xi:
        raise SystemExit("no rows in %s — run `ingest.py parse` first" % TIDY)
    players: dict[str, dict] = {}
    _merge(players, market, "name", MARKET_FIELDS)
    _merge(players, xi, "player_name", XI_FIELDS)
    return players


def read_ledger(name: str = "transactions.csv") -> list[dict]:
    """inputs/transactions.csv, comments stripped, oldest first.

    The file carries its own documentation as # lines below the header, and
    a row whose player field is blank is a stray comma, not a transaction.
    """
    path = input_path(name)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
    rows = [r for r in csv.DictReader(lines)
            if (r.get("player") or "").strip()
            and not r["player"].lstrip().startswith("#")]
    rows.sort(key=lambda r: (r.get("date") or ""))
    return rows


class Valuation(NamedTuple):
    """A value reading, with enough context to distrust it.

    lag_h is hours between the snapshot and the moment asked about. A large
    lag doesn't invalidate the number, but a premium computed against a
    two-day-old value is a weaker claim than one against a two-hour-old
    value, and the report should be able to say which it has.
    """
    value: float
    observed_at: str
    lag_h: float
    name: str


class Market:
    """Every market snapshot, indexed by normalised name.

        m = Market(load_market())
        v = m.at("raphinha", ledger_stamp("2026-08-12T21:24"))
        if v and v.lag_h < 24:
            premium = price / v.value - 1
    """

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self._by_key: dict[str, list[tuple[datetime, dict]]] = {}
        for r in rows:
            key = norm(r.get("name"))
            when = snapshot_stamp(r.get("observed_at", ""))
            if not key or when is None:
                continue
            self._by_key.setdefault(key, []).append((when, r))
        for hist in self._by_key.values():
            hist.sort(key=lambda t: t[0])

    def __len__(self) -> int:
        return len(self._by_key)

    def key_for(self, name):
        """Normalised key for a human-typed name, or None if it doesn't
        resolve uniquely. Substring and initials handled by ffcore.text."""
        k = norm(name)
        if k in self._by_key:
            return k
        row, _cands = resolve(name, latest_only(self.rows))
        return norm(row["name"]) if row else None

    def latest(self) -> dict[str, dict]:
        """{key: newest row} — the 'what exists today' view."""
        return {k: hist[-1][1] for k, hist in self._by_key.items() if hist}

    def at(self, name, when: datetime | None) -> Valuation | None:
        """Value from the last snapshot at or before `when`.

        Falls back to the earliest snapshot if the moment predates the data —
        common early in the season, when the ledger reaches further back than
        the ingest does. lag_h goes negative there, which is the signal that
        the reading is an extrapolation backwards and the premium built on
        it should be treated as indicative only.
        """
        key = self.key_for(name)
        if not key or when is None:
            return None
        hist = self._by_key.get(key) or []
        if not hist:
            return None
        prior = [(t, r) for t, r in hist if t <= when]
        t, r = prior[-1] if prior else hist[0]
        val = money(r.get("value"))
        if val is None:
            return None
        return Valuation(val, r.get("observed_at", ""),
                         (when - t).total_seconds() / 3600.0,
                         r.get("name", name))

    def series(self, name) -> list[tuple[datetime, float]]:
        """[(when, value)] oldest first — the input to post-buy drift."""
        key = self.key_for(name)
        out = []
        for t, r in self._by_key.get(key, []) if key else []:
            v = money(r.get("value"))
            if v is not None:
                out.append((t, v))
        return out

    def drift(self, name, since: datetime | None, days: float):
        """Value change from `since` to `since + days`, as (abs, pct).

        Returns None until a snapshot that late exists, so a horizon the data
        cannot yet support reads as blank rather than as zero drift.
        """
        if since is None:
            return None
        base = self.at(name, since)
        if not base:
            return None
        target = since + timedelta(days=days)
        later = [(t, v) for t, v in self.series(name) if t >= target]
        if not later:
            return None
        _, v = later[0]
        return v - base.value, (v / base.value - 1) * 100.0 if base.value else None


# ---------------------------------------------------------------------------
# selftest — the pure parts only: no filesystem, no clock
# ---------------------------------------------------------------------------

def _selftest() -> None:
    rows = [{"observed_at": "t1", "name": "A"}, {"observed_at": "t2",
            "name": "B"}, {"observed_at": "t2", "name": "C"}]
    assert [r["name"] for r in latest_only(rows)] == ["B", "C"]
    assert latest_only([]) == []
    assert snapshots(rows) == ["t1", "t2"]

    mkt = [{"name": "Ane Aldea", "team": "Alavés", "position": "defensa",
            "value": "2.050.000", "delta_1d": "-12.000"},
           {"name": "Bo Bidal", "team": "Betis", "position": "delantero",
            "value": "", "delta_1d": "0"}]
    xi = [{"player_name": "Ane Aldea", "team_slug": "alaves",
           "start_pct": "0.72", "status": "doubt"},
          {"player_name": "Cai Coro", "team_slug": "celta",
           "start_pct": "85", "status": "ok"}]
    p = _merge(_merge({}, mkt, "name", MARKET_FIELDS), xi,
               "player_name", XI_FIELDS)

    a = p["ane aldea"]
    assert a["value"] == 2050000.0 and a["delta_1d"] == -12000.0
    assert a["pos"] == "defensa" and a["start"] == 72.0 and a["status"] == "doubt"
    # market's display name and team win over the XI page's slug
    assert a["team"] == "Alavés" and a["name"] == "Ane Aldea"

    # An empty cell leaves the field unset, so fmt_money prints "—" not "0K".
    assert "value" not in p["bo bidal"] and p["bo bidal"]["delta_1d"] == 0.0
    # ...and a start_pct nobody published is absent, not zero.
    assert "start" not in p["bo bidal"]

    # XI-only player: name and team come from the XI page.
    c = p["cai coro"]
    assert c["name"] == "Cai Coro" and c["team"] == "celta" and c["start"] == 85.0
    assert "value" not in c

    both = [{"source": "futbolfantasy", "player_name": "Ane"},
            {"source": "analitica", "player_name": "Ane"},
            {"player_name": "Bo"}]                      # pre-source-column row
    assert [r["source"] for r in pick_source(both, "analitica")] == ["analitica"]
    assert len(pick_source(both, "futbolfantasy")) == 1
    assert pick_source(both, "") == both                 # "" means all sources
    assert pick_source(both, "nobody") == []             # a source not stored

    # -- kickoffs ----------------------------------------------------------
    # THE TRAP this parser exists to avoid: an offset that is not UTC. The
    # digit parser used for snapshot stamps would read "21:30+02:00" as 21:30
    # UTC — two hours late, which turns a locked squad into an editable one.
    assert kickoff_stamp("2026-08-15T19:30:00+00:00") == datetime(
        2026, 8, 15, 19, 30, tzinfo=timezone.utc)
    assert kickoff_stamp("2026-08-15T21:30:00+02:00") == datetime(
        2026, 8, 15, 19, 30, tzinfo=timezone.utc)
    # No offset at all is read as UTC, which is what they publish today.
    assert kickoff_stamp("2026-08-15T19:30:00") == datetime(
        2026, 8, 15, 19, 30, tzinfo=timezone.utc)
    assert kickoff_stamp("") is None and kickoff_stamp("soon") is None

    print("ffcore.tidy self-test OK (23 cases)")


if __name__ == "__main__":
    _selftest()
