
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


from ffcore.bid import deals, low_priced_buys
from ffcore.render import title_name
from ffcore.score import SLOT_LABEL, SLOT_MIN, squad_pool
from ffcore.tidy import (run_now, shown,
                         ALERTS, DECISIONS,
                         age_phrase, append_csv, load_crosswalk,
                         load_deadline, read_csv,
                         snapshot_stamp, stale_feeds, widen_csv, write_lines)

WARNINGS = Path(os.environ.get("LFG_WARNINGS", ".runtime/warnings.json"))

STALE_HOURS = 14.0

LOG_COLS = ["observed_at", "hours_to_lock", "formation", "index_total",
            "ff_id", "player", "pos", "slot", "start_pct", "start_source", "status",
            "assumed", "value", "score", "picked",
            "ppm", "fix", "opp", "home", "cur_pj", "flat",
            "fix_basis", "elo_gap", "score_h3", "pj"]

def _score_h3(p: dict) -> float:
    rest_rate = p["ppm"] * (p["pct_rest"] / 100.0)
    return p["score"] + 2 * rest_rate


def squad_names(lg) -> tuple[list[str], str]:
    if lg is not None:
        mine = lg.managers.get(lg.cfg.me)
        if mine and mine.players:
            return (list(mine.players), "ledger")
    return [], "nothing"


def log_squad(observed, players, chosen, formation: str, total, deadline,
              obs_dt) -> None:
    path = DECISIONS / "squad_log.csv"
    widen_csv(path, LOG_COLS)
    if observed in {r.get("observed_at") for r in read_csv(path)}:
        return
    htl = ""
    if deadline and obs_dt:
        htl = f"{(deadline - obs_dt).total_seconds() / 3600:.1f}"
    rows = []
    for p in players:
        src = ("read" if p["pct"] is not None
               else "listed_blank" if p["on_page"] else "absent")
        rows.append({
            "observed_at": observed, "hours_to_lock": htl,
            "formation": formation,
            "index_total": f"{total:.2f}",
            "ff_id": p.get("key", ""),
            "player": p["name"], "pos": p["pos"], "slot": p["slot"],
            "start_pct": "" if p["pct"] is None else f"{p['pct']:.0f}",
            "start_source": src, "status": p["status"] or "ok",
            "assumed": int(bool(p["assumed"])),
            "value": f"{p['value']:.0f}", "score": f"{p['score']:.3f}",
            "picked": int(id(p) in chosen),
            "ppm": f"{p['ppm']:.3f}", "fix": f"{p['fix']:.3f}",
            "opp": p["opp"], "home": int(bool(p["home"])) if p["opp"] else "",
            "cur_pj": f"{p['cur_pj']:.0f}", "flat": f"{p['flat']:.3f}",
            "fix_basis": p.get("fix_basis") or "none",
            "elo_gap": ("" if p.get("elo_gap") is None
                        else f"{p['elo_gap']:.1f}"),
            "score_h3": f"{_score_h3(p):.3f}",
            "pj": f"{p['pj']:.1f}",
        })
    append_csv(path, rows, LOG_COLS)




def stale_feed_warnings(quiet=None) -> list[str]:
    quiet = stale_feeds() if quiet is None else quiet
    if not quiet:
        return []
    return ["**The app's own feed is %s stale** (%s) — squads, prices and "
            "balances below are the ledger's estimate, not the app's reading. "
            "The token may have expired: `python -m ffcore.auth --login`."
            % (age_phrase(max(quiet.values())), ", ".join(sorted(quiet)))]


def alerts(warnings, token_days) -> list[str]:
    out = ["**Squad** — %s" % w for w in warnings]
    if token_days is not None and token_days < 14:
        out.append("**Log in again** — the league token expires in %d days "
                   "(`python -m ffcore.auth --login`)" % token_days)
    return out


def main() -> None:
    from ffcore.model import session
    m = session()
    market = m.market

    if not market:
        print("no market data; nothing to warn about")
        return

    lg = m.lg
    sc = m.sc

    observed = market[0]["observed_at"]
    obs_dt = snapshot_stamp(observed)
    now = run_now()
    age_h = (now - obs_dt).total_seconds() / 3600 if obs_dt else None

    squad, _squad_src = squad_names(lg)
    scored, missing = sc.score_squad(squad)
    players = []
    for s in scored:
        row = s.as_row()
        row["name"] = title_name(row["name"])
        players.append(row)

    market_scored, _market_missing = sc.score_squad(sorted(sc.lookup))
    squad_keys = {p["key"] for p in players}
    market_players = []
    for s in market_scored:
        row = s.as_row()
        if row["key"] in squad_keys:
            continue
        row["name"] = title_name(row["name"])
        market_players.append(row)

    xw = load_crosswalk()
    pool = squad_pool(players)

    import decide
    import sim
    u = decide.load() if players else None
    xi = u.current_xi[1] if u else set()
    if players and xi:
        chosen = [p for p in players if p.get("key") in xi]
        # FORMATION IS sim.shape()'S JOB, NOT A SECOND COUNT OF THE SAME XI.
        # This used to re-derive DEF/MED/DEL counts from the scorer's rows,
        # a second implementation of exactly what shape() already does from
        # the Universe -- the same class of duplication that let a report
        # and its own workings disagree before (see lfg-publish's own note
        # on why decisions.json IS the report now, not a second rendering
        # of it). The SCORE total stays local: it is the Scorer's per-round
        # rating, deliberately NOT sim.py's season-forecast xi_total --
        # squad_log.csv's own grading pipeline (methodology.fit_rate_rel_floor,
        # golden_dataset) reads it as that specific quantity.
        best = (sum(p["score"] for p in chosen), sim.shape(u, xi), chosen)
    else:
        best = None

    cash = lg[lg.cfg.me].cash if lg and lg.cfg.me in lg.managers else None
    dl = deals(lg, lg.market) if lg and lg.market else []

    deadline, _dl_src = load_deadline(with_source=True)
    if players and best:
        log_squad(observed, players + market_players,
                  {id(p) for p in best[2]}, best[1],
                  best[0], deadline, obs_dt)

    warnings: list[str] = stale_feed_warnings()
    if age_h is not None and age_h > STALE_HOURS:
        warnings.append(f"**Data is {age_h:.0f}h old** — the ingest workflow "
                        "may have failed. Everything above is that snapshot.")
    if best:
        for k, n in SLOT_MIN.items():
            have = len(pool.get(k, []))
            if have <= n:
                warnings.append(f"**Only {have} {SLOT_LABEL[k]}"
                                f"{'s' if have != 1 else ''}** — one knock and "
                                "you can't field a legal XI.")
        guessed = [p["name"] for p in best[2] if p["assumed"]]
        if guessed:
            warnings.append(f"**{len(guessed)} unmodelled** "
                            f"({', '.join(guessed)}) — no LaLiga record, so "
                            "they carry an assumed baseline, not an earned "
                            "one.")
    if missing:
        warnings.append("**Not found in the market:** "
                        + ", ".join(f"`{m}`" for m in missing)
                        + ".")
    below_floor = low_priced_buys(dl)
    if below_floor:
        warnings.append(
            "**%d purchase%s priced below the floor** (%s) — check by hand: "
            "either a mis-join, or a discounted relist after an instant "
            "sale (unverified either way)." % (
                len(below_floor), "" if len(below_floor) == 1 else "s",
                ", ".join("%s %+.1f%%" % (d["player"], d["premium"])
                          for d in below_floor)))
    clashes = xw.clashes() if xw else {}
    if clashes:
        warnings.append(
            "**Crosswalk identifier clash:** %s — an id two players claim, "
            "refused rather than guessed at. Run `python src/crosswalk.py` "
            "to see which players." % (
                "; ".join("%s: %s" % (k, ", ".join(v))
                          for k, v in clashes.items())))
    if cash and cash.value is not None and cash.confidence != "known":
        warnings.append("Cash is an estimate — record an observed balance in "
                        "`inputs/cash.txt`.")
    elif cash and cash.value is not None:
        last_tx = max((t.get("date") or "" for t in lg.txns), default="")
        anchor = re.search(r"\d{4}-\d{2}-\d{2}", cash.basis or "")
        if anchor and last_tx and last_tx[:10] > anchor.group(0):
            warnings.append(f"Balance last checked {anchor.group(0)}, but the "
                            f"ledger moved on {last_tx[:10]}. Re-check it.")
    elif not cash or cash.value is None:
        warnings.append("No cash figure — add `inputs/cash.txt`.")

    try:
        from ffcore.auth import TokenStore
        token_days = TokenStore().expiry_days()
    except Exception:                                       # noqa: BLE001
        token_days = None
    WARNINGS.parent.mkdir(parents=True, exist_ok=True)
    WARNINGS.write_text(json.dumps(warnings, ensure_ascii=False),
                        encoding="utf-8")

    lines = alerts(warnings, token_days)
    if lines:
        write_lines(ALERTS, [f"# Alerts — {shown(now)}", ""]
                    + [f"- {ln}" for ln in lines])
    else:
        Path(ALERTS).unlink(missing_ok=True)
        print("no alerts")


def _selftest() -> None:
    w = stale_feed_warnings({"api_teams": 3.1, "api_market": 3.1})
    assert len(w) == 1 and "3 days" in w[0], w
    assert "api_teams" in w[0] and "api_market" in w[0], w
    assert stale_feed_warnings({}) == []

    assert title_name("nico van gaal") == "Nico van Gaal"

    body = "\n".join(alerts(["Only 1 delantero"], token_days=None))
    assert "delantero" in body, body
    assert alerts([], None) == []
    assert any("Log in again" in ln for ln in alerts([], token_days=9))
    assert alerts([], token_days=60) == []

    nailed = {"score": 5.0, "ppm": 4.0, "pct_rest": 100.0}
    assert _score_h3(nailed) == 5.0 + 2 * 4.0, _score_h3(nailed)
    bench = {"score": 0.5, "ppm": 4.0, "pct_rest": 10.0}
    assert abs(_score_h3(bench) - (0.5 + 2 * 0.4)) < 1e-9, _score_h3(bench)

    print("report self-test OK (%d cases)" % 11)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
