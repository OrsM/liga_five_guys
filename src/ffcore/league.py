from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

from ffcore.parse import money
from ffcore.tidy import input_path

__all__ = ["Config", "load_config"]


@dataclass
class Config:
    me: str = "miguel_autentico"
    budget: float = 100e6


def load_config(name: str = "league.ini") -> Config:
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cp.optionxform = str  # type: ignore[assignment,method-assign]  # keep keys' case: configparser's own idiom
    path = input_path(name)
    if path.exists():
        cp.read(path, encoding="utf-8")
    base = Config()
    return Config(
        me=cp.get("league", "me", fallback=base.me),
        budget=money(cp.get("league", "budget", fallback=str(base.budget))) or 0.0)


def _selftest() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "league.ini"
        p.write_text("[league]\nme = someone\nbudget = 100.000.000\n"
                     , encoding="utf-8")
        cfg = load_config(str(p))
    assert (cfg.me, cfg.budget) == ("someone", 100e6), cfg
    print("ffcore.league self-test OK")


if __name__ == "__main__":
    _selftest()
