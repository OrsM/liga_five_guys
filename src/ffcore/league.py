from __future__ import annotations

import configparser
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from ffcore import db as lfg
from ffcore.parse import money
from ffcore.tidy import input_path

__all__ = ["Config", "load_config", "write_config"]


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


def write_config(cfg: Config | None = None, path: Path | None = None) -> str:
    """The one input of the league's views no table holds (schema.sql:
    owners, users, cash, price_paid over the app's tables as of t): you and
    the budget, into league_config. You."""
    cfg = cfg or load_config()
    with closing(lfg.connect(path)) as db, db:
        db.execute("DELETE FROM league_config")
        db.execute("INSERT INTO league_config VALUES (?, ?)", (cfg.me, cfg.budget))
    return cfg.me


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
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    write_config()
    for m, n, cash in lfg.rows("SELECT c.manager, count(o.player), c.cash FROM cash c"
                               " LEFT JOIN owners o USING (manager) GROUP BY c.manager"):
        print("%-18s %2d players  cash %7.2fM" % (m, n, cash / 1e6))
