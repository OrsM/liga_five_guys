
from __future__ import annotations

import gc
import sys
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
        gc.collect()

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
