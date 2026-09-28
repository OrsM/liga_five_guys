
from __future__ import annotations

from datetime import date, timedelta

from ffcore.fixture import season_board
from ffcore.locks import JornadaClock
from ffcore.rules import FREE_FORMATIONS, MAX_SLOT


def rounds_left(matches) -> tuple[list[int], dict[int, set[str]]]:

    js = {r["jornada"] for r in matches if (r.get("jornada") or "").isdigit()}
    finished = {j for j in js
                if all(r.get("score") for r in matches if r["jornada"] == j)}
    open_j = {int(j) for j in js - finished}
    clock_order = [j for j in JornadaClock(matches).order if j in open_j]
    rem = clock_order + sorted(open_j - set(clock_order))

    played: dict[int, set[str]] = {}
    for r in matches:
        j = r.get("jornada") or ""
        if j.isdigit() and int(j) in rem and r.get("score"):
            played.setdefault(int(j), set()).update(
                c for c in (r.get("home"), r.get("away")) if c)
    return rem, played


UNSCORED_DEFAULT = (2.0, 0.5)


def jornada_expectation(r, match, first: bool, fit: float = 1.0
                        ) -> tuple[float, float]:
    """(points if he plays, chance he plays): points per match times the
    fixture, and the chance he is picked if fit times the chance he is fit."""
    fix = 1.0 if match is None else (
        match.def_factor if r.slot in ("POR", "DEF") else match.atk_factor)
    return max(0.0, r.ppm * fix), (r.p_now if first else r.p_rest) * fit


def jornada_dates(matches: list[dict], rem: list[int]) -> dict[int, date]:
    clock = JornadaClock(matches)
    known = {j: when.date() for j in rem if (when := clock.round_lock(j))}
    return {j: known[j] if j in known else known[near] + timedelta(weeks=j - near)
            for j in rem if known
            for near in [min(known, key=lambda k: abs(k - j))]}


def season(rates: dict, club: dict[str, str], rem: list[int],
           played: dict[int, set[str]], board: dict[int, dict],
           fit=None) -> tuple[dict[int, dict], dict[str, int]]:
    per_j: dict[int, dict] = {}
    first_of: dict[str, int] = {}
    for j in rem:
        layer = {}
        for k, r in rates.items():
            if club.get(k) in played.get(j, ()):
                continue
            first = k not in first_of
            if first:
                first_of[k] = j
            layer[k] = UNSCORED_DEFAULT if r is None else jornada_expectation(
                r, board.get(j, {}).get(club.get(k)), first,
                fit(k, j, first_of[k]) if fit else 1.0)
        per_j[j] = layer
    return per_j, first_of


def expectations(sc, ratings, keys, matches: list[dict]
                 ) -> tuple[dict[int, dict], dict[str, int], dict, list[int],
                            dict[int, set[str]]]:

    rem, played = rounds_left(matches)
    rates = {k: sc.rates(sc.lookup[k]) if k in sc.lookup else None
             for k in keys}
    club = {k: sc.lookup[k].get("club") for k in keys if k in sc.lookup}
    dates = jornada_dates(matches, rem)
    per_j, first_of = season(
        rates, club, rem, played, season_board(ratings, matches, rem),
        lambda k, j, first: sc.fit(k, j, dates.get(j), first))
    return per_j, first_of, rates, rem, played


def phantom_topup(sq: dict[str, str]) -> dict[str, str]:

    counts: dict[str, int] = {}
    for slot in sq.values():
        counts[slot] = counts.get(slot, 0) + 1

    best = None
    for d, m, f in FREE_FORMATIONS:
        want = {"POR": 1, "DEF": d, "MED": m, "DEL": f}
        short = {s: n - counts.get(s, 0) for s, n in want.items()
                if n - counts.get(s, 0) > 0}
        cost = sum(short.values())
        if best is None or cost < best[0]:
            best = (cost, short)
    if not best or not best[1]:
        return sq
    sq = dict(sq)
    for s, n in best[1].items():
        for i in range(n):
            sq["__phantom_%s_%d" % (s, i)] = s
    return sq


def phantom_fill(squads: dict[str, dict[str, str]], per_jornada: dict[int, dict],
                 pos: dict[str, str]
                 ) -> tuple[dict[str, dict[str, str]], dict[int, dict]]:

    squads = {m: dict(sq) for m, sq in squads.items()}
    per_jornada = {j: dict(layer) for j, layer in per_jornada.items()}
    avg: dict[int, dict[str, tuple[float, float]]] = {}
    for j, layer in per_jornada.items():
        by_pos: dict[str, list[tuple[float, float]]] = {}
        for k, (pts, p) in layer.items():
            s = pos.get(k)
            if s:
                by_pos.setdefault(s, []).append((pts, p))
        avg[j] = {s: (sum(v[0] for v in vs) / len(vs),
                     sum(v[1] for v in vs) / len(vs))
                 for s, vs in by_pos.items() if vs}

    for j in per_jornada:
        for s, n in MAX_SLOT.items():
            if s not in avg.get(j, {}):
                continue
            for i in range(n):
                per_jornada[j].setdefault("__phantom_%s_%d" % (s, i), avg[j][s])

    squads = {m: phantom_topup(sq) for m, sq in squads.items()}
    return squads, per_jornada


def _selftest() -> None:
    from ffcore.score import Rates as R

    fixt = [{"jornada": "8", "home": "a", "away": "b",
             "kickoff": "2026-10-11T14:00:00+00:00", "score": ""},
            {"jornada": "9", "home": "a", "away": "c", "kickoff": "", "score": ""}]
    got = jornada_dates(fixt, [8, 9, 10])
    assert got == {8: date(2026, 10, 11), 9: date(2026, 10, 18),
                   10: date(2026, 10, 25)}, got
    assert jornada_dates([], [8]) == {}
    hurt = R("k", "DEL", 4.0, 0.6, 0.8, 10.0)
    assert jornada_expectation(hurt, None, True) == (4.0, 0.6), "fit unless told"
    assert jornada_expectation(hurt, None, True, 0.5) == (4.0, 0.3), \
        "chance he plays = picked if fit x fit"
    assert jornada_expectation(hurt, None, False, 0.5) == (4.0, 0.4)

    ms = [{"jornada": "1", "home": "alaves", "away": "getafe", "score": "3-0"},
          {"jornada": "1", "home": "celta", "away": "osasuna", "score": ""},
          {"jornada": "2", "home": "alaves", "away": "celta", "score": ""},
          {"jornada": "3", "home": "alaves", "away": "getafe", "score": "1-1"},
          {"jornada": "", "home": "alaves", "away": "celta", "score": ""}]
    rem, done = rounds_left(ms)
    assert rem == [1, 2], rem
    assert done == {1: {"alaves", "getafe"}}, done

    from ffcore.fixture import Match
    from ffcore.score import Rates

    easy, hard = Match(1.2, 1.1), Match(0.8, 0.7)
    rates = {"del": Rates("del", "DEL", 8.0, 0.9, 0.7, 10.0),
             "por": Rates("por", "POR", 4.0, 0.9, 0.9, 10.0),
             "susp": Rates("susp", "DEL", 8.0, 0.9, 0.8, 5.0),
             "dbt": Rates("dbt", "MED", 5.0, 0.8, 0.8, 5.0),
             "ghost": None}
    club = {"del": "mine", "por": "mine", "susp": "mine", "dbt": "mine",
            "ghost": "other"}
    board = {1: {"mine": easy}, 2: {"mine": hard}, 3: {}}
    hurt_now = {"susp": 0.0, "dbt": 0.5}
    per_j, first_of = season(rates, club, [1, 2, 3], {}, board,
                             lambda k, j, first: hurt_now[k] if k in hurt_now
                             and j == first else 1.0)
    assert per_j[1]["del"] == (8.0 * 1.2, 0.9), per_j[1]
    assert per_j[1]["por"] == (4.0 * 1.1, 0.9), per_j[1]
    assert per_j[2]["del"] == (8.0 * 0.8, 0.7), per_j[2]
    assert per_j[3]["del"] == (8.0, 0.7), per_j[3]
    assert per_j[1]["susp"] == (8.0 * 1.2, 0.0), per_j[1]["susp"]
    assert per_j[2]["susp"] == (8.0 * 0.8, 0.8), "a suspension is one match"
    assert per_j[1]["dbt"] == (5.0 * 1.2, 0.8 * 0.5), per_j[1]["dbt"]
    assert per_j[1]["ghost"] == per_j[2]["ghost"] == UNSCORED_DEFAULT
    assert first_of == dict.fromkeys(rates, 1), first_of

    later, later_first = season(rates, club, [1, 2], {1: {"mine"}}, board)
    assert set(later[1]) == {"ghost"} and later[2]["del"] == (8.0 * 0.8, 0.9)
    assert later_first["del"] == 2 and later_first["ghost"] == 1
    assert season(rates, club, [], {}, board) == ({}, {})

    ph_sq = {"m": {"d1": "DEF", "d2": "DEF", "x1": "MED", "x2": "MED",
                   "x3": "MED", "p1": "POR", "f1": "DEL"}}
    ph_pos = {"d1": "DEF", "d2": "DEF", "other_def": "DEF",
              "x1": "MED", "x2": "MED", "x3": "MED", "p1": "POR", "f1": "DEL"}
    ph_per = {1: {"d1": (4.0, 1.0), "d2": (2.0, 0.5),
                  "other_def": (6.0, 0.5), "x1": (3.0, 1.0)}}
    new_sq, new_per = phantom_fill(ph_sq, ph_per, ph_pos)
    phantoms = sorted(k for k in new_sq["m"] if k.startswith("__phantom_"))
    assert phantoms == ["__phantom_DEF_0", "__phantom_DEF_1",
                        "__phantom_DEF_2", "__phantom_MED_0"], phantoms
    assert new_per[1]["__phantom_DEF_0"] == (4.0, (1.0 + 0.5 + 0.5) / 3)
    assert "__phantom_DEF_0" not in ph_sq["m"] and \
        "__phantom_DEF_0" not in ph_per[1], "inputs must not be mutated"

    eleven = {"p1": "POR", **{"d%d" % i: "DEF" for i in range(4)},
              **{"x%d" % i: "MED" for i in range(4)}, "f1": "DEL", "f2": "DEL"}
    for squad, added in [
            (eleven, {}),
            ({k: v for k, v in eleven.items() if k != "p1"}, {"POR": 1}),
            ({k: v for k, v in eleven.items() if k not in ("d0", "d1")},
             {"DEF": 2}),
            (ph_sq["m"], {"DEF": 3, "MED": 1})]:
        topped = phantom_topup(squad)
        got = {}
        for k, slot in topped.items():
            if k.startswith("__phantom_"):
                got[slot] = got.get(slot, 0) + 1
        assert got == added, (squad, got)
        assert all(topped[k] == v for k, v in squad.items())

    print("ffcore.schedule self-test OK")


if __name__ == "__main__":
    _selftest()
