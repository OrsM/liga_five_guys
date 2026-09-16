
from __future__ import annotations

from ffcore.crosswalk import club_key


def rounds_left(matches, teams) -> tuple[list[int], dict[int, set[str]], list]:
    js = {r["jornada"] for r in matches if (r.get("jornada") or "").isdigit()}
    finished = {j for j in js
                if all(r.get("score") for r in matches if r["jornada"] == j)}
    rem = sorted(int(j) for j in js - finished)

    played: dict[int, set[str]] = {}
    unjoined: list[str] = []
    for r in matches:
        j = r.get("jornada") or ""
        if not j.isdigit() or int(j) not in rem or not r.get("score"):
            continue
        for side in (r.get("home"), r.get("away")):
            club = club_key(side, teams)
            if not club:
                if side and side not in unjoined:
                    unjoined.append(side)
                continue
            played.setdefault(int(j), set()).add(club)
    return rem, played, unjoined


def next_then_rest(base: dict, base_rest: dict, rem: list[int],
                   played: dict[int, set[str]], club: dict[str, str]
                   ) -> dict[int, dict]:
    first_seen: set[str] = set()
    out: dict[int, dict] = {}
    for j in rem:
        this_j = ({k: v for k, v in base.items()
                  if club.get(k) not in played[j]}
                 if j in played else base)
        layer = {}
        for k, v in this_j.items():
            if k in first_seen:
                layer[k] = base_rest.get(k, v)
            else:
                first_seen.add(k)
                layer[k] = v
        out[j] = layer
    return out


def first_jornada_per_player(base: dict, rem: list[int],
                             played: dict[int, set[str]],
                             club: dict[str, str]) -> dict[str, int]:
    first_seen: set[str] = set()
    out: dict[str, int] = {}
    for j in rem:
        this_j = ({k for k in base if club.get(k) not in played[j]}
                 if j in played else set(base))
        for k in this_j:
            if k not in first_seen:
                first_seen.add(k)
                out[k] = j
    return out


def apply_fixtures(per_jornada: dict[int, dict], sboard: dict[int, dict],
                   club: dict[str, str], pos: dict[str, str],
                   ppm_of: dict[str, float], status_of: dict = None,
                   first_jornada_of: dict = None) -> dict[int, dict]:
    from ffcore.profile import status_adjusted

    status_of = status_of or {}
    first_jornada_of = first_jornada_of or {}
    out: dict[int, dict] = {}
    for j, layer in per_jornada.items():
        board_j = sboard.get(j, {})
        new_layer = {}
        for k, (pts, p) in layer.items():
            m = board_j.get(club.get(k, ""))
            if m is None or k not in ppm_of:
                new_layer[k] = (pts, p)
                continue
            fix = (m.def_factor if pos.get(k) in ("POR", "DEF")
                  else m.atk_factor)
            new_pts, new_p = max(0.0, ppm_of[k] * fix), p
            if first_jornada_of.get(k) == j:
                new_pts, new_p = status_adjusted(new_pts, new_p,
                                                 status_of.get(k, ""))
            new_layer[k] = (new_pts, new_p)
        out[j] = new_layer
    return out


def phantom_topup(sq: dict[str, str]) -> dict[str, str]:
    from ffcore.score import SLOT_MIN

    counts: dict[str, int] = {}
    for slot in sq.values():
        counts[slot] = counts.get(slot, 0) + 1
    short = {s: n - counts.get(s, 0) for s, n in SLOT_MIN.items()
            if n - counts.get(s, 0) > 0}
    if not short:
        return sq
    sq = dict(sq)
    for s, n in short.items():
        for i in range(n):
            sq["__phantom_%s_%d" % (s, i)] = s
    return sq


def phantom_fill(squads: dict[str, dict[str, str]], per_jornada: dict[int, dict],
                 pos: dict[str, str]
                 ) -> tuple[dict[str, dict[str, str]], dict[int, dict]]:
    from ffcore.score import SLOT_MIN

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
        for s, n in SLOT_MIN.items():
            if s not in avg.get(j, {}):
                continue
            for i in range(n):
                per_jornada[j].setdefault("__phantom_%s_%d" % (s, i), avg[j][s])

    squads = {m: phantom_topup(sq) for m, sq in squads.items()}
    return squads, per_jornada


def _selftest() -> None:
    teams = ["Alavés", "Getafe", "Celta Vigo", "Osasuna", "Rayo"]
    ms = [{"jornada": "1", "home": "alaves", "away": "getafe", "score": "3-0"},
          {"jornada": "1", "home": "celta", "away": "osasuna", "score": ""},
          {"jornada": "2", "home": "alaves", "away": "celta", "score": ""},
          {"jornada": "3", "home": "alaves", "away": "getafe", "score": "1-1"},
          {"jornada": "", "home": "alaves", "away": "celta", "score": ""}]
    rem, done, unjoined = rounds_left(ms, teams)
    assert rem == [1, 2], rem
    assert done == {1: {"alaves", "getafe"}}, done
    assert unjoined == [], unjoined

    _r, _d, un = rounds_left(
        [{"jornada": "1", "home": "zzz-united", "away": "getafe",
          "score": "1-0"},
         {"jornada": "1", "home": "celta", "away": "osasuna", "score": ""}],
        teams)
    assert un == ["zzz-united"], un
    assert _d == {1: {"getafe"}}, _d

    rem2, played2 = [1, 2, 3], {1: {"alaves"}}
    base2 = {"susp": (5.0, 0.05), "normal": (4.0, 0.9)}
    rest2 = {"susp": (5.0, 0.9), "normal": (4.0, 0.9)}
    club2 = {"susp": "getafe", "normal": "alaves"}
    pj = next_then_rest(base2, rest2, rem2, played2, club2)
    assert pj[1] == {"susp": base2["susp"]}, pj[1]
    assert "normal" not in pj[1], pj[1]
    assert pj[2] == {"susp": rest2["susp"], "normal": base2["normal"]}, pj[2]
    assert pj[3] == {"susp": rest2["susp"], "normal": rest2["normal"]}, pj[3]

    pj2 = next_then_rest(base2, rest2, [1, 2], {}, {})
    assert pj2[1] == base2 and pj2[2] == rest2, pj2

    from ffcore.fixture import Match as _M

    easy_m = _M(opponent="Easy", home=True, kickoff=None,
               atk_factor=1.2, def_factor=1.1, rank=3, of=3)
    hard_m = _M(opponent="Hard", home=False, kickoff=None,
               atk_factor=0.8, def_factor=0.7, rank=1, of=3)
    pj3 = {1: {"del": (10.0, 0.9), "por": (5.0, 0.9), "ghost": (3.0, 0.5)},
          2: {"del": (10.0, 0.9), "por": (5.0, 0.9)}}
    board = {1: {"myclub": easy_m}, 2: {"myclub": hard_m}}
    club3 = {"del": "myclub", "por": "myclub", "ghost": "unjoinable"}
    pos3 = {"del": "DEL", "por": "POR"}
    ppm3 = {"del": 8.0, "por": 4.0}
    out = apply_fixtures(pj3, board, club3, pos3, ppm3)
    assert out[1]["del"] == (8.0 * 1.2, 0.9), out[1]["del"]
    assert out[1]["por"] == (4.0 * 1.1, 0.9), out[1]["por"]
    assert out[2]["del"] == (8.0 * 0.8, 0.9), out[2]["del"]
    assert out[1]["del"] != out[2]["del"], (out[1]["del"], out[2]["del"])
    assert out[1]["del"][1] == pj3[1]["del"][1] == 0.9
    assert out[1]["ghost"] == (3.0, 0.5), out[1]["ghost"]

    fjo = {"del": 1, "por": 2}
    status_of = {"del": "suspended", "por": "ok"}
    out_susp = apply_fixtures(pj3, board, club3, pos3, ppm3,
                              status_of=status_of, first_jornada_of=fjo)
    assert out_susp[1]["del"][1] == 0.0, out_susp[1]["del"]
    assert out_susp[2]["del"] == (8.0 * 0.8, 0.9), out_susp[2]["del"]
    assert out_susp[1]["por"] == (4.0 * 1.1, 0.9), out_susp[1]["por"]

    from ffcore.score import DOUBT_FACTOR
    status_doubt = {"del": "doubt"}
    out_doubt = apply_fixtures(pj3, board, club3, pos3, ppm3,
                               status_of=status_doubt, first_jornada_of=fjo)
    assert abs(out_doubt[1]["del"][0] - 8.0 * 1.2 * DOUBT_FACTOR) < 1e-9, \
        out_doubt[1]["del"]
    assert out_doubt[1]["del"][1] == 0.9, out_doubt[1]["del"]

    assert apply_fixtures(pj3, board, club3, pos3, ppm3) == out, out

    fjp = first_jornada_per_player(base2, rem2, played2, club2)
    assert fjp == {"susp": 1, "normal": 2}, fjp

    ph_sq = {"m": {"d1": "DEF", "d2": "DEF", "x1": "MED", "x2": "MED",
                   "x3": "MED", "p1": "POR", "f1": "DEL"}}
    ph_pos = {"d1": "DEF", "d2": "DEF", "other_def": "DEF",
              "x1": "MED", "x2": "MED", "x3": "MED", "p1": "POR", "f1": "DEL"}
    ph_per = {1: {"d1": (4.0, 1.0), "d2": (2.0, 0.5),
                  "other_def": (6.0, 0.5), "x1": (3.0, 1.0)}}
    new_sq, new_per = phantom_fill(ph_sq, ph_per, ph_pos)
    phantom_keys = [k for k in new_sq["m"] if k.startswith("__phantom_")]
    assert len(phantom_keys) == 1, phantom_keys
    pk = phantom_keys[0]
    assert new_sq["m"][pk] == "DEF", new_sq["m"]
    assert new_per[1][pk] == (4.0, (1.0 + 0.5 + 0.5) / 3), new_per[1][pk]
    assert pk == "__phantom_DEF_0", pk
    assert "__phantom_DEF_0" not in ph_sq["m"], ph_sq
    assert pk not in ph_per[1], ph_per[1]
    legal_sq = {"m2": {"p1": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF",
                       "x1": "MED", "x2": "MED", "x3": "MED", "f1": "DEL"}}
    same_sq, filled_per = phantom_fill(legal_sq, ph_per, ph_pos)
    assert same_sq == legal_sq, same_sq
    assert "__phantom_DEF_0" in filled_per[1], filled_per[1]
    assert "__phantom_DEF_2" in filled_per[1], filled_per[1]

    assert phantom_topup(legal_sq["m2"]) == legal_sq["m2"], "already legal"
    short_one = {"d1": "DEF", "d2": "DEF", "x1": "MED", "x2": "MED",
                "x3": "MED", "p1": "POR", "f1": "DEL"}
    topped = phantom_topup(short_one)
    assert topped != short_one, "must not mutate the caller's dict in place"
    assert short_one == {"d1": "DEF", "d2": "DEF", "x1": "MED", "x2": "MED",
                         "x3": "MED", "p1": "POR", "f1": "DEL"}, short_one
    assert topped.get("__phantom_DEF_0") == "DEF", topped
    assert sum(1 for k in topped if k.startswith("__phantom_")) == 1, topped
    short_por = {"d1": "DEF", "d2": "DEF", "d3": "DEF", "x1": "MED",
                "x2": "MED", "x3": "MED", "f1": "DEL"}
    assert phantom_topup(short_por).get("__phantom_POR_0") == "POR"

    print("ffcore.schedule self-test OK (28 cases)")


if __name__ == "__main__":
    _selftest()
