"""
history.py — one-off per season: last season's points per player.

Scrapes the LaLiga Fantasy Oficial points table from futbolfantasy analytics,
which publishes total points, matches played and average per match for every
player. That is the only pre-season signal we have with any predictive
content, since no jornada of the new season has been played.

    python src/history.py                 # default URL, current selection
    python src/history.py --url "<url>"   # a specific season from the selector

Writes:
    data/raw/season=<label>/puntos.html.gz     immutable, like ff_ingest
    data/season/points_<label>.csv             tidy, joined on name

NOT part of the daily run. Run it from the history workflow once a season, or
again if the parse looks wrong. Nothing else imports it.

Two things to know:
  * The table has no position column. Positions come from market.csv at report
    time, so this file only carries points, matches and average.
  * The player cell holds two names — the full one and the short display name.
    Both are written out, because the market page uses the short form and name
    is the only join we have.

    pip install httpx lxml
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import os
import re
import sys
from pathlib import Path

import httpx
from lxml import html as lxml_html

URL = "https://www.futbolfantasy.com/analytics/laliga-fantasy/puntos"
ROOT = Path(os.environ.get("FF_ROOT", "./data"))
UA = ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36")

# Header text -> our column. Matched case-insensitively as a substring, so
# "PuntosPts" and "MediaMed" both land correctly.
WANT = {
    "name": ["jugador"],
    "points": ["puntos", "pts"],
    "games": ["pj"],
    "avg": ["media", "med"],
}


def num(t):
    if t is None:
        return None
    t = str(t).strip().replace("%", "").replace(",", ".")
    if not t or t in {"-", "—"}:
        return None
    # 4.59 is a decimal here; 44.550 (valor/punto) we never ask for.
    try:
        return float(t)
    except ValueError:
        return None


def cell_texts(el) -> list[str]:
    out = []
    for t in el.xpath(".//text()"):
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            out.append(t)
    return out


def map_headers(cells: list[str]) -> dict:
    """Map our field names onto this table's header row."""
    got = {}
    for i, raw in enumerate(cells):
        low = raw.lower()
        for field, needles in WANT.items():
            if field in got:
                continue
            if any(n in low for n in needles):
                got[field] = i
    return got


def parse(text: str) -> list[dict]:
    doc = lxml_html.fromstring(text)
    best: list[dict] = []

    for table in doc.xpath("//table"):
        head = table.xpath(".//thead//tr")
        if not head:
            continue
        headers = [" ".join(cell_texts(c))
                   for c in head[-1].xpath("./th|./td")]
        cols = map_headers(headers)
        if not {"name", "points", "games"} <= set(cols):
            continue

        rows = []
        for tr in table.xpath(".//tbody//tr"):
            tds = tr.xpath("./td")
            if len(tds) <= max(cols.values()):
                continue
            names = cell_texts(tds[cols["name"]])
            if not names:
                continue
            full = names[0]
            short = names[1] if len(names) > 1 else names[0]
            team = names[2] if len(names) > 2 else ""
            pts = num(" ".join(cell_texts(tds[cols["points"]])[:1]))
            pj = num(" ".join(cell_texts(tds[cols["games"]])[:1]))
            avg = None
            if "avg" in cols:
                avg = num(" ".join(cell_texts(tds[cols["avg"]])[:1]))
            if avg is None and pts is not None and pj:
                avg = pts / pj
            if pts is None or pj is None:
                continue
            rows.append({
                "player_name": short,
                "player_name_full": full,
                "team": team,
                "points": f"{pts:g}",
                "games": f"{pj:g}",
                "avg": f"{avg:.3f}" if avg is not None else "",
            })

        if len(rows) > len(best):
            best = rows

    return best


def season_label(text: str) -> str:
    """Best-effort label like 2025-26, taken from the season selector."""
    hits = re.findall(r"20\d{2}\s*/\s*(?:20)?\d{2}", text)
    if hits:
        return re.sub(r"\s*/\s*", "-", hits[0])
    return "unknown"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=URL)
    ap.add_argument("--label", default="",
                    help="season label for filenames, e.g. 2025-26")
    args = ap.parse_args()

    with httpx.Client(follow_redirects=True, timeout=45,
                      headers={"User-Agent": UA,
                               "Accept-Language": "es-ES,es;q=0.9"}) as c:
        r = c.get(args.url)
        r.raise_for_status()
        text = r.text

    label = args.label or season_label(text)
    label = re.sub(r"[^0-9A-Za-z._-]", "", label) or "unknown"

    raw_dir = ROOT / "raw" / f"season={label}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(raw_dir / "puntos.html.gz", "wt", encoding="utf-8") as fh:
        fh.write(text)
    print(f"saved raw -> {raw_dir/'puntos.html.gz'} ({len(text)} chars)")

    rows = parse(text)
    if not rows:
        print("PARSE FAILED — no table matched. The raw HTML is saved above; "
              "the markup has probably changed. Nothing was written to "
              "data/season, so the last good file is untouched.")
        sys.exit(1)

    out_dir = ROOT / "season"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"points_{label}.csv"
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "player_name", "player_name_full", "team", "points", "games",
            "avg", "season", "observed_at", "source_url"])
        w.writeheader()
        for row in rows:
            row.update(season=label, observed_at=stamp, source_url=args.url)
            w.writerow(row)

    played = sum(1 for r in rows if num(r["games"]))
    print(f"wrote {out} — {len(rows)} players, {played} with minutes, "
          f"season label '{label}'")
    print("Spot-check a few names against the app before trusting the report.")


if __name__ == "__main__":
    main()
