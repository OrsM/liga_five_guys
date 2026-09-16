
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    kind: str
    buy: str = ""
    sell: tuple[str, ...] = ()
    cost: float = 0.0
    proceeds: float = 0.0
    victim: str = ""

    def __post_init__(self):
        if isinstance(self.sell, str):
            object.__setattr__(self, "sell",
                               (self.sell,) if self.sell else ())

    @property
    def net(self) -> float:
        return self.cost - self.proceeds

    def label(self, names: dict[str, str] | None = None) -> str:
        def show(k):
            return (names or {}).get(k, k)
        sold = " + ".join(show(k) for k in self.sell)
        if self.kind == "sell":
            return "sell %s" % sold
        who = "clause %s from %s" % (show(self.buy), self.victim) \
            if self.victim else "buy %s" % show(self.buy)
        return who + (" · sell %s" % sold if sold else "")


def _selftest() -> None:
    assert Action("clause", buy="X", victim="R").label() == "clause X from R"
    assert Action("swap", buy="X", sell="Y").label() == "buy X · sell Y"
    assert Action("swap", buy="X", sell="Y").sell == ("Y",)
    assert Action("buy", buy="X").sell == ()
    assert Action("clause", buy="x", victim="R").label({"x": "Xavi"}) \
        == "clause Xavi from R"
    assert Action("sell", sell="y").label({"y": "Yuri"}) == "sell Yuri"
    assert Action("swap", buy="x", sell="y").label({"x": "Xavi"}) \
        == "buy Xavi · sell y"
    print("ffcore.action self-test OK (6 cases)")


if __name__ == "__main__":
    _selftest()
