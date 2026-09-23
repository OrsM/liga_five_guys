# tools/session_selftests.py -- run every session()-dependent selftest in
# ONE process instead of tools/selftests.sh's usual one-`uv run`-per-module.
#
# xi.py, report.py, decide.py and sim.py each call ffcore.model.session(),
# which builds the whole real-data forecasting model once and memoizes it
# in a process-lifetime list (ffcore/model.py's _CACHE) -- cheap on a
# second call, ~100-165s on the first (measured 2026-09-23). Run as four
# separate `uv run` subprocesses, as tools/selftests.sh's normal dispatch
# does, each pays that first-call cost independently: the SAME real market
# and lineup history gets loaded and scored four times over for one gate
# run. Importing all four here instead means ffcore.model's module object,
# and its _CACHE, is the same object for every one of them -- only the
# first pays to build it.
from __future__ import annotations

import runpy
import sys

MODULES = ["xi.py", "report.py", "decide.py", "sim.py"]


def main() -> int:
    for name in MODULES:
        sys.argv = ["src/" + name, "--selftest"]
        try:
            runpy.run_path("src/" + name, run_name="__main__")
        except SystemExit as e:
            if e.code:
                print("  FAILED: %s (session-shared)" % name)
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
