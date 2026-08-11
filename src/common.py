"""Shared helpers for squads.py and watch.py.

Deliberately tolerant about column names: it scans every CSV in data/tidy
and merges them on the normalised player name, so it keeps working if
ff_ingest.py renames or splits its outputs.
"""

import csv
import glob
import os
import re
import unicodedata

TIDY_DIR = os.path.join("data", "tidy")

# Candidate column names, first match wins. Add to these if a run reports
# a missing field.
FIELDS = {
    "name": ["name", "player", "jugador", "nombre"],
    "team": ["team", "equipo", "club"],
    "pos": ["pos", "position", "posicion", "posición"],
    "value": ["value", "valor", "market_value", "valor_mercado"],
    "delta_1d": ["delta_1d", "delta1d", "change_1d", "diff_1d", "cambio_1d"],
    "start": ["start_prob", "start_pct", "start", "probabilidad",
              "prob_titular", "titular"],
}

POS_ORDER = ["portero", "defensa", "mediocampista", "delantero", "entrenador"]


def norm(s):
    """Accent-insensitive, case-insensitive key. The only join we have."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace(".", " ").replace("-", " ").replace("'", "")
    return re.sub(r"\s+", " ", s).strip()


def _num(v):
    if v is None or v == "":
        return None
    t = str(v).strip().replace("€", "").replace(",", ".").replace("%", "")
    t = t.replace(" ", "")
    mult = 1.0
    if t[-1:].upper() == "M":
        mult, t = 1e6, t[:-1]
    elif t[-1:].upper() == "K":
        mult, t = 1e3, t[:-1]
    try:
        return float(t) * mult
    except ValueError:
        return None


def _pick(header):
    """Map our field names onto whatever this CSV actually calls them."""
    lower = {h.lower().strip(): h for h in header}
    out = {}
    for field, candidates in FIELDS.items():
        for c in candidates:
            if c in lower:
                out[field] = lower[c]
                break
    return out


def load_players():
    """Merge every CSV in data/tidy into {norm_name: {field: value}}."""
    players = {}
    files = sorted(glob.glob(os.path.join(TIDY_DIR, "*.csv")))
    if not files:
        raise SystemExit("no CSVs in %s — run the ingest workflow first" % TIDY_DIR)

    for path in files:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                continue
            cols = _pick(reader.fieldnames)
            if "name" not in cols:
                continue
            for row in reader:
                key = norm(row.get(cols["name"]))
                if not key:
                    continue
                rec = players.setdefault(key, {"name": row[cols["name"]].strip()})
                for field, col in cols.items():
                    if field == "name":
                        continue
                    val = row.get(col)
                    if val in (None, ""):
                        continue
                    rec[field] = _num(val) if field in (
                        "value", "delta_1d", "start") else str(val).strip()
    # start probabilities may arrive as 0-1 or 0-100; normalise to 0-100
    for rec in players.values():
        s = rec.get("start")
        if s is not None and s <= 1.0:
            rec["start"] = s * 100.0
    return players


def fmt_money(v):
    if v is None:
        return "—"
    if abs(v) >= 1e6:
        return "%.2fM" % (v / 1e6)
    return "%.0fK" % (v / 1e3)


def fmt_pct(v):
    return "—" if v is None else "%.0f%%" % v


def pos_key(p):
    p = (p or "").lower()
    return POS_ORDER.index(p) if p in POS_ORDER else len(POS_ORDER)
