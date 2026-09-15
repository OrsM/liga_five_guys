"""
ffcore/second.py — the second probable-XI source, joined.

futbolfantasy (**FF**) is the primary — its percentage is the P(start)
inside every xPts/j in this repo. analiticafantasy (**AF**) is a second
opinion: printed BESIDE FF's own figure in the report (never blended into
that display), but ffcore.startprob.Calibration DOES fit a weight to
blend it into the actual forecast where it earns that out of sample — the
two uses read the same rows and share the identity join below, kept in
one place after they were found reimplementing it independently (one of
them, incorrectly: see resolve_second_source()'s own note).

The join is by identity (crosswalk first, name second) — a name with
several candidates is handed back to be printed, never guessed between.

    python src/ffcore/second.py     # selftest: the cell and the join, no IO
"""

from __future__ import annotations

from ffcore.text import norm, resolve
from ffcore.tidy import latest_only, load_lineups

__all__ = ["SECOND_SOURCE", "FF_AF_HEAD", "LEGEND", "af_cell",
          "second_cells", "resolve_second_source"]

SECOND_SOURCE = "analitica"

# The two columns, so every table spells them the same way and in the same
# order — primary first, second opinion second.
FF_AF_HEAD = "FF | AF"

LEGEND = ("**FF** is futbolfantasy's own probable-XI percentage. **AF** is "
          "analiticafantasy's read of the same eleven, printed beside it as "
          "its own column, never merged into FF's figure on this table — "
          "`titular` is a named starter (a final call, with no number to "
          "it), a percentage is their editors' consensus, `?` means they "
          "list him without either, and `—` means they do not have him. "
          "Two columns that disagree are the signal; that is the whole "
          "point of carrying both. (The FORECAST itself may still weigh AF "
          "in — see ffcore.startprob.Calibration — this table just never "
          "shows a blend.)")


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


def resolve_second_source(rows, xw=None) -> dict[str, dict]:
    """AF rows indexed by resolved player identity — the crosswalk key
    where a row's own slug or name resolves to one, its normalized name
    otherwise.

    THE SHARED JOIN. second_cells() (this file, for display) and
    ffcore.startprob.observations() (for calibration fitting) used to
    each reimplement this independently against the same rows — found
    2026-09-16 while asked why maintaining a second source costs so much
    code, not because either caller looked broken. They'd also drifted:
    second_cells()'s version passed `ff_slug=r.get("player_slug")` to
    Crosswalk.player() — checking AF's own slug against the FF index,
    which practically never hits — instead of `af_slug=`, silently
    falling through to name-matching far more than it needed to.
    """
    out: dict[str, dict] = {}
    for r in rows:
        pid = xw.player(af_slug=r.get("player_slug"),
                        name=r.get("player_name")) if xw else None
        key = pid or norm(r.get("player_name") or r.get("player_slug") or "")
        if key:
            out[key] = r
    return out


def second_cells(who, source: str = SECOND_SOURCE, rows=None, xw=None):
    """{market key: the second source's row}, plus the names it could not
    join. `who` is (key, name) pairs — the key is what the caller looks
    the answer up under, so it must be passed explicitly rather than
    re-derived from the name.

    The join goes by identity first (resolve_second_source(), crosswalk
    where it resolves), falling back to the name.

    `rows` is for the selftest: pass a list and no CSV is read.
    """
    if rows is None:
        rows = latest_only(load_lineups(source))
    if xw is None:
        from ffcore.tidy import load_crosswalk
        xw = load_crosswalk()
    if xw is False:            # the self-test: no table, name join only
        xw = None
    by_identity = resolve_second_source(rows, xw)
    cells: dict = {}
    unclear: list[tuple[str, list[str]]] = []
    seen: set = set()
    for key, name in who:
        if not key or key in seen:
            continue
        seen.add(key)
        hit = by_identity.get(key)
        if hit is not None:
            cells[key] = hit
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
