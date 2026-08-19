"""
run — every generator stage, in one interpreter.

WHY ONE PROCESS. The chain is ten stages and each of them used to be its own
`uv run python src/<stage>.py`: ten interpreter starts, ten imports of lxml
and numpy, and — the expensive part — ten independent walks over the same
tidy store. Nothing here needs isolation from anything else; the stages
already share a data directory, and running them in one process makes the
work each of them does once get done once for all of them.

WHAT IS SHARED, AND WHY THAT IS SAFE. Only pure memoisation: ffcore.text.norm
(a function of a string), sources._css (a function of a selector), and the
per-document parse caches, which are keyed on content. No stage caches a FILE
across the run, which is what makes this sound: ledger rewrites
inputs/transactions.csv and squads reads it back, points writes
data/season/live and methodology reads that, and every one of those reads hits
the disk exactly as it did when they were separate processes. If a read cache
is ever added to ffcore.tidy, it must key on the file's mtime or this becomes
a stale-data bug rather than a speed-up.

The stages are still runnable one at a time — `python src/sim.py` is
unchanged, and that is how a failure gets bisected. This only takes the place
of the shell loop that ran all of them.

    python src/run.py                 the full chain
    python src/run.py sim digest      just those, in the order given
"""

from __future__ import annotations

import sys
import time
import traceback


def _ledger() -> None:
    import ledger

    print(ledger.write(ledger.build()))


# Order is the dependency chain, the same one lfg-run documents: parse feeds
# the tidy store, crosswalk resolves names over it, ledger and points derive
# from it, squads replays ownership, and the generators read all of that.
STAGES: list[tuple[str, str]] = [
    ("parse", "ingest:parse"),
    ("crosswalk", "crosswalk:main"),
    ("ledger", ""),
    ("points", "points:main"),
    ("squads", "squads:main"),
    ("report", "report:main"),
    ("xi", "xi:main"),
    ("methodology", "methodology:main"),
    ("sim", "sim:main"),
    ("digest", "digest:main"),
]


def call(spec: str):
    if not spec:
        return _ledger
    mod, fn = spec.split(":")
    return getattr(__import__(mod), fn)


def main(argv: list[str]) -> int:
    want = [a for a in argv if not a.startswith("-")]
    stages = [s for s in STAGES if not want or s[0] in want]
    if want:
        stages = sorted(stages, key=lambda s: want.index(s[0]))

    started, times = time.time(), []
    for name, spec in stages:
        t0 = time.time()
        print("%s" % name, flush=True)
        try:
            call(spec)()
        except SystemExit as e:
            if e.code:
                print("  FAILED: %s exited %s" % (name, e.code))
                return 1
        except Exception:
            traceback.print_exc()
            print("  FAILED: %s" % name)
            return 1
        times.append((time.time() - t0, name))

    print("  %s" % "  ".join("%s %.1fs" % (n, t) for t, n in times))
    print("  %d stages in %.1fs" % (len(times), time.time() - started))
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        # Every stage named here must be importable and must have the entry
        # point named. A stage renamed in its own file and not here would
        # otherwise fail halfway through a run, after it had already written
        # half the report — the same class of miss as the deleted ppm_cell
        # that all twenty-three suites passed around.
        for _name, _spec in STAGES:
            assert callable(call(_spec)), _name
        assert [n for n, _ in STAGES] == sorted(
            {n for n, _ in STAGES}, key=[n for n, _ in STAGES].index), \
            "duplicate stage"
        print("run.py selftest OK (%d stages)" % len(STAGES))
        raise SystemExit(0)
    raise SystemExit(main(sys.argv[1:]))
