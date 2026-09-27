
from __future__ import annotations

from dataclasses import dataclass

from ffcore.crosswalk import Player

__all__ = ["PlayerCurrent",
          "PlayerDerived", "PlayerProfile", "build_profiles", "mk_profile",
          "status_adjusted"]


@dataclass
class PlayerCurrent:
    club: str = ""
    pos: str = ""
    status: str = ""
    listed: bool = False
    price: float | None = None
    owner: str | None = None
    value: float | None = None
    route: str | None = None
    proceeds: float | None = None


def status_adjusted(pts: float, p_start: float, status: str,
                    factors: dict | None = None) -> tuple[float, float]:
    from ffcore.score import OUT_STATUSES, status_multiplier

    mult = status_multiplier(status, factors)
    if status in OUT_STATUSES:
        return pts, p_start * mult
    return pts * mult, p_start


@dataclass
class PlayerDerived:
    ppm: float | None = None
    pj: float = 0.0
    start_p: float | None = None
    market_exp: float | None = None
    pts_now: float | None = None
    scored: object = None


UNSCORED_DEFAULT = (2.0, 0.5)


@dataclass
class PlayerProfile:
    identity: Player
    current: PlayerCurrent
    derived: PlayerDerived

    def to_bootstrap_input(self) -> tuple[tuple[float, float],
                                          tuple[float, float]]:
        s = self.derived.scored
        if s is None:
            return UNSCORED_DEFAULT, UNSCORED_DEFAULT
        pts_rest = max(0.0, s.ppm * s.fix)
        p_rest = min(1.0, (s.pct_rest or 0) / 100)
        return ((self.derived.pts_now, self.derived.start_p),
               (pts_rest, p_rest))


def build_profiles(players: dict, sc, xw=None,
                   market_keyed: dict | None = None) -> dict[str, "PlayerProfile"]:
    out: dict[str, PlayerProfile] = {}
    for k, rec in players.items():
        xp = xw.players.get(k) if xw is not None else None
        ident = xp if xp is not None else Player(player_id=k)
        ident.name = rec.get("name") or ident.name or k
        mk = (market_keyed or {}).get(k, {})
        cur = PlayerCurrent(
            club=rec.get("club") or (xp.club_id if xp else ""),
            pos=(rec.get("pos") or "").upper(),
            listed=bool(mk.get("listed")),
            price=mk.get("price"),
            owner=mk.get("owner"),
            value=mk.get("value"),
            route=mk.get("route"),
            proceeds=mk.get("proceeds"),
        )
        row = sc.lookup.get(k)
        s = sc.score(row) if row else None
        if s is not None:
            pts_adj, p_start_adj = status_adjusted(
                max(0.0, s.ppm * s.fix), min(1.0, (s.pct_used or 0) / 100),
                s.status, sc.cal.status_factor)
        else:
            pts_adj = p_start_adj = None
        der = PlayerDerived(
            ppm=s.ppm if s else None,
            pj=s.pj if s else 0.0,
            start_p=p_start_adj,
            market_exp=(pts_adj * p_start_adj) if s else None,
            pts_now=pts_adj,
            scored=s,
        )
        if s is not None:
            cur.status = s.status
        out[k] = PlayerProfile(identity=ident, current=cur, derived=der)
    return out


def mk_profile(pj: float, pos: str = "MED", price=None, name: str = "",
               market_exp=None) -> PlayerProfile:
    return PlayerProfile(
        identity=Player(player_id="x", name=name),
        current=PlayerCurrent(pos=pos, price=price, listed=price is not None),
        derived=PlayerDerived(pj=pj, market_exp=market_exp))


def _selftest() -> None:
    from types import SimpleNamespace
    from ffcore.crosswalk import Crosswalk, Player
    from ffcore.score import Scored
    from ffcore.startprob import Calibration

    scored = Scored(name="Known Player", key="999", slot="DEL", pos="delantero",
                    score=0.0, flat=0.0, ppm=6.0, pct=None, pct_used=80.0,
                    pct_rest=60.0, on_page=True, status="ok", assumed=False,
                    value=0.0, fix=1.1, pj=12.0)
    fake_sc = SimpleNamespace(cal=Calibration(), lookup={"999": "999"},
                              score={"999": scored}.get)
    players = {"999": {"name": "Known Player", "pos": "DEL", "club": "betis"},
               "unknown": {"name": "Unknown Player", "pos": "MED",
                           "club": "celta"}}
    xw = Crosswalk({"999": Player("999", "Known Player", club_id="betis",
                                  app_id="app-999", understat_id="us-999")})
    profiles = build_profiles(players, fake_sc, xw=xw,
                              market_keyed={"999": {"listed": True,
                                                     "price": 5e6,
                                                     "owner": "alice"}})

    assert set(profiles) == {"999", "unknown"}, profiles
    k = profiles["999"]
    assert k.identity.app_id == "app-999", k.identity
    assert k.identity.understat_id == "us-999", k.identity
    assert k.identity.name == "Known Player"
    assert k.current.club == "betis" and k.current.listed is True
    assert k.current.price == 5e6 and k.current.owner == "alice"
    assert k.current.status == "ok"
    assert k.derived.ppm == 6.0 and k.derived.pj == 12.0
    assert abs(k.derived.start_p - 0.8) < 1e-9
    assert abs(k.derived.market_exp - 5.28) < 1e-9
    this_j, rest = k.to_bootstrap_input()
    assert abs(this_j[0] - 6.6) < 1e-9 and abs(this_j[1] - 0.8) < 1e-9, this_j
    assert abs(rest[0] - 6.6) < 1e-9 and abs(rest[1] - 0.6) < 1e-9, rest

    from ffcore.score import DOUBT_FACTOR

    susp_sc = SimpleNamespace(
        cal=Calibration(), lookup={"999": "999"},
        score={"999": scored._replace(status="suspended")}.get)
    susp_profiles = build_profiles(players, susp_sc, xw=xw,
                                   market_keyed={"999": {"listed": True,
                                                          "price": 5e6,
                                                          "owner": "alice"}})
    ks = susp_profiles["999"]
    assert ks.derived.start_p == 0.0, ks.derived.start_p
    assert ks.derived.market_exp == 0.0, ks.derived.market_exp
    assert ks.derived.ppm == 6.0
    susp_this, susp_rest = ks.to_bootstrap_input()
    assert abs(susp_this[0] - 6.6) < 1e-9 and susp_this[1] == 0.0, susp_this
    assert abs(susp_rest[0] - 6.6) < 1e-9 \
        and abs(susp_rest[1] - 0.6) < 1e-9, susp_rest

    doubt_sc = SimpleNamespace(
        cal=Calibration(), lookup={"999": "999"},
        score={"999": scored._replace(status="doubt")}.get)
    doubt_profiles = build_profiles(players, doubt_sc, xw=xw,
                                    market_keyed={"999": {"listed": True,
                                                          "price": 5e6,
                                                          "owner": "alice"}})
    kd = doubt_profiles["999"]
    assert abs(kd.derived.pts_now - 6.6 * DOUBT_FACTOR) < 1e-9, \
        kd.derived.pts_now
    assert kd.derived.start_p == 0.8, kd.derived.start_p
    doubt_this, doubt_rest = kd.to_bootstrap_input()
    assert abs(doubt_this[0] - 6.6 * DOUBT_FACTOR) < 1e-9, doubt_this
    assert abs(doubt_this[1] - 0.8) < 1e-9, doubt_this
    assert abs(doubt_rest[0] - 6.6) < 1e-9, doubt_rest

    u = profiles["unknown"]
    assert u.identity.player_id == "unknown" and u.current.club == "celta"
    assert u.derived.ppm is None and u.derived.market_exp is None
    assert u.current.listed is False and u.current.price is None
    assert u.to_bootstrap_input() == (UNSCORED_DEFAULT, UNSCORED_DEFAULT)

    print("ffcore.profile self-test OK")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
