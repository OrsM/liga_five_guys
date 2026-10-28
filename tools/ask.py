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
from collections.abc import Callable
from typing import Any
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from assemble import scorer, universe  # noqa: E402
from decide import FUNNEL, Action, Board, Move, Universe, board, plan, program  # noqa: E402
from ffcore.names import Name  # noqa: E402
from ffcore.text import resolve  # noqa: E402
from ffcore.jornadas import rounds_ahead  # noqa: E402
from ffcore.schedule import jornada_dates  # noqa: E402
from ffcore.startprob import bucket  # noqa: E402
from ffcore.forecast import expected_points  # noqa: E402
from ffcore.tidy import LINEUP_SOURCE, current  # noqa: E402


def find(u: Universe, words: list[str]) -> str | None:
    """The player these words name, by text.resolve over his Name; None,
    saying who matched, if not one."""
    want = " ".join(words)
    rows = [{"name": n.raw, "key": k} for k, n in u.market.name.items() if n]
    row, hits = resolve(want, rows)
    if row:
        return row["key"]
    print("%s players match %r%s" % (len(hits) or "no", want, ": " + ", ".join(
        sorted(u.market.shown(r["key"]) for r in hits)[:10]) if hits else ""))
    return None


def step(fn: Callable[..., Any], why: str) -> str:
    return "%d/%d %s: %s" % (FUNNEL.index(fn) + 1, len(FUNNEL), fn.__name__, why)


def show(u: Universe, mv: Move) -> str:
    names = u.market.names
    return "%s: %+.1f points (%+.2f a million), a %.0f%% chance of helping" % (
        mv.action.label(names), mv.d_pts, mv.per_million, 100 * mv.p_better)


def not_offered(u: Universe, k: str) -> str:
    """Why candidates() has no move for him, or "" if it has."""
    no = u.why_not(k)
    return step(Universe.candidates, no) if no else ""


def fate(u: Universe, b: Board, r: Move) -> str:
    """Whether the plan makes a ranked move, and if not what making it
    would cost the plan (decide.program with it forced in), beside what a
    million is worth now (Board.lam)."""
    if r in b.plan:
        return step(plan, "in the plan")
    _x, best, _p = program(u, b.rows, relax=False)
    took, worse, _q = program(u, b.rows, relax=False, force=r)
    if not took:
        return step(plan, "left out: the plan cannot make it (no side to field, or no money for it)")
    names = u.market.names
    with_it = [o.action.label(names) for o in took if o.action.players != r.action.players]
    return step(plan, "left out: made, the plan does %.1f points worse%s; a million is worth %.2f points now" % (
        best - worse, " (with %s)" % ", ".join(with_it) if with_it else "", b.lam))


def why(u: Universe, k: str) -> None:
    b = board(u)
    if k in u.mine:
        m, sale = u.market, b.sale(k)
        shown = {f: "-" if (v := getattr(m, f).get(k)) is None else "%.1fM" % (v / 1e6)
                 for f in ("paid", "value")}
        print("yours: paid {paid}, worth {value}".format(**shown)
              + ", %+.0f%% expected" % (m.trend.get(k) or 0.0)
              + (", selling him %+.2f pts/M" % sale.per_million if sale else ""))
        print("Selling him alone: " + (show(u, sale) + "\n  " + fate(u, b, sale)
              if sale else "not ranked (your side cannot be fielded without him)"))
        return
    if gone := not_offered(u, k):
        print(gone)
        return
    r = next(r for r in b.rows if r.action.buy == k)
    print(show(u, r))
    print(fate(u, b, r))


def whatif(u: Universe, words: list[str]) -> None:
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
        bought = u.acquire(k)
        if bought is None:
            print(not_offered(u, k))
            return
        a = bought
    if sold:
        a = replace(a, sell=(sold, ), proceeds=m.fetches(sold))
    mv = u.rank([a])[0]
    print(show(u, mv))
    b = board(u)
    picked, _lam = plan(u, [*b.rows, mv])
    print(step(plan, "the plan would take it") if mv in picked
          else fate(u, b._replace(rows=[*b.rows, mv]), mv))


def forecast(u: Universe, k: str, ahead: int) -> None:
    """His expected points as the model builds them, jornada by jornada:
    points per match x fixture x chance picked if fit x chance fit."""
    m = u.market
    sc = scorer(current("market"), current("lineups", LINEUP_SOURCE))
    rec, st = sc.lookup.get(k), sc.starts
    owner = m.owner.get(k) or ("on the market at %.2fM" % (m.price[k] / 1e6)
                               if k in m.price else "free agent")
    print("%s (%s), %s; value %s, trend %s" % (
        m.shown(k), m.pos.get(k, "?"), owner,
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
    assert first is not None, "forecast players all have a first jornada"
    dates = jornada_dates(rounds_ahead(), u.state.jornadas)
    print("\n%-4s %-10s %6s %7s %7s %6s %6s %8s  %s" % (
        "J", "date", "pts", "fixture", "picked", "fit", "plays", "expected", "why fit"))
    shown = 0
    for j in u.state.jornadas:
        f = u.factors.get(j, {}).get(k)
        if f is None or shown >= ahead:
            continue
        print("%-4d %-10s %6.2f %7.2f %7.2f %6.2f %6.2f %8.2f  %s" % (
            j, dates.get(j, ""), f.cell[0], f.fixture, f.picked, f.fit, f.cell[1],
            expected_points(f.cell), bucket(prog, j, dates.get(j), first) if prog else "-"))
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
        assert said == "3/3 plan: in the plan" if r in b.plan else said.startswith("3/3 plan: left out"), said
    keeper = next(r for r in b.rows if r.action.sell == ("k",))
    said = fate(u, b, keeper)
    assert keeper not in b.plan and "points worse" in said and "sell k" not in said.split("(with")[-1], \
        ("selling your only keeper: what it costs the plan, and what else it would then make", said)
    accented = replace(u, market=replace(u.market, name={
        **u.market.name, "cand_free": Name("Iñigo Vicente"), "cand_rival": Name("Iñigo Martínez")}))
    assert find(accented, ["inigo", "vicente"]) == "cand_free", "accents never count"
    assert find(accented, ["INIGO"]) is None, "two Iñigos: ask which"
    assert not_offered(u, "cand_rival") == \
        "1/3 candidates: riv's player"
    assert not_offered(u, "nobody") == "1/3 candidates: nobody's, and not on the market now"
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
