"""football-data.co.uk results, one CSV per season."""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from typing import Any

from ffcore.futbolfantasy import club_slug
from ffcore.source import Source

__all__ = ["FD_BASE", "FD_URL", "FD_SOURCE", "FD_SEASONS_BACK", "fd_sources",
           "parse_fd_results"]


FD_BASE = "https://www.football-data.co.uk"
FD_URL = FD_BASE + "/mmz4281/{season}/SP1.csv"
FD_SOURCE = "football-data"


FD_SEASONS_BACK = 3

FD_FIELDS = ("FTHG", "FTAG", "HxG", "AxG", "HS", "AS", "HST", "AST",
            "HC", "AC")


def _season_start_year(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return now.year if now.month >= 7 else now.year - 1


def _season_suffix(key: str, prefix: str) -> str:
    m = re.match(r"^%s_(\d{4})$" % re.escape(prefix), key)
    return m.group(1) if m else ""


def fd_sources(now: datetime | None = None) -> list["Source"]:
    cur_y = _season_start_year(now)
    out = []
    for back in range(FD_SEASONS_BACK + 1):
        y = cur_y - back
        season = "%02d%02d" % (y % 100, (y + 1) % 100)
        out.append(Source(
            "fd_%s" % season, "results_history",
            FD_URL.format(season=season), parse_fd_results,
            cadence="every_run" if back == 0 else "once"))
    return out


def _fd_rows(text: str) -> list[dict[str, str]]:
    if not text:
        return []
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


def parse_fd_results(text: str, observed_at: str,
                     key: str = "fd_2526") -> list[dict[str, Any]]:
    season = _season_suffix(key, "fd")
    rows = []
    for r in _fd_rows(text):
        home, away = (r.get("HomeTeam") or "").strip(), \
                     (r.get("AwayTeam") or "").strip()
        if not home or not away:
            continue
        date = ""
        for fmt in ("%d/%m/%Y", "%d/%m/%y"):
            try:
                date = datetime.strptime(r.get("Date") or "", fmt
                                         ).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue
        row = {
            "observed_at": observed_at, "source": FD_SOURCE,
            "season": season, "date": date,
            "home_name": home, "away_name": away,
            "home": club_slug(home), "away": club_slug(away),
        }
        for col, out_key in zip(FD_FIELDS,
                                ("home_goals", "away_goals", "home_xg",
                                 "away_xg", "home_shots", "away_shots",
                                 "home_shots_on_target", "away_shots_on_target",
                                 "home_corners", "away_corners")):
            row[out_key] = (r.get(col) or "").strip()
        rows.append(row)
    return rows


_FD_CUR = ("﻿Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,"
          "HTAG,HTR,HxG,AxG,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR\n"
          "SP1,15/08/2026,18:30,Alaves,Getafe,3,0,H,0,0,D,1.93,0.24,"
          "18,6,8,2,16,13,5,3,4,3,0,1\n"
          "SP1,15/08/2026,20:30,Sevilla,Vallecano,2,1,H,0,1,A,2.19,"
          "1.89,13,6,4,3,18,18,1,2,4,4,1,0\n")
_FD_OLD = ("﻿Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
          "SP1,17/08/22,Ath Bilbao,Girona,0,0,D\n")


def _selftest() -> None:
    cur = parse_fd_results(_FD_CUR, "2026-08-20T1200Z", "fd_2627")
    assert len(cur) == 2, cur
    assert cur[0]["season"] == "2627" and cur[0]["date"] == "2026-08-15"
    assert cur[0]["home"] == "alaves" and cur[0]["away"] == "getafe"
    assert cur[0]["home_goals"] == "3" and cur[0]["home_xg"] == "1.93"
    assert cur[1]["home"] == "sevilla" and cur[1]["away"] == "rayo-vallecano"

    old = parse_fd_results(_FD_OLD, "2026-08-20T1200Z", "fd_2223")
    assert len(old) == 1, old
    assert old[0]["home_xg"] == "" and old[0]["away_xg"] == ""
    assert old[0]["home"] == "athletic"
    assert old[0]["away"] == "", old[0]
    assert old[0]["away_name"] == "Girona"

    assert parse_fd_results("", "t", "fd_2627") == []
    assert parse_fd_results("not a csv file at all", "t", "fd_2627") == []
    assert parse_fd_results("﻿Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\n"
                            "SP1,17/08/22,,,,\n", "t", "fd_2223") == []



    fs = fd_sources(datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert [s.key for s in fs] == ["fd_2627", "fd_2526", "fd_2425", "fd_2324"]
    assert fs[0].cadence == "every_run"
    assert all(s.cadence == "once" for s in fs[1:])
    assert all(s.table == "results_history" for s in fs)

    print("ffcore.footballdata self-test OK")


if __name__ == "__main__":
    _selftest()
