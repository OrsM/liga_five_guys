"""Shared helpers for squads.py and watch.py.

Deliberately tolerant about column names: it scans every CSV in data/tidy
and merges them on the normalised player name, so it keeps working if
ff_ingest.py renames or splits its outputs.

Where a file carries a timestamp column, the newest row per player wins,
so an append-only snapshot history parses correctly.

CHANGED: norm() and the number parsing now live in ffcore/ and are shared
with every other script. The private _num() that used to be here returned
None for any dot-grouped amount — "2.050.000" and every other price in
inputs/transactions.csv — so nothing must call it again; it is gone. Each
field now declares which parser it wants, in PARSERS below, because a dot
means thousands in one source and a decimal point in another.

norm() is re-exported so `from common import norm` keeps working.
"""

import csv
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from ffcore.parse import money, pct100  # noqa: E402
    from ffcore.text import norm  # noqa: E402  (re-exported)
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "cannot import ffcore (%s).\n"
        "src/ffcore/ must sit next to src/common.py and contain "
        "__init__.py, text.py and parse.py." % exc)

TIDY_DIR = os.path.join("data", "tidy")

# Candidate column names, first match wins. Add to these if a run reports
# a missing field.
FIELDS = {
    "name": ["player_name", "name", "player", "jugador", "nombre"],
    "team": ["team", "equipo", "club", "team_slug", "team_name"],
    "pos": ["pos", "position", "posicion", "posición"],
    "value": ["value", "valor", "market_value", "valor_mercado"],
    "delta_1d": ["delta_1d", "delta1d", "change_1d", "diff_1d", "cambio_1d"],
    "start": ["start_pct", "start_prob", "start", "probabilidad",
              "prob_titular", "titular", "prob", "probability",
              "start_probability", "titularidad", "prob_xi", "xi_prob"],
    "status": ["status", "estado", "injury", "lesion"],
}

# Which parser each numeric field wants. money() reads dot groups as
# thousands; pct100() reads dots as decimal points and rescales 0-1 to 0-100.
# Anything absent here is kept as a stripped string.
PARSERS = {
    "value": money,
    "delta_1d": money,
    "start": pct100,
}

# Columns that mark when a row was taken. Newest row per player wins.
OBS_COLS = ["observed_at", "observed", "snapshot", "timestamp", "dt", "date"]

POS_ORDER = ["portero", "defensa", "mediocampista", "delantero", "entrenador"]

OK_STATUS = ("ok", "", "none", "disponible", "available")


def _pick(header):
    """Map our field names onto whatever this CSV actually calls them."""
    lower = {h.lower().strip(): h for h in header}
    out = {}
    for field, candidates in FIELDS.items():
        for c in candidates:
            if c in lower:
                out[field] = lower[c]
                break
    obs = None
    for c in OBS_COLS:
        if c in lower:
            obs = lower[c]
            break
    return out, obs


def load_players():
    """Merge every CSV in data/tidy into {norm_name: {field: value}}.

    Each field remembers the timestamp of the row that set it, so a later
    file or an older snapshot can't clobber fresher data.
    """
    players, stamps = {}, {}
    seen_fields = set()
    files = sorted(glob.glob(os.path.join(TIDY_DIR, "*.csv")))
    if not files:
        raise SystemExit("no CSVs in %s — run the ingest workflow first"
                         % TIDY_DIR)

    for path in files:
        base = os.path.basename(path)
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                continue
            cols, obs_col = _pick(reader.fieldnames)
            print("%s: headers=%s -> mapped=%s%s"
                  % (base, reader.fieldnames, sorted(cols),
                     " (snapshots via %s)" % obs_col if obs_col else ""))
            if "name" not in cols:
                print("WARN %s skipped — no name column. Add its header to "
                      "FIELDS['name'] in src/common.py" % base)
                continue
            seen_fields.update(cols)

            for row in reader:
                key = norm(row.get(cols["name"]))
                if not key:
                    continue
                obs = (row.get(obs_col) or "") if obs_col else ""
                rec = players.setdefault(key, {})
                rec.setdefault("name", str(row[cols["name"]]).strip())
                seen = stamps.setdefault(key, {})
                for field, col in cols.items():
                    if field == "name":
                        continue
                    val = row.get(col)
                    if val in (None, ""):
                        continue
                    if field in rec and obs <= seen.get(field, ""):
                        continue
                    parser = PARSERS.get(field)
                    parsed = parser(val) if parser else str(val).strip()
                    # A field that fails to parse is left as it was rather
                    # than overwritten with None — a bad row in one snapshot
                    # must not erase a good reading from another.
                    if parsed is None:
                        continue
                    rec[field] = parsed
                    seen[field] = obs

    for field in FIELDS:
        if field not in seen_fields:
            print("WARN no column found for '%s' — add your header to "
                  "FIELDS in src/common.py" % field)

    return players


def fmt_money(v):
    if v is None:
        return "—"
    if abs(v) >= 1e6:
        return "%.2fM" % (v / 1e6)
    return "%.0fK" % (v / 1e3)


def fmt_pct(v):
    return "—" if v is None else "%.0f%%" % v


def flag(rec):
    """Marker for anything the feed says isn't a clean 'ok'."""
    st = (rec.get("status") or "").strip().lower()
    return "" if st in OK_STATUS else " ⚠︎%s" % st


def pos_key(p):
    p = (p or "").lower()
    return POS_ORDER.index(p) if p in POS_ORDER else len(POS_ORDER)
