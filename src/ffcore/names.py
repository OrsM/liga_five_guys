"""Who a player is, and what he is called. A Name is built once from the
text a source wrote; nothing else normalizes or title-cases a player's
name. PlayerKey (our id, the crosswalk's) and AppId (the LaLiga app's)
are distinct types, so mypy refuses one where the other belongs."""
from __future__ import annotations

from typing import NewType

from ffcore.parse import text
from ffcore.text import norm

__all__ = ["AppId", "Name", "PlayerKey", "app_id", "row_key"]

PlayerKey = NewType("PlayerKey", str)
AppId = NewType("AppId", str)

_PARTICLES = {"de", "del", "van", "von", "der", "den", "di", "da", "dos",
              "do", "y", "bin", "ibn", "ter"}


class Name:
    """A name as a source wrote it (raw). key is what two names are
    compared by: accents, case and punctuation never count. shown is how
    the board writes it: an all-lowercase name title-cased, any other kept."""
    __slots__ = ("raw", "key")

    def __init__(self, raw: str | None):
        self.raw = (raw or "").strip()
        self.key = norm(self.raw)

    @property
    def shown(self) -> str:
        if self.raw != self.raw.lower():
            return self.raw
        return " ".join(w if i and w in _PARTICLES else
                        "-".join(p[:1].upper() + p[1:] for p in w.split("-"))
                        for i, w in enumerate(self.raw.split()))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Name) and other.key == self.key

    def __hash__(self) -> int:
        return hash(self.key)

    def __bool__(self) -> bool:
        return bool(self.key)

    def __str__(self) -> str:
        return self.shown

    def __repr__(self) -> str:
        return "Name(%r)" % self.raw


def row_key(row, cols: tuple[str, ...] = ("name",)) -> PlayerKey:
    """The player a source's row is about: its own ff_id, else the key of
    the first of its name columns present; "" if neither."""
    fid = (row.get("ff_id") or "").strip()
    return PlayerKey(fid or next((n.key for c in cols if (n := Name(row.get(c)))), ""))


def app_id(row) -> AppId:
    """The LaLiga app's id for the player an app row (api_teams, api_market,
    api_activity, api_lineup...) is about: its player_id column."""
    return AppId(text(row, "player_id"))


def _selftest() -> None:
    assert Name("Iñigo Vicente") == Name("inigo vicente") == Name(" INIGO  VICENTE ")
    assert hash(Name("Iñigo Vicente")) == hash(Name("inigo vicente"))
    assert Name("Iñigo Vicente") != Name("Iñigo Martínez")
    assert Name("Iñigo Vicente").key == "inigo vicente"
    assert not Name("") and not Name(None) and Name("x")
    assert str(Name("pepelu")) == "Pepelu"
    for raw, shown in [("marcos alonso", "Marcos Alonso"), ("nico van gaal", "Nico van Gaal"),
                       ("de la fuente", "De La Fuente"), ("ruiz-de galarreta", "Ruiz-De Galarreta"),
                       ("Vini Jr.", "Vini Jr."), ("SusoGattuso", "SusoGattuso"), ("", ""),
                       ("omar el hilali", "Omar El Hilali"), ("Iñigo Vicente", "Iñigo Vicente")]:
        assert Name(raw).shown == shown, (raw, Name(raw).shown)
        assert Name(Name(raw).shown).shown == Name(raw).shown, "showing twice changes nothing"
    assert row_key({"ff_id": " 12 ", "name": "X"}) == "12", "a source's own id first"
    assert row_key({"name": "Iñigo Vicente"}) == "inigo vicente"
    assert row_key({"player_name_full": "", "player_name": "Pepelu"},
                   ("player_name_full", "player_name")) == "pepelu", "the first name present"
    assert row_key({}) == ""
    assert app_id({"player_id": " 2929 "}) == "2929" and app_id({}) == "", \
        "an app row's player_id is the app's id, never our key"
    print("ffcore.names self-test OK")


if __name__ == "__main__":
    _selftest()
