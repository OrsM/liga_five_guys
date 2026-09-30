
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    kind: str
    buy: str = ""
    sell: tuple[str, ...] = ()
    cost: float = 0.0
    proceeds: float = 0.0

    def __post_init__(self):
        if isinstance(self.sell, str):
            object.__setattr__(self, "sell",
                               (self.sell,) if self.sell else ())

    @property
    def net(self) -> float:
        return self.cost - self.proceeds

    @property
    def players(self) -> tuple[str, ...]:
        return ((self.buy, ) if self.buy else ()) + self.sell

    def label(self, names: dict[str, str] | None = None) -> str:
        names = names or {}
        sold = " + ".join(names.get(k, k) for k in self.sell)
        if self.kind == "sell":
            return "sell %s" % sold
        return ("%s %s" % ("take" if self.kind == "clause" else "buy",
                           names.get(self.buy, self.buy))
                + (" · sell %s" % sold if sold else ""))


def _selftest() -> None:
    assert Action("swap", buy="X", sell="Y").label() == "buy X · sell Y"
    assert Action("swap", buy="X", sell="Y").sell == ("Y",)
    assert Action("buy", buy="X").sell == ()
    assert Action("buy", buy="X", sell="Y").players == ("X", "Y")
    assert Action("sell", sell="Y").players == ("Y", )
    assert Action("sell", sell="y").label({"y": "Yuri"}) == "sell Yuri"
    assert Action("clause", buy="x", sell="y").label() == "take x · sell y"
    assert Action("swap", buy="x", sell="y").label({"x": "Xavi"}) \
        == "buy Xavi · sell y"
    print("ffcore.action self-test OK")


if __name__ == "__main__":
    _selftest()
