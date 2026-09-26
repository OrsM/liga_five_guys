
from __future__ import annotations



def rounds_left(matches, fixtures=()) -> tuple[list[int], dict[int, set[str]]]:
    from ffcore.tidy import JornadaClock

    js = {r["jornada"] for r in matches if (r.get("jornada") or "").isdigit()}
    finished = {j for j in js
                if all(r.get("score") for r in matches if r["jornada"] == j)}
    open_j = {int(j) for j in js - finished}
    clock_order = [j for j in JornadaClock(matches, fixtures).order
                  if j in open_j] if fixtures else []
    rem = clock_order + sorted(open_j - set(clock_order))

    played: dict[int, set[str]] = {}
    for r in matches:
        j = r.get("jornada") or ""
        if j.isdigit() and int(j) in rem and r.get("score"):
            played.setdefault(int(j), set()).update(
                c for c in (r.get("home"), r.get("away")) if c)
    return rem, played


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
                   first_jornada_of: dict = None,
                   status_factor: dict | None = None) -> dict[int, dict]:
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
                                                 status_of.get(k, ""),
                                                 status_factor)
            new_layer[k] = (new_pts, new_p)
        out[j] = new_layer
    return out


def phantom_topup(sq: dict[str, str]) -> dict[str, str]:
    from ffcore.score import formations

    counts: dict[str, int] = {}
    for slot in sq.values():
        counts[slot] = counts.get(slot, 0) + 1

    best = None
    for d, m, f in formations():
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
    from ffcore.score import MAX_SLOT

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
    ms = [{"jornada": "1", "home": "alaves", "away": "getafe", "score": "3-0"},
          {"jornada": "1", "home": "celta", "away": "osasuna", "score": ""},
          {"jornada": "2", "home": "alaves", "away": "celta", "score": ""},
          {"jornada": "3", "home": "alaves", "away": "getafe", "score": "1-1"},
          {"jornada": "", "home": "alaves", "away": "celta", "score": ""}]
    rem, done = rounds_left(ms)
    assert rem == [1, 2], rem
    assert done == {1: {"alaves", "getafe"}}, done

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

    print("ffcore.schedule self-test OK (28 cases)")


if __name__ == "__main__":
    _selftest()
