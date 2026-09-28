"""One player's forecast, as the model builds it, jornada by jornada.

    python tools/explain.py iago aspas [jornadas]

  expected points = points per match x fixture x chance he plays
  chance he plays = chance he is picked if fit x chance he is fit

Every number printed is one the model uses; nothing here is recomputed a
second way.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from assemble import scorer, universe  # noqa: E402
from ffcore.schedule import jornada_dates  # noqa: E402
from ffcore.startprob import bucket  # noqa: E402
from ffcore.tidy import LINEUP_SOURCE, current  # noqa: E402


def main(argv: list[str]) -> int:
    words = [a for a in argv if not a.isdigit()]
    ahead = int(next((a for a in argv if a.isdigit()), 6))
    if not words:
        print(__doc__)
        return 2
    u = universe()
    m = u.market
    want = " ".join(words).lower()
    hits = [k for k, n in m.name.items() if n and want in n.lower()]
    if len(hits) != 1:
        print("%s players match %r%s" % (len(hits) or "no", want,
              ": " + ", ".join(sorted(m.name[k] for k in hits)[:10]) if hits else ""))
        return 1
    k = hits[0]
    sc = scorer(current("market"), current("lineups", LINEUP_SOURCE))
    rec = sc.lookup.get(k)
    st = sc.starts

    owner = m.owner.get(k) or ("on the market at %.2fM" % (m.price[k] / 1e6)
                               if k in m.price else "free agent")
    print("%s (%s), %s; value %s, trend %s" % (
        m.name[k], m.pos.get(k, "?"), owner,
        "%.2fM" % (m.value[k] / 1e6) if k in m.value else "?",
        "%+.1f%%" % m.trend[k] if k in m.trend else "?"))
    if rec is None:
        print("  not on futbolfantasy's market page: no points rate, so no forecast")
        return 0

    r = sc.rate(rec)
    last = r.pj - r.cur_pj
    print("\npoints per match %.2f: %s" % (r.ppm, "; ".join(filter(None, [
        "%d matches last season" % last if last else "no last season (position prior)",
        "%d this season" % r.cur_pj if r.cur_pj else "none this season",
        "treated as unproven" if r.assumed else ""]))))

    status, prog = st.status_of(k), st.prognosis.get(k)
    got, squads = st.history.get(k, (0.0, 0.0))
    listing = ("this week %.0f%%" % st.start_pct[k] if k in st.start_pct
               else "listed, no %%" if k in st.listed else "not listed")
    print("picked if fit: next match %.2f, later %.2f  (listing: %s%s; record %d of %d squads%s)" % (
        sc.rates(rec).p_now, sc.rates(rec).p_rest, listing,
        " - ignored, he is flagged" if status and k in st.start_pct else "",
        got, squads,
        "; last listing while fit %.0f%%" % st.last_fit[k] if status and k in st.last_fit else ""))
    print("fit: %s" % ("not flagged" if not status else "%s, prognosis %s" % (status, prog)))

    first = u.first_jornada_of.get(k)
    dates = jornada_dates(current("matches"), u.state.jornadas)
    print("\n%-4s %-10s %6s %7s %7s %6s %6s %8s  %s" % (
        "J", "date", "pts", "fixture", "picked", "fit", "plays", "expected", "why fit"))
    shown = 0
    for j in u.state.jornadas:
        cell = u.forecaster.per_jornada.get(j, {}).get(k)
        if cell is None or shown >= ahead:
            continue
        pts, p = cell
        picked = sc.rates(rec).p_now if j == first else sc.rates(rec).p_rest
        fit = sc.fit(k, j, dates.get(j), first)
        why = bucket(prog, j, dates.get(j), first) if prog else "-"
        print("%-4d %-10s %6.2f %7.2f %7.2f %6.2f %6.2f %8.2f  %s" % (
            j, dates.get(j, ""), pts, pts / r.ppm if r.ppm else 0.0, picked,
            fit, p, pts * p, why))
        shown += 1
    where = ("in your best eleven" if k in u.outlook.xi.players else
             "on your bench" if k in u.mine else "not in your squad")
    print("\nseason from here: %.0f points; %s" % (u.outlook.season.get(k, 0.0), where))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
