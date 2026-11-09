from __future__ import annotations

import sqlite3
import sys
import zlib
from contextlib import closing

from ffcore import db as lfg
from ffcore.db import rows

# Who each player is, as SQL: the registry view (schema.sql) written into
# crosswalk with no slugs; each ff_identity row (identity.sql) then names
# its player by name, or name and club, while a slug names nobody; each
# player's slug is the first one a line-up or starters row of his gave, in
# the order stored; then ff_identity again, now that slugs name players.
REGISTRY = """
INSERT INTO crosswalk (player_id, name, club_id, app_id)
SELECT player_id, name, club_id, app_id FROM registry
"""
SLUGS = """
UPDATE crosswalk SET ff_slug = f.player_slug
FROM (SELECT player, player_slug FROM (
          SELECT player, player_slug, row_number() OVER (PARTITION BY player ORDER BY src, seq) AS n
          FROM (SELECT 0 AS src, seq, player_slug, player FROM history_lineups
                UNION ALL SELECT 1, seq, player_slug, player FROM current_starters)
          WHERE player IS NOT NULL AND player_slug <> '') WHERE n = 1) f
WHERE crosswalk.player_id = f.player
"""


def identity(db: sqlite3.Connection) -> None:
    """ff_identity_kept (identity.sql) up to the registry and the rows it is
    read for: refilled from ff_identity_now when either changed (build calls
    it too; so does connect, through facts.ensure)."""
    if db.execute("SELECT count(*) FROM sqlite_master WHERE name IN ('ff_identity_kept', 'facts_meta')"
                  ).fetchone()[0] < 2:
        return
    reg = db.execute("SELECT player_id, name, club_id, ff_slug, app_id FROM crosswalk ORDER BY 1").fetchall()
    through = "%08x:%d:%d" % (
        zlib.crc32(repr(reg).encode()),
        db.execute("SELECT coalesce(max(rowid), 0) FROM src_lineups").fetchone()[0],
        db.execute("SELECT coalesce(max(rowid), 0) FROM src_starters").fetchone()[0])
    version = "%08x" % zlib.crc32(db.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'ff_identity_now'").fetchone()[0].encode())
    if db.execute("SELECT version, through FROM facts_meta WHERE name = 'ff_identity'").fetchone() == (version, through):
        return
    own = not db.in_transaction                 # else the caller's transaction holds it
    if own:
        db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("DELETE FROM ff_identity_kept")
        db.execute("INSERT INTO ff_identity_kept SELECT * FROM ff_identity_now")
        db.execute("INSERT OR REPLACE INTO facts_meta VALUES ('ff_identity', ?, ?)", (version, through))
        if own:
            db.execute("COMMIT")
    except BaseException:
        if own:
            db.execute("ROLLBACK")
        raise


def build(db: sqlite3.Connection) -> None:
    db.execute("DELETE FROM crosswalk")
    db.execute(REGISTRY)
    identity(db)
    db.execute(SLUGS)
    identity(db)


def main() -> None:
    with closing(lfg.connect()) as db, db:
        build(db)
        n, ff, app = db.execute("SELECT count(*), sum(ff_slug <> ''), sum(app_id <> '') FROM crosswalk").fetchone()
    print("registry: %d players — %.0f%% carry a probable-XI slug, %.0f%% an app id"
          % (n, 100 * ff / (n or 1), 100 * app / (n or 1)))
    clashes: dict[str, list[str]] = {}
    for idx, value in rows("SELECT id, value FROM id_clashes ORDER BY id, value"):
        clashes.setdefault(idx, []).append(value)
    for idx, ids in clashes.items():
        print("  warn: %s claimed by two players, refused until resolved: %s"
              % (idx, ", ".join(ids)))


def _selftest() -> None:
    import tempfile
    from pathlib import Path
    from ffcore.tidy import current, store_sql, tables_in

    a, b = "2026-08-01T0900Z", "2026-08-02T0900Z"
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        store_sql("market", [{"observed_at": a, "ff_id": i, "name": n, "club": c, "value": "1"}
                             for i, n, c in [("af", "Álvaro Fernández", "espanyol"),
                                             ("jc", "Jonny Castro", "alaves")]])
        store_sql("lineups", [{"observed_at": a, "source": "futbolfantasy", "team_slug": "espanyol",
                               "player_name": "Álvaro Fernández", "player_slug": slug}
                              for slug in ("alvaro-fdez", "second")])
        store_sql("starters", [{"observed_at": a, "team_slug": "alaves", "player_name": "Jonny Castro",
                                "player_slug": "jonny-castro-ff", "role": "starter", "match_id": "m"}])
        with closing(lfg.connect()) as db, db:
            build(db)
            got = dict(db.execute("SELECT player_id, ff_slug FROM crosswalk"))
        assert got == {"af": "alvaro-fdez", "jc": "jonny-castro-ff"}, \
            ("a slug, the first his rows gave, to the player the row names by name (within the club)", got)

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        store_sql("market", [{"observed_at": at, "ff_id": i, "name": n, "club": c, "value": v}
                             for at, i, n, c, v in [
            (a, "jc", "Jonny Castro", "celta", "19798425"), (b, "jc", "Jonny Castro", "alaves", "19798425"),
            (a, "lz", "Sergio Jose Lozano", "eibar", "560396"), (a, "ja", "Jose Angel Lopez", "espanyol", "560396"),
            (a, "pf", "Pablo Fornals", "betis", "5000000"), (a, "gone", "Left Early", "x", "1000000")]])
        store_sql("api_teams", [{"observed_at": at, "player_id": i, "player_name": n, "market_value": v,
                                 "manager": "m"} for at, i, n, v in [
            (a, "2552", "Jonny Otto", "19798425"), (b, "2552", "Jonny Otto", "19798425"),
            (a, "3178", "Lozano", "560396"), (a, "3172", "Jose Angel", "560396"),
            (a, "99", "Fornals", "40000000")]])
        c = "2026-08-03T0900Z"
        store_sql("market", [{"observed_at": at, "ff_id": "bo", "name": "Cesar Bonafe", "club": "elche",
                              "value": "1057681"} for at in (a, b, c)]
                  + [{"observed_at": at, "ff_id": "bn", "name": "Bono Bonafe", "club": "x", "value": "1057681"}
                     for at in (a,)])
        store_sql("api_players_all", [{"observed_at": a, "player_id": "3279", "player_name": "Bonafé",
                                       "market_value": "1057681"}])
        got = {r[0]: r[1:] for r in rows("SELECT player_id, app_id, name, club_id FROM registry")}
    assert {k: v[0] for k, v in got.items()} == {
        "jc": "2552", "lz": "3178", "ja": "3172", "pf": "", "gone": "", "bo": "3279", "bn": ""}, \
        ("same value, names sharing a stem; at one value, the most stems shared wins; an all-players"
         " row holds until his next, so it meets every market snapshot in between", got)
    assert got["jc"][1:] == ("Jonny Castro", "alaves"), "his latest market row; one who left keeps his"

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        with closing(lfg.connect()) as db, db:
            db.executemany("INSERT INTO crosswalk (player_id, name, club_id, ff_slug, app_id) VALUES (?, ?, ?, ?, ?)", [
                ("af", "Alvaro Fernandez", "espanyol", "alvaro-fernandez", "2101"),
                ("jc", "Jonny Castro", "alaves", "jonny-castro", ""),
                ("cr", "Carlos Romero", "", "", "2614"), ("ir", "Isaac Romero", "", "", "2614"),
                ("11", "Álvaro García", "betis", "", ""), ("12", "Alvaro Garcia", "rayo", "", "")])
        store_sql("lineups", [dict(observed_at=a, player_slug=slug, player_name=name,
                                   team_slug=club) for slug, name, club in [
            ("alvaro-fernandez", "x", "espanyol"), ("", "Álvaro Fernández", ""),
            ("who-is-this", "", ""), ("", "Álvaro García", ""), ("", "Alvaro Garcia", "rayo")]])
        assert [r["player"] for r in current("lineups")] == ["af", "af", None, None, "12"], \
            "slug, else a name one player has, else a name one player of the club has"
        store_sql("api_teams", [dict(observed_at=a, player_id=i, manager="m")
                                for i in ("2101", "2614", "9999")])
        assert [r["player"] for r in current("api_teams")] == ["af", None, None], \
            "an app id two players claim names nobody"
        assert rows("SELECT id, value FROM id_clashes") == [("app_id", "2614")]

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        def who() -> list[tuple[object, ...]]:
            with closing(lfg.connect()) as db:
                return db.execute("SELECT player_slug, player FROM ff_identity ORDER BY 1").fetchall()
        assert who() == []
        store_sql("lineups", [{"observed_at": "2026-08-10T0800Z", "source": "futbolfantasy",
                               "player_slug": "x", "player_name": "X", "team_slug": "t"}])
        with closing(lfg.connect()) as db, db:
            db.execute("INSERT INTO crosswalk (player_id, name, club_id, ff_slug, app_id) VALUES ('p1', 'X', 't', 'x', '')")
        assert who() == [("x", "p1")], "a new line-up row and a new registry are named at the next connect"
        with closing(lfg.connect()) as db, db:
            db.execute("UPDATE crosswalk SET ff_slug = 'y', name = 'Z'")
            identity(db)
        assert who() == [("x", None)], "a changed registry refills it in the caller's transaction"

    print("crosswalk self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
