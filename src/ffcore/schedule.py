"""
ffcore/schedule.py — each player's OWN remaining-jornada schedule, and
patching a squad short a legal position.

Split out of decide.py 2026-09-15 (rationalization plan session 4):
these six functions have zero external call sites outside decide.load()
and decide.apply() — a self-contained concern, not decide.py's own.
Why: docs/notes/decide.md#rounds_left--a-jornada-with-some-scores-in-still-counts
"""

from __future__ import annotations

from ffcore.crosswalk import club_key


def rounds_left(matches, teams) -> tuple[list[int], dict[int, set[str]], list]:
    """(jornadas still to come, who has already played one, unjoined clubs).

    A jornada with every score in is finished, not simulated. A
    partially-played one stays in `rem`, with its finished clubs
    dropped from `played`. `teams` is the market's own club spellings
    (club_key()). Not modelled: a round in progress has its XI already
    LOCKED, but the simulator re-picks it from whoever's left.
    Why: docs/notes/decide.md#rounds_left--a-jornada-with-some-scores-in-still-counts
    """
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
    """Bootstrap's own `per_jornada` — `base` for a player's FIRST
    remaining jornada, `base_rest` for every one after it.

    `base` carries this week's editorial reading (a suspension, a
    knock) — real news for the one game it was published for, not for
    every future one. "First remaining jornada" is PER PLAYER: a
    partial round drops a player once his own club has played it (see
    rounds_left()), so his true next jornada is wherever `played` first
    shows his club clear.
    Why: docs/notes/decide.md#next_then_rest--apply_fixtures
    """
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
    """{key: the first jornada in `rem` that is genuinely HIS next one} —
    the same per-player tracking next_then_rest() does internally,
    pulled out so apply_fixtures() can ask "is THIS his status
    override's jornada" without duplicating next_then_rest()'s
    scheduling logic inline.
    """
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
    """`per_jornada`, with the POINTS half repriced against THAT
    jornada's real opponent (season_board()) instead of the single
    next-fixture factor `base`/`base_rest` were built with. P(start) is
    untouched — next_then_rest() already answers that question. A
    player season_board() has no Match for keeps his frozen
    next-fixture number.

    `status_of`/`first_jornada_of` (both optional, default {}): applies
    ffcore.profile.status_adjusted() at exactly first_jornada_of[k] — a
    suspension or knock doesn't follow a player to jornada 20, the same
    reasoning next_then_rest() uses for base vs base_rest.
    Why: docs/notes/decide.md#next_then_rest--apply_fixtures
    """
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
    """`sq`, topped up to SLOT_MIN with generic phantom keys, or `sq`
    itself unchanged if nothing is short.

    THE SQUAD-SIDE HALF of phantom_fill() — `apply()` calls this too, since
    a squad legal at report time can be left short by a LATER transfer
    (a raid takes a rival's last player at his position minimum) with
    nothing else re-checking SLOT_MIN; a squad that fails it scores zero
    every remaining jornada in rank()'s simulation. `__phantom_<slot>_<n>`
    keys are the SAME ones phantom_fill() already registered real
    per_jornada data for at load time, so this never has to invent new
    forecaster data mid-run.
    """
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
    """Squads and per_jornada, with one AVERAGE-PLAYER-AT-THE-POSITION
    phantom added per position any manager is short of SLOT_MIN in —
    and real per_jornada data registered for EVERY SLOT_MIN slot
    regardless of who is short today, so a later transfer (via
    phantom_topup()) can reach for one too.

    Without this, a squad short one SLOT_MIN position can't fill ANY
    legal formation — best_xi() returns [], scoring zero every
    remaining jornada with zero variance. The phantom is an AVERAGE,
    not a specific player, computed off the same real per-jornada data
    every other player at that position carries; no `matches` entry, as
    for a brand-new player. Keyed `__phantom_<slot>_<n>` (no manager in
    the key) — a virtual average player has no real ownership to
    distinguish, so every squad short the same position shares it.
    Applies to every manager, rivals included — not because we check
    whether a rival is careless, but the opposite: ASSUME he'd competently
    field or buy someone, so his data gap doesn't crash his simulated
    score to zero and silently flatter my own win probability.
    Why: docs/notes/decide.md#phantom_fill--why-a-short-squad-gets-a-phantom-and-why-its-an-average
    Why: docs/notes/decide.md#optimize-for-competent-play-warn-dont-model-for-incompetent-play
    """
    from ffcore.score import SLOT_MIN

    squads = {m: dict(sq) for m, sq in squads.items()}
    per_jornada = {j: dict(layer) for j, layer in per_jornada.items()}
    # ONE AVERAGE PER (jornada, position) off every REAL scored player
    # at that position — not per manager, so every squad short the same
    # position shares the identical, real, jornada-varying number.
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

    # EVERY SLOT_MIN KEY, EVERY JORNADA, UNCONDITIONALLY — phantom_topup()
    # can only assign a key into a squad, not invent forecaster data,
    # so whatever it might need has to already be here.
    for j in per_jornada:
        for s, n in SLOT_MIN.items():
            if s not in avg.get(j, {}):
                continue
            for i in range(n):
                per_jornada[j].setdefault("__phantom_%s_%d" % (s, i), avg[j][s])

    squads = {m: phantom_topup(sq) for m, sq in squads.items()}
    return squads, per_jornada


def _selftest() -> None:
    # -- rounds_left: a round already half played -----------------------
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

    # -- next_then_rest: this week's status answers for ONE jornada -----
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

    # -- apply_fixtures: the REAL opponent, per jornada, not the frozen one
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

    # -- apply_fixtures: the status override survives the repricing, real
    # bug 2026-09-13 (Miguel: "no booked player is playing so shouldn't
    # they have 0% end odds to play next game?") ------------------------
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

    # -- first_jornada_per_player -----------------------------------------
    fjp = first_jornada_per_player(base2, rem2, played2, club2)
    assert fjp == {"susp": 1, "normal": 2}, fjp

    # -- phantom_fill(): a squad short a position gets ONE average-player
    # stand-in per missing slot, not frozen at zero for the rest of the
    # season -----------------------------------------------------------
    ph_sq = {"m": {"d1": "DEF", "d2": "DEF", "x1": "MED", "x2": "MED",
                   "x3": "MED", "p1": "POR", "f1": "DEL"}}   # 2 DEF, short 1
    ph_pos = {"d1": "DEF", "d2": "DEF", "other_def": "DEF",
              "x1": "MED", "x2": "MED", "x3": "MED", "p1": "POR", "f1": "DEL"}
    ph_per = {1: {"d1": (4.0, 1.0), "d2": (2.0, 0.5),
                  "other_def": (6.0, 0.5), "x1": (3.0, 1.0)}}
    new_sq, new_per = phantom_fill(ph_sq, ph_per, ph_pos)
    phantom_keys = [k for k in new_sq["m"] if k.startswith("__phantom_")]
    assert len(phantom_keys) == 1, phantom_keys          # short exactly 1 DEF
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
    assert "__phantom_DEF_2" in filled_per[1], filled_per[1]  # SLOT_MIN DEF=3

    # -- phantom_topup(): the same patch, reachable after a REAL transfer --
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
                "x2": "MED", "x3": "MED", "f1": "DEL"}   # 0 POR, needs 1
    assert phantom_topup(short_por).get("__phantom_POR_0") == "POR"

    print("ffcore.schedule self-test OK (28 cases)")


if __name__ == "__main__":
    _selftest()
