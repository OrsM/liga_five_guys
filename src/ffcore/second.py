
from __future__ import annotations

from ffcore.text import norm, resolve
from ffcore.tidy import latest_only, load_lineups

__all__ = ["SECOND_SOURCE", "FF_AF_HEAD", "LEGEND", "af_cell",
          "second_cells", "resolve_second_source"]

SECOND_SOURCE = "analitica"

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
    if not row:
        return "—"
    pct = row.get("start_pct")
    if pct not in (None, ""):
        return f"{float(pct):.0f}%"
    return "titular" if row.get("role") == "starter" else "?"


def resolve_second_source(rows, xw=None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        pid = xw.key_of(r) if xw else None
        key = pid or norm(r.get("player_name") or r.get("player_slug") or "")
        if key:
            out[key] = r
    return out


def second_cells(who, source: str = SECOND_SOURCE, rows=None, xw=None):
    if rows is None:
        rows = latest_only(load_lineups(source))
    if xw is None:
        from ffcore.tidy import load_crosswalk
        xw = load_crosswalk()
    if xw is False:
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



def _selftest() -> None:
    assert af_cell(None) == "—"
    assert af_cell({}) == "—"
    assert af_cell({"role": "starter", "start_pct": ""}) == "titular"
    assert "%" not in af_cell({"role": "starter", "start_pct": ""})
    assert af_cell({"role": "starter", "start_pct": "100.0"}) == "100%"
    assert af_cell({"role": "doubt", "start_pct": "66.7"}) == "67%"
    assert af_cell({"role": "doubt", "start_pct": ""}) == "?"

    rows = [{"player_name": "Ane Aldea", "role": "starter", "start_pct": ""},
            {"player_name": "Bo Bidal", "role": "doubt", "start_pct": "50"},
            {"player_name": "Cai Coro Uno", "role": "starter",
             "start_pct": ""},
            {"player_name": "Cai Coro Dos", "role": "doubt",
             "start_pct": "25"}]

    cells, unclear = second_cells([("101", "Ane Aldea"), ("102", "Bo Bidal"),
                                   ("103", "Didi Duna")], rows=rows, xw=False)
    assert af_cell(cells.get("101")) == "titular"
    assert af_cell(cells.get("102")) == "50%"
    assert "103" not in cells and unclear == [], unclear

    cells, unclear = second_cells([("104", "Cai Coro")], rows=rows, xw=False)
    assert cells == {}, cells
    assert unclear == [("Cai Coro", ["Cai Coro Uno", "Cai Coro Dos"])], unclear

    cells, unclear = second_cells([("104", "Cai Coro"), ("104", "cai coro"),
                                   ("", "")], rows=rows, xw=False)
    assert len(unclear) == 1, unclear

    print("ffcore.second selftest OK (14 cases)")


if __name__ == "__main__":
    _selftest()
