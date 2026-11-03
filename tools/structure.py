"""The layers tools/uml.py draws: the data layer reads tables and the clock;
the model computes from what it is given. (The lazy-import check is ruff's
PLC0415, in pyproject.toml.)"""
from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
DATA = {"ffcore.tidy", "ffcore.db", "ffcore.clock", "ffcore.jornadas", "ffcore.points"}
MODEL = {"ffcore.score", "ffcore.startprob", "ffcore.schedule",
         "ffcore.fixture", "ffcore.forecast", "ffcore.season",
         "ffcore.market", "ffcore.pricing", "ffcore.action",
         "ffcore.rules"}


def modules() -> dict[str, Path]:
    return {str(p.relative_to(SRC).with_suffix("")).replace("/", "."): p
            for p in sorted(SRC.glob("*.py")) + sorted(SRC.glob("ffcore/*.py"))
            if p.name != "__init__.py"}
