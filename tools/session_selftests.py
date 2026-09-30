from __future__ import annotations

import runpy
import sys

MODULES = ["src/decide.py", "src/sim.py", "tools/ask.py"]


def main() -> int:
    for name in MODULES:
        sys.argv = [name, "--selftest"]
        try:
            runpy.run_path(name, run_name="__main__")
        except SystemExit as e:
            if e.code:
                print("  FAILED: %s (session-shared)" % name)
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
