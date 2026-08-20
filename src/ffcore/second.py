"""
ffcore/second.py — the second probable-XI source, joined and rendered.

futbolfantasy (**FF**) is the primary. It is what the Scorer reads, so its
percentage is the P(start) inside every xPts/j in this repo.
analiticafantasy (**AF**) is a second opinion, printed BESIDE it in every
table and never blended into it: neither source has been checked against a
played jornada, so there is no weight to blend them by. Store both, use one,
compare when outcomes exist.

Three reports now print both columns — report.py (question 1, the slate, the
bench), rivals.py (three tables) and squads.py (the watchlist) — which is why
the join and the cell live here instead of in whichever one needed them first.
A second copy of `af_cell` would eventually round differently from the first.

The join is by NAME, because the two sites number players differently and
neither publishes the app's slug. Nothing is guessed: a name with several
candidates is handed back to be printed, not picked between.

Deps: stdlib, plus ffcore.tidy for the CSV read.

    python src/ffcore/second.py     # selftest: the cell and the join, no IO
"""

from __future__ import annotations

from ffcore.text import norm, resolve
from ffcore.tidy import latest_only, load_lineups

__all__ = ["SECOND_SOURCE", "FF_AF_HEAD", "LEGEND", "af_cell", "second_cells"]

SECOND_SOURCE = "analitica"

# The two columns, so every table spells them the same way and in the same
# order — primary first, second opinion second.
FF_AF_HEAD = "FF | AF"

LEGEND = ("**FF** is futbolfantasy's probable-XI percentage, which is the one "
          "the forecast uses. **AF** is analiticafantasy's read of the same "
          "eleven, printed beside it and never blended in — `titular` is a "
          "named starter (a final call, with no number to it), a percentage "
          "is their editors' consensus, `?` means they list him without "
          "either, and `—` means they do not have him. Two columns that "
          "disagree are the signal; that is the whole point of carrying "
          "both.")


def af_cell(row) -> str:
    """The second source's read, in its own units.

    It publishes two different things on the same page, and they are not
    interchangeable. Close to kickoff it names a starting eleven — a final
    call, with no number attached. Before that it publishes its editors'
    consensus as a fraction ("2/3 titular"), which IS a probability and is
    printed as one. A final call is printed as `titular`, never dressed up as
    100%, because turning a yes into a percentage would mean inventing the
    constant that converts them, and no jornada has been played to fit it.
    """
    if not row:
        return "—"
    pct = row.get("start_pct")
    if pct not in (None, ""):
        return f"{float(pct):.0f}%"
    return "titular" if row.get("role") == "starter" else "?"


def second_cells(who, source: str = SECOND_SOURCE, rows=None, xw=None):
    """{market key: the second source's row}, plus the names it could not join.

    `who` is (key, name) pairs. It used to be names alone, keyed by norm(),
    and the docstring said a caller could look up p["key"] because key WAS
    norm(name) "by construction". That construction ended when the market
    started keying on the site's own id: every caller looked up an id in a
    dict of names and got nothing, so the second-source column read "—" for
    all 654 players and no test noticed, because the column is only in
    METHOD.md and "—" is a legitimate value.

    The join itself goes by slug first — the same identifier ffcore.score
    uses for the same rows — and falls back to the name.

    `rows` is for the selftest: pass a list and no CSV is read.
    """
    if rows is None:
        rows = latest_only(load_lineups(source))
    if xw is None:
        from ffcore.tidy import load_crosswalk
        xw = load_crosswalk()
    if xw is False:            # the self-test: no table, name join only
        xw = None
    by_slug = {}
    for r in rows:
        slug = norm(r.get("player_slug") or "")
        if not slug:
            continue
        pid = xw.player(ff_slug=r.get("player_slug")) if xw else None
        by_slug[slug] = (pid, r)
    cells: dict = {}
    unclear: list[tuple[str, list[str]]] = []
    seen: set = set()
    for key, name in who:
        if not key or key in seen:
            continue
        seen.add(key)
        hit = [r for s, (k, r) in by_slug.items() if k == key]
        if hit:
            cells[key] = hit[0]
            continue
        row, cands = resolve(name, rows, key="player_name")
        if row:
            cells[key] = row
        elif cands:
            unclear.append((name, [c["player_name"] for c in cands]))
    return cells, unclear


# ---------------------------------------------------------------------------
# selftest — the cell and the join, no IO
# ---------------------------------------------------------------------------

def _selftest() -> None:
    # -- the cell, in the source's own units -------------------------------
    assert af_cell(None) == "—"                      # not joined at all
    assert af_cell({}) == "—"
    assert af_cell({"role": "starter", "start_pct": ""}) == "titular"
    # A named starter is never dressed up as 100%: there is no fitted constant
    # to convert a final call into a probability.
    assert "%" not in af_cell({"role": "starter", "start_pct": ""})
    assert af_cell({"role": "starter", "start_pct": "100.0"}) == "100%"
    assert af_cell({"role": "doubt", "start_pct": "66.7"}) == "67%"
    assert af_cell({"role": "doubt", "start_pct": ""}) == "?"

    # -- the join ----------------------------------------------------------
    rows = [{"player_name": "Ane Aldea", "role": "starter", "start_pct": ""},
            {"player_name": "Bo Bidal", "role": "doubt", "start_pct": "50"},
            {"player_name": "Cai Coro Uno", "role": "starter",
             "start_pct": ""},
            {"player_name": "Cai Coro Dos", "role": "doubt",
             "start_pct": "25"}]

    # (market key, display name) pairs — the key is what the caller holds
    # and what the answer is filed under, so the two cannot drift apart.
    cells, unclear = second_cells([("101", "Ane Aldea"), ("102", "Bo Bidal"),
                                   ("103", "Didi Duna")], rows=rows, xw=False)
    assert af_cell(cells.get("101")) == "titular"
    assert af_cell(cells.get("102")) == "50%"
    # A name the source does not carry gets no cell and no complaint: silence
    # is not ambiguity.
    assert "103" not in cells and unclear == [], unclear

    # Two candidates are reported, never picked between — a wrong player
    # silently costs money.
    cells, unclear = second_cells([("104", "Cai Coro")], rows=rows, xw=False)
    assert cells == {}, cells
    assert unclear == [("Cai Coro", ["Cai Coro Uno", "Cai Coro Dos"])], unclear

    # The same player arriving twice (squad and slate both list him) is one
    # lookup and one entry, not a duplicated ambiguity report.
    cells, unclear = second_cells([("104", "Cai Coro"), ("104", "cai coro"),
                                   ("", "")], rows=rows, xw=False)
    assert len(unclear) == 1, unclear

    print("ffcore.second selftest OK (14 cases)")


if __name__ == "__main__":
    _selftest()
