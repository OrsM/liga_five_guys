"""Questions about the board, answered by the code that makes it.

    python tools/ask.py why iago aspas               # where the funnel left him
    python tools/ask.py whatif buy aspas sell dolan  # a move of your own, scored
    python tools/ask.py whatif sell dolan
    python tools/ask.py forecast iago aspas [6]      # his points, factor by factor

Every answer comes from the production code: assemble.universe, the steps
of decide.FUNNEL (the ones docs/funnel.mmd draws) and the board's seed, so
it agrees with the board. Nothing here decides anything a second way.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from assemble import scorer, universe  # noqa: E402
from decide import (FUNNEL, Action, Move, Universe, blocked, board, joint,  # noqa: E402
                    plan, raised, verdict)
from ffcore.render import title_name  # noqa: E402
from sim import holding  # noqa: E402
from ffcore.schedule import jornada_dates, jornada_expectation  # noqa: E402
from ffcore.startprob import bucket  # noqa: E402
from ffcore.forecast import expected_points  # noqa: E402
from ffcore.tidy import LINEUP_SOURCE, current  # noqa: E402


def find(u, words: list[str]) -> str | None:
    want = " ".join(words).lower()
    hits = [k for k, n in u.market.name.items() if n and want in n.lower()]
    exact = [k for k in hits if u.market.name[k].lower() == want]
    if len(hits) == 1 or len(exact) == 1:
        return (exact or hits)[0]
    print("%s players match %r%s" % (len(hits) or "no", want, ": " + ", ".join(
        sorted(title_name(u.market.name[k]) for k in hits)[:10]) if hits else ""))
    return None


def step(fn, why: str) -> str:
    return "%d/%d %s: %s" % (FUNNEL.index(fn) + 1, len(FUNNEL), fn.__name__, why)


def named(u) -> dict[str, str]:
    return {k: title_name(n) for k, n in u.market.name.items()}


def show(u, mv) -> str:
    names = named(u)
    return ("%s: %+.1f points (%+.2f a million), better off in %.0f%% of seasons"
            " (at a glance %+.1f points over whoever plays instead)") % (
        mv.action.label(names), mv.d_pts, mv.per_million, 100 * mv.p_better,
        u.points(mv.action))


def not_offered(u, k: str) -> str:
    """Why candidates() has no move for him, or "" if it has."""
    no = u.why_not(k)
    return step(Universe.candidates, no) if no else ""


def fate(u, b, r) -> str:
    """Where a ranked move stopped: at verdict, or at plan and why. The plan
    last tried every move it left out against all of itself, paid for."""
    if r in b.plan:
        return step(plan, "in the plan")
    if (no := verdict(r)) is not None:
        return step(verdict, no)
    if (no := blocked(u, b.plan, r.action, b.rows)) is not None:
        return step(plan, "clears the bar alone, left out: %s" % no)
    funded = raised(u, [*b.plan, r], b.rows)
    paid = [m.action.label(named(u)) for m in funded[len(b.plan) + 1:]]
    total = joint(u, funded, b.base)
    return step(plan, "clears the bar alone, left out%s: %s" % (
        " (paid for by %s)" % ", ".join(paid) if paid else "",
        verdict(total) or "together %+.1f, no more than the plan's %+.1f" % (
            total.d_pts, b.gain)))


def why(u, k: str) -> None:
    b = board(u)
    if k in u.mine:
        h, sale = holding(u, b, k), b.sale(k)
        shown = {f: "-" if h[f] is None else "%.1fM" % (h[f] / 1e6)
                 for f in ("paid", "value")}
        print("yours: paid {paid}, worth {value}".format(**shown)
              + ", %+.0f%% expected" % (h["trend"] or 0.0)
              + (", selling him %+.2f pts/M" % h["per_million"]
                 if h["per_million"] is not None else ""))
        print("Selling him alone: " + (show(u, sale) + "\n  " + fate(u, b, sale)
              if sale else "not ranked (your side cannot be fielded without him)"))
        return
    if gone := not_offered(u, k):
        print(gone)
        return
    r = next(r for r in b.rows if r.action.buy == k)
    print(show(u, r))
    print(fate(u, b, r))


def whatif(u, words: list[str]) -> None:
    """buy X [sell Y ...] or sell Y: scored as rank scores any move."""
    m, cut = u.market, [i for i, w in enumerate(words) if w in ("buy", "sell")]
    parts = {words[i]: words[i + 1:j] for i, j in zip(cut, cut[1:] + [len(words)])}
    if not parts:
        print(__doc__)
        return
    sold = find(u, parts["sell"]) if "sell" in parts else None
    if "sell" in parts and sold is None:
        return
    a = Action("sell")
    if "buy" in parts:
        k = find(u, parts["buy"])
        if k is None:
            return
        if gone := not_offered(u, k):
            print(gone)
            return
        a = u.acquire(k)
    if sold:
        a = replace(a, sell=(sold, ), proceeds=m.fetches(sold))
    mv = u.rank([a]).rows[0]
    print(show(u, mv))
    b = board(u)
    picked, gain = plan(u, [*b.rows, mv], b.base) if verdict(mv) is None else ([], 0.0)
    print(step(plan, "the plan would take it: together %+.1f, against %+.1f" % (gain, b.gain))
          if mv in picked else fate(u, b, mv))


def forecast(u, k: str, ahead: int) -> None:
    """His expected points as the model builds them, jornada by jornada:
    points per match x fixture x chance picked if fit x chance fit."""
    m = u.market
    sc = scorer(current("market"), current("lineups", LINEUP_SOURCE))
    rec, st = sc.lookup.get(k), sc.starts
    owner = m.owner.get(k) or ("on the market at %.2fM" % (m.price[k] / 1e6)
                               if k in m.price else "free agent")
    print("%s (%s), %s; value %s, trend %s" % (
        title_name(m.name[k]), m.pos.get(k, "?"), owner,
        "%.2fM" % (m.value[k] / 1e6) if k in m.value else "?",
        "%+.1f%%" % m.trend[k] if k in m.trend else "?"))
    if rec is None:
        print("  not on futbolfantasy's market page: no points rate, so no forecast")
        return
    if not any(k in u.forecaster.per_jornada.get(j, {}) for j in u.state.jornadas):
        print("  not forecast: the model scores players in a squad or on the market")
        return
    r, rates = sc.rate(rec), sc.rates(rec)
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
        rates.p_now, rates.p_rest, listing,
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
        picked = jornada_expectation(rates, None, j == first)[1]
        fit = sc.fit(k, j, dates.get(j), first)
        print("%-4d %-10s %6.2f %7.2f %7.2f %6.2f %6.2f %8.2f  %s" % (
            j, dates.get(j, ""), pts, pts / r.ppm if r.ppm else 0.0, picked,
            fit, p, expected_points(cell), bucket(prog, j, dates.get(j), first) if prog else "-"))
        shown += 1
    where = ("in your best eleven" if k in u.outlook.xi.players else
             "on your bench" if k in u.mine else "not in your squad")
    print("\nseason from here: %.0f points; %s" % (u.outlook.season.get(k, 0.0), where))


def _selftest() -> None:
    from ffcore.fixtures import tiny_market_universe
    from ffcore.forecast import Bootstrap

    u = tiny_market_universe()
    season = list(range(1, 11))
    u = replace(u, state=replace(u.state, jornadas=season), forecaster=Bootstrap(
        {j: dict(u.forecaster.per_jornada[1]) for j in season}))
    b = board(u)
    assert b.plan, "the fixture has a move worth making"
    for r in b.rows:
        said = fate(u, b, r)
        if r in b.plan:
            assert said == "4/4 plan: in the plan", said
        elif verdict(r) is None:
            assert said.startswith("4/4 plan: clears the bar alone, left out"), said
        else:
            assert said == "3/4 verdict: " + verdict(r), said
    poor = replace(u, market=replace(u.market, cash=1e6))
    pb = board(poor)
    cand = next(r for r in pb.rows if r.action.buy == "cand_free")
    real = globals()["joint"]
    try:
        globals()["joint"] = lambda u, moves, base: Move(Action("plan"), 5.0, 0.6)
        said = fate(poor, pb._replace(plan=[], gain=0.0), cand)
    finally:
        globals()["joint"] = real
    assert said == ("4/4 plan: clears the bar alone, left out (paid for by sell bench_m,"
                    " sell bench_k): better off in 60% of seasons, under 70%"), said
    assert not_offered(u, "cand_rival") == \
        "1/4 candidates: riv's player"
    assert not_offered(u, "nobody") == "1/4 candidates: nobody's, and not on the market now"
    assert not_offered(u, "cand_free") == ""
    print("ask self-test OK")


def main(argv: list[str]) -> int:
    if argv[:1] == ["--selftest"]:
        _selftest()
        return 0
    if not argv or argv[0] not in ("why", "whatif", "forecast"):
        print(__doc__)
        return 2
    cmd, words = argv[0], argv[1:]
    u = universe()
    if cmd == "whatif":
        whatif(u, words)
        return 0
    ahead = int(next((w for w in words if w.isdigit()), 6))
    k = find(u, [w for w in words if not w.isdigit()])
    if k is None:
        return 1
    why(u, k) if cmd == "why" else forecast(u, k, ahead)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
