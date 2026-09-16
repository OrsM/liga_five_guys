
from __future__ import annotations

from typing import NamedTuple

__all__ = ["Fitness", "resolve_fitness"]

_APP_TO_FF = {"doubtful": "doubt", "ok": ""}


class Fitness(NamedTuple):
    state: str
    agree: bool
    app_state: str | None


def resolve_fitness(status_by_key: dict[str, str],
                    player_status_by_key: dict[str, str]) -> dict[str, Fitness]:
    out = {}
    for key, ff in status_by_key.items():
        raw = player_status_by_key.get(key)
        aligned = _APP_TO_FF.get(raw, raw) if raw is not None else None
        agree = aligned is None or aligned == ff
        out[key] = Fitness(ff, agree, raw)
    return out


def _selftest() -> None:
    fit = resolve_fitness({"a": "", "b": "doubt", "c": "injured"},
                          {"a": "ok", "b": "doubtful", "c": "injured"})
    assert fit["a"] == ("", True, "ok"), fit["a"]
    assert fit["b"] == ("doubt", True, "doubtful"), fit["b"]
    assert fit["c"] == ("injured", True, "injured"), fit["c"]

    fit = resolve_fitness({"d": ""}, {"d": "injured"})
    assert fit["d"] == ("", False, "injured"), fit["d"]
    fit = resolve_fitness({"e": "doubt"}, {"e": "ok"})
    assert fit["e"] == ("doubt", False, "ok"), fit["e"]

    fit = resolve_fitness({"f": "doubt"}, {})
    assert fit["f"] == ("doubt", True, None), fit["f"]

    fit = resolve_fitness({"g": "unavailable"}, {"g": "ok"})
    assert fit["g"].agree is False, fit["g"]

    assert resolve_fitness({}, {"h": "injured"}) == {}

    print("ffcore.attributes self-test OK (%d cases)" % 8)


if __name__ == "__main__":
    _selftest()
