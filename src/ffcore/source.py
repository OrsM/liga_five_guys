"""A page to fetch: where it lives, which table it feeds and what parses it."""
from __future__ import annotations

from typing import Callable, NamedTuple

__all__ = ["Source"]


def _once(seen: set, key) -> bool:
    if not key or key in seen:
        return False
    seen.add(key)
    return True


def _rebuild(key: str, pattern, table: str, parse, url_for, **kw):
    m = pattern.match(key or "")
    if not m:
        return None
    return Source(key, table, url_for(m), parse, **kw)


class Source(NamedTuple):
    """follow(text, context) returns the pages a fetched page points to,
    queued in the same run: the context carries who you are ("me")."""
    key: str
    table: str
    url: str
    parse: Callable
    cadence: str = "every_run"
    enabled: bool = True
    auth: bool = False
    follow: Callable[[str, dict], list] | None = None


def _selftest() -> None:
    import re

    seen: set = set()
    assert _once(seen, "a") and not _once(seen, "a") and not _once(seen, "")
    got = _rebuild("page_7", re.compile(r"^page_(\d+)$"), "t", str,
                   lambda m: "u/" + m.group(1), cadence="once")
    assert got == Source("page_7", "t", "u/7", str, cadence="once"), got
    assert _rebuild("other", re.compile(r"^page_(\d+)$"), "t", str, str) is None
    assert got.follow is None, "a page follows nothing unless it says so"
    print("ffcore.source self-test OK")


if __name__ == "__main__":
    _selftest()
