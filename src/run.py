
from __future__ import annotations

import gc
import collections
import sys
import threading
import time
import traceback


def _ledger() -> None:
    import ledger

    print(ledger.write(ledger.build()))


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


def _sample(hits, stop, tid) -> None:
    # Innermost src/ frame of the main thread every 0.25s, not cProfile: a
    # stage took 38s scheduled and 2.2s by hand (2026-09-24) and cProfile's
    # ~2x would distort the run being explained.
    while not stop.wait(0.25):
        f, at = sys._current_frames().get(tid), "(outside src/)"
        while f is not None:
            if "/src/" in f.f_code.co_filename:
                at = "%s:%s" % (f.f_code.co_filename.rsplit("/src/", 1)[1],
                                f.f_code.co_name)
                break
            f = f.f_back
        hits[at] += 1


def main(argv: list[str]) -> int:
    want = [a for a in argv if not a.startswith("-")]
    stages = [s for s in STAGES if not want or s[0] in want]
    if want:
        stages = sorted(stages, key=lambda s: want.index(s[0]))

    started, times = time.time(), []
    for name, spec in stages:
        t0 = time.time()
        print("%s" % name, flush=True)
        hits, stop = collections.Counter(), threading.Event()
        threading.Thread(target=_sample, daemon=True, args=(
            hits, stop, threading.get_ident())).start()
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
        # gc.collect() BEFORE the stage's time is recorded, not after: it
        # used to run outside the timer, so a stage's own printed cost was
        # a lie and the difference showed up as an unexplained gap between
        # the per-stage sum and the total -- 365.7s vs 698.4s on
        # 2026-09-23, all of it later traced to GC passes over a
        # memory-pressured box (swap in use), invisible because nothing
        # timed them. Kept, not removed: this repo runs under a 750M
        # MemoryMax and the collect is what keeps one stage's numpy/CSV
        # working set from still being live when the next stage allocates.
        gc.collect()
        stop.set()
        times.append((time.time() - t0, name))
        if times[-1][0] > 8:            # a healthy run's log is unchanged
            print("  slow %s, where: %s" % (name, "  ".join(
                "%d%% %s" % (100 * v // sum(hits.values()), k)
                for k, v in hits.most_common(5))))

    print("  %s" % "  ".join("%s %.1fs" % (n, t) for t, n in times))
    print("  %d stages in %.1fs" % (len(times), time.time() - started))
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        for _name, _spec in STAGES:
            assert callable(call(_spec)), _name
        assert [n for n, _ in STAGES] == sorted(
            {n for n, _ in STAGES}, key=[n for n, _ in STAGES].index), \
            "duplicate stage"
        print("run.py selftest OK (%d stages)" % len(STAGES))
        raise SystemExit(0)
    raise SystemExit(main(sys.argv[1:]))
