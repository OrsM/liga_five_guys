"""Parser self-report: a source whose newest snapshot is late or thin is
told to you, with the last good one to start from: once, again only when
the trouble changes, forgotten when it is gone. parser_health over snapshots_of
decides; main is the run's health stage.

Named rules, each tested:
  usual_needs_five: a source is judged only with at least 5 snapshots among the 20 before its newest.
  stale_before_thin: stale when its newest is later than max(6 h, 3 usual gaps), else thin when under half the usual
    rows, else ok.
  last_good_is_newest_usual: last_good is the newest snapshot (of all, up to the cut) of at least half the usual
    rows.
  rounded_half_up: usual_rows, hours_since and usual_gap_h are rounded to a tenth, a tie away from zero (as SQLite).
  drift_is_the_latest_calibration: the drifting bins told are those of the newest calibration, this run's when it
    scored the auctions, else the latest published run's that has any.

  sale_gate_is_the_latest_sale_score: the bench sales told are Miguel's n at the season horizon of the newest sale_score,
    this run's when it graded the sales, else the latest published run's that has any; told from SALE_GATE_N on.

    python src/health.py text    # what to tell you, empty if every source is fine (reads the parse cache)
"""
from __future__ import annotations

import sys
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import NamedTuple

from ffcore import identity, parsed, store
from ffcore import observations as ob
from ffcore.clock import cut
from ffcore.identity import Unmatched
from ffcore.records import AuctionCalibration, SaleScore
from ffcore.season_facts import stamp_jd
from ffcore.store import RunRecords
from publish import notify

__all__ = ["Snapshot", "Health", "drifting", "main", "parser_health", "said", "sale_gate_line", "sale_scores", "snapshots_of", "text", "trouble"]

SENT = Path.home() / ".local/state/lfg/health.sent"
UNMATCHED_FROM = 5e6
HEALTH_SOURCES = ("market", "lineups", "api_standings", "api_lineup", "api_teams", "api_leagues", "api_market", "api_offers")
HEALTH_USUAL = 21
SALE_GATE_N = 40


class Snapshot(NamedTuple):
    src: str
    at: str
    n: int


class Health(NamedTuple):
    src: str
    last_at: str
    rows: int
    usual_rows: float
    hours_since: float
    usual_gap_h: float
    status: str
    last_good: str | None


def tenth(x: float) -> float:
    """x rounded to a tenth, a tie away from zero (rule rounded_half_up)."""
    return float(Decimal(x).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def julian(stamp: str) -> float:
    """The Julian day of an observed_at stamp (every stored one is a stamp)."""
    day = stamp_jd(stamp)
    assert day is not None, stamp
    return day


def snapshots_of(obs: ob.Observations, cut: str) -> tuple[Snapshot, ...]:
    """The snapshots of each HEALTH_SOURCES table obs holds (the stamps at or before cut, with their row counts)."""
    out: list[Snapshot] = []
    for src in HEALTH_SOURCES:
        stamps = ob.stamps_of(obs, src)
        counts = Counter(stamps[:bisect_right(stamps, cut)])
        out.extend(Snapshot(src, at, counts[at]) for at in sorted(counts))
    return tuple(out)


def parser_health(snapshots: Iterable[Snapshot], cut: str) -> tuple[Health, ...]:
    """The status of each source that has a usual (rules usual_needs_five, stale_before_thin,
    last_good_is_newest_usual) as of cut."""
    by_src: dict[str, list[Snapshot]] = {}
    for s in sorted(snapshots, key=lambda s: s.at, reverse=True):
        by_src.setdefault(s.src, []).append(s)
    now = julian(cut)
    out: list[Health] = []
    for src in sorted(by_src):
        newest, *older = by_src[src][:HEALTH_USUAL]
        before = older
        if len(before) < 5:
            continue
        usual = sum(s.n for s in before) / len(before)
        days = [julian(s.at) for s in before]
        gap = (max(days) - min(days)) * 24 / (len(before) - 1)
        late = (now - julian(newest.at)) * 24
        status = "stale" if late > max(6.0, 3 * gap) else "thin" if newest.n < 0.5 * usual else "ok"
        good = max((s.at for s in by_src[src] if s.n >= 0.5 * usual), default=None)
        out.append(Health(src, newest.at, newest.n, tenth(usual), tenth(late), tenth(gap), status, good))
    return tuple(out)


def health_line(h: Health) -> str:
    what = ("no snapshot for %.0fh (one every %.0fh)" % (h.hours_since, h.usual_gap_h) if h.status == "stale"
            else "%d rows against the usual %.0f" % (h.rows, h.usual_rows))
    return "%s: %s; last good snapshot %s (data/raw/dt=%s.tar.xz)" % (h.src, what, h.last_good, h.last_good)


def unmatched_line(u: Unmatched) -> str:
    return "unmatched: %s (%s, %.1fM) has no %s" % (u.name, u.club, u.value / 1e6, "app id" if u.no_app else "probable-XI slug")


def trouble(health: Iterable[Health], unmatched: Iterable[Unmatched]) -> list[str]:
    """One line a source that is not ok, then one a player worth UNMATCHED_FROM or more, dearest first, that the app
    or the line-ups do not reach."""
    return [health_line(h) for h in sorted(health, key=lambda h: h.src) if h.status != "ok"] + [
        unmatched_line(u) for u in sorted(unmatched, key=lambda u: -u.value) if u.value >= UNMATCHED_FROM]


def drifting(pending: Iterable[AuctionCalibration] = ()) -> list[AuctionCalibration]:
    """The bins of the newest calibration that drift (rule drift_is_the_latest_calibration; pending is this run's), the
    furthest stated from observed first, then by scope and bin."""
    held = list(pending)
    if not held:
        newest = max((c.run_id for c in store.records("auction_calibration")), default=None)
        held = list(store.records("auction_calibration", newest)) if newest is not None else []
    return sorted((c for c in held if c.drift == 1), key=lambda c: (-abs(c.stated - c.observed), c.scope, c.bin))


def sale_scores(pending: Iterable[SaleScore] = ()) -> list[SaleScore]:
    """The sale_score rows of the newest run that has any (rule sale_gate_is_the_latest_sale_score; pending is this run's)."""
    held = list(pending)
    for run in reversed(store.runs()):
        if held:
            break
        held = list(store.records("sale_score", run.run_id))
    return held


def sale_gate_line(scores: Iterable[SaleScore]) -> str | None:
    """The line that the sell-rule gate is due when Miguel's bench sales at the season horizon number SALE_GATE_N or more, else None."""
    n = max((s.n for s in scores if (s.group, s.case, s.horizon) == ("miguel", "bench", "season")), default=0)
    if n < SALE_GATE_N:
        return None
    return ("sell-rule gate: Miguel has %d bench sales (gate re-test due at %d): run tools/lfg-rules and read the PAIRED block"
            % (n, SALE_GATE_N))


def text(health: Iterable[Health], unmatched: Iterable[Unmatched], drift: Iterable[AuctionCalibration],
         sales: Iterable[SaleScore] = ()) -> str:
    """trouble of health and unmatched, one line the drifting bins, and one line sale_gate_line of sales; empty if all is well."""
    lines = trouble(health, unmatched)
    gate = sale_gate_line(sales)
    if gate:
        lines.append(gate)
    bins = list(drift)
    if bins:
        worst = bins[0]
        scopes = ", ".join(sorted({c.scope for c in bins}))
        lines.append("auction forecast drifting in %d bins (%s); worst %s, chance %s: stated %.2f, observed %.2f over %d"
                     % (len(bins), scopes, worst.scope, worst.bin, worst.stated, worst.observed, worst.n))
    return ("parser trouble:\n" + "\n".join(lines)) if lines else ""


def said(records: RunRecords | None = None, obs: ob.Observations | None = None) -> str:
    """text of the parse cache as of t (obs: as parsed.read gives it; read here when None): the status of the sources, the
    players the app or the line-ups do not reach, and the drifting bins (those of records when it is this run's)."""
    if obs is None:
        obs = parsed.read(parsed.paths_in(), sorted({*identity.NEEDS, *HEALTH_SOURCES}))
    at = cut()
    people = identity.crosswalk(obs, at)
    return text(parser_health(snapshots_of(obs, at), at), identity.unmatched(people, obs, at),
                drifting(records.rows("auction_calibration") if records is not None else ()),
                sale_scores(records.rows("sale_score") if records is not None else ()))


def offers_excluded(records: RunRecords | None = None) -> int:
    """The number of dec_offer rows (the store's and this run's) grading leaves out as made up (grading.sound_offers)."""
    from grading import sound_offers
    return sound_offers([*store.records("dec_offer"), *(records.rows("dec_offer") if records is not None else ())])[1]


def main(records: RunRecords | None = None, sent: Path = SENT, obs: ob.Observations | None = None) -> dict[str, int]:
    """The health stage: tell said(records, obs) if it is new (sent holds what was told last), forget it when there is
    nothing to say."""
    told = said(records, obs)
    excluded = offers_excluded(records)
    print("health: %d dec_offer rows excluded from grading as made up (grading.FABRICATED_FROM)" % excluded)
    if not told:
        sent.unlink(missing_ok=True)
    elif not sent.is_file() or sent.read_text(encoding="utf-8") != told:
        if notify(told):
            sent.parent.mkdir(parents=True, exist_ok=True)
            sent.write_text(told, encoding="utf-8")
    return {"trouble": int(bool(told)), "offers_excluded": excluded}


def _selftest() -> None:
    import os
    import tempfile
    from datetime import datetime, timedelta, timezone

    from ffcore.records import Run

    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)

    def stamp(ago_h: float) -> str:
        return (now - timedelta(hours=ago_h)).strftime("%Y-%m-%dT%H%MZ")

    def feed(src: str, last: float, n: int, usual: int = 10, gap: float = 2.0, count: int = 21) -> list[Snapshot]:
        return [Snapshot(src, stamp(last + gap * i), n if i == 0 else usual) for i in range(count)]

    def unmatched(name: str, value: float, no_app: bool = True) -> Unmatched:
        return Unmatched("id", name, "x", value, no_app, not no_app)

    assert text((), (), ()) == "", "nothing fetched yet: nothing to say"
    snaps = (feed("market", 1, 5) + feed("lineups", 1, 3) + feed("api_standings", 30, 10)
             + feed("api_teams", 8, 10, gap=4.0) + feed("api_leagues", 1, 10, count=6)
             + feed("api_market", 1, 2) + feed("api_offers", 1, 10, count=5))
    got = {h.src: h for h in parser_health(snaps, stamp(0))}
    assert "api_offers" not in got and "api_leagues" in got, "usual_needs_five: 4 snapshots before the newest is too few"
    assert {s: h.status for s, h in got.items()} == {
        "market": "ok", "lineups": "thin", "api_standings": "stale", "api_teams": "ok", "api_leagues": "ok",
        "api_market": "thin"}, "stale_before_thin"
    assert got["lineups"].last_good == stamp(3), "last_good_is_newest_usual"
    tie = [Snapshot("x", stamp(1), 12)] + [Snapshot("x", stamp(3 + 2 * i), n) for i, n in enumerate([12] * 6 + [13, 13])]
    assert tenth(12.35) == 12.3 and parser_health(tie, stamp(0))[0].usual_rows == 12.3, "rounded_half_up"
    out = text(got.values(), (), ())
    assert out.startswith("parser trouble:") and "lineups: 3 rows against the usual 10" in out \
        and "api_standings: no snapshot for 30h" in out and "\nmarket:" not in out and "api_teams" not in out, out
    out = text((), [unmatched("Star Man", 6e6), unmatched("Cheap Man", 1e6), unmatched("Slugless", 7e6, False)], ())
    assert out.splitlines()[1:] == ["unmatched: Slugless (x, 7.0M) has no probable-XI slug",
                                    "unmatched: Star Man (x, 6.0M) has no app id"], \
        "a player of UNMATCHED_FROM or more the app or the line-ups do not reach is told, the dearest first; a cheap one is not"

    with tempfile.TemporaryDirectory() as tmp:
        kept = os.environ.get("FF_ROOT")
        os.environ["FF_ROOT"] = str(Path(tmp) / "data")
        try:
            store.publish(Run(1, "t", "a", "service", "c", "f", 1, 1.0), auction_calibration=[
                AuctionCalibration(1, "rookies", "high", 52, 0.93, 0.77, 1), AuctionCalibration(1, "veterans", "mid", 42, 0.97, 0.85, 1),
                AuctionCalibration(1, "keepers", "low", 30, 0.50, 0.49, 0)])
            out = text((), (), drifting())
            drifting_lines = [line for line in out.splitlines() if "drifting" in line]
            assert len(drifting_lines) == 1, out
            assert "rookies" in drifting_lines[0] and "veterans" in drifting_lines[0] and "0.93" in drifting_lines[0] and "0.77" in drifting_lines[0], out
            assert "keepers" not in out, out
            store.publish(Run(2, "t", "a", "service", "c", "f", 1, 1.0), auction_calibration=[AuctionCalibration(2, "keepers", "low", 30, 0.5, 0.2, 1)])
            assert [c.scope for c in drifting()] == ["keepers"], "drift_is_the_latest_calibration: the newest run that scored"
            this = RunRecords(3, "c")
            assert [c.scope for c in drifting(this.rows("auction_calibration"))] == ["keepers"], "a run that scored nothing leaves the latest published one"
            this.add("auction_calibration", [{"scope": "rookies", "bin": "high", "n": 9, "stated": 0.9, "observed": 0.1, "drift": 1}])
            assert [c.run_id for c in drifting(this.rows("auction_calibration"))] == [3], "this run's calibration, once it scored, replaces them"
            assert "rookies" in text((), (), drifting(this.rows("auction_calibration")))
            this = RunRecords(4, "c")

            def bench(n: int, run: int = 4) -> list[SaleScore]:
                return [SaleScore(run, g, c, h, n if (g, c) == ("miguel", "bench") else 99, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                        for g in ("miguel", "rivals") for c in ("all", "bench") for h in ("lock", "season")]

            assert sale_scores(this.rows("sale_score")) == [] and "sell-rule" not in text((), (), (), sale_scores()), "no sale_score yet: no line"
            assert text((), (), (), bench(SALE_GATE_N - 1)) == "", "below SALE_GATE_N: nothing to say"
            out = text((), (), (), bench(SALE_GATE_N))
            assert out.count("sell-rule gate: Miguel has 40 bench sales") == 1 and "tools/lfg-rules" in out, out
            assert "has 41 bench" in text((), (), (), bench(SALE_GATE_N + 1)), "every run after the first"
            store.publish(Run(4, "t", "a", "service", "c", "f", 1, 1.0), sale_score=bench(SALE_GATE_N))
            store.publish(Run(5, "t", "a", "service", "c", "f", 1, 1.0), auction_calibration=[AuctionCalibration(5, "keepers", "low", 30, 0.5, 0.2, 1)])
            assert "has 40 bench" in text((), (), (), sale_scores()), "the latest run that has sale_score: told from the store"
            assert "has 41 bench" in text((), (), (), sale_scores(bench(41, 6))), "this run's sale_score replaces it"
            assert text((), (), drifting(), bench(SALE_GATE_N)).count("\n") == 2, "the gate is one line beside the others"
        finally:
            if kept is None:
                os.environ.pop("FF_ROOT")
            else:
                os.environ["FF_ROOT"] = kept
    print("health self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    told = said() if sys.argv[1:] == ["text"] else ""
    sys.stdout.write(told + "\n" if told else "")
