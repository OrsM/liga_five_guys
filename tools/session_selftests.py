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
