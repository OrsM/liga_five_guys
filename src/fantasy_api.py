"""
fantasy_api.py — read-only probe of the official LaLiga Fantasy API.

A SPIKE, not a pipeline. It answers one question: does authentication work
from Actions, and what does the JSON actually contain? Nothing else in the
repo imports it, and the futbolfantasy scrape stays the source of truth
until this has proved itself.

    python src/fantasy_api.py

Two ways to authenticate, in this order:

  LALIGA_REFRESH_TOKEN — for accounts that log in with Facebook, Google or
      any other social provider. Those have no password on LaLiga's B2C
      tenant, so the password grant cannot work for them. Capture the token
      once from a desktop browser (log in at miliga.laliga.com, DevTools ->
      Network, find the token response, copy its refresh_token). B2C expires
      these on a sliding window of roughly two weeks, so expect to redo it.

  LALIGA_EMAIL + LALIGA_PASSWORD — for accounts with a local password. This
      one needs no browser ever, so it is much the better option if you can
      set a password on the account.

SAFETY: every request is a GET. The API also exposes bid, offer, sell and
buyout-pay endpoints; none of them are referenced here, and none should be
until you have watched read-only runs for a while. A bug in a write call
costs real money in your league.

Writes reports/api_probe.md: per endpoint, the HTTP status, the shape of the
response and a few sample values. Long strings are truncated and tokens are
never written anywhere.

Auth is OAuth2 ROPC against LaLiga's Azure B2C tenant — a form POST, no
browser. Tokens last about 24h, so a real pipeline would just re-auth each
run rather than storing anything.

Endpoint layout is season 26/27: the API moved host and most routes gained a
competition segment (1 = LaLiga). Older public scrapers target the 25/26
host and are stale.

    pip install httpx
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

TENANT = "https://login.laliga.es/laligadspprob2c.onmicrosoft.com"
TOKEN_URL = f"{TENANT}/oauth2/v2.0/token"
POLICY = "B2C_1A_ResourceOwnerv2"
SIGNIN_POLICY = "B2C_1A_5ULAIP_PARAMETRIZED_SIGNIN"
EMAIL_CLIENT_ID = "af88bcff-1157-40a0-b579-030728aacf0b"
# The client the social/auth-code flow issues refresh tokens against. Two
# exist: 6457fa17 is the web client (redirect https://miliga.laliga.com, so a
# browser can actually land on it) and af88bcff is the native one (redirect
# authredirect://com.lfp.laligafantasy, which a browser cannot open). The
# authorize URL, this value and FF_REDIRECT_URI must all agree.
OAUTH_CLIENT_ID = os.environ.get(
    "FF_CLIENT_ID", "6457fa17-1224-416a-b21a-ee6ce76e9bc0")
OAUTH_REDIRECT_URI = os.environ.get(
    "FF_REDIRECT_URI", "https://miliga.laliga.com")
REDIRECT_URI = "authredirect://com.lfp.laligafantasy"

API = "https://fantasy-api.llt-services.com/api"
COMPETITION = os.environ.get("FF_COMPETITION_ID", "1")
CMP = f"/v1/competition/{COMPETITION}"

OUT = Path("reports") / "api_probe.md"
MAX_STR = 60


def authenticate(email: str, password: str) -> str:
    """ROPC password grant. Returns the bearer token."""
    data = {
        "grant_type": "password",
        "client_id": EMAIL_CLIENT_ID,
        "scope": f"openid {EMAIL_CLIENT_ID} offline_access",
        "redirect_uri": REDIRECT_URI,
        "username": email,
        "password": password,
        "response_type": "id_token",
    }
    r = httpx.post(f"{TOKEN_URL}?p={POLICY}", data=data, timeout=30,
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
    body = {}
    try:
        body = r.json()
    except ValueError:
        pass
    if r.status_code != 200:
        # error_description is B2C's own text; it says what actually failed.
        raise SystemExit("AUTH FAILED %s: %s" % (
            r.status_code,
            body.get("error_description") or body.get("error") or r.text[:200]))
    token = (body.get("access_token") or body.get("id_token") or "")
    if not token:
        raise SystemExit("AUTH returned no usable token; keys: %s"
                         % sorted(body))
    return token


def exchange_code(code: str, verifier: str) -> dict:
    """Trade a one-time authorization code for tokens (auth-code + PKCE).

    This is the path for social logins (Facebook, Google), which have no
    password on the B2C tenant. The code comes from opening the authorize URL
    in a browser and reading it off the redirect; it is single-use and expires
    within minutes.
    """
    data = {
        "grant_type": "authorization_code",
        "client_id": OAUTH_CLIENT_ID,
        "code": code,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "code_verifier": verifier,
        "scope": "openid offline_access",
    }
    print("exchanging against client %s, redirect %s"
          % (OAUTH_CLIENT_ID, OAUTH_REDIRECT_URI))
    r = httpx.post(f"{TOKEN_URL}?p={SIGNIN_POLICY}", data=data, timeout=30,
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
    body = {}
    try:
        body = r.json()
    except ValueError:
        pass
    if r.status_code != 200:
        raise SystemExit(
            "CODE EXCHANGE FAILED %s: %s\nCodes expire in minutes and work "
            "once. Re-open the authorize URL and try again — and check the "
            "verifier matches the challenge that URL was built with."
            % (r.status_code, body.get("error_description")
               or body.get("error") or r.text[:200]))
    return body


def refresh(token: str) -> tuple[str, str]:
    """Exchange a refresh token for an access token.

    Returns (access_token, new_refresh_token). B2C rotates refresh tokens, so
    the new one must be stored or the next run fails — see the workflow.
    """
    data = {
        "grant_type": "refresh_token",
        "refresh_token": token,
        "client_id": OAUTH_CLIENT_ID,
        "scope": "openid offline_access",
    }
    r = httpx.post(f"{TOKEN_URL}?p={SIGNIN_POLICY}", data=data, timeout=30,
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
    body = {}
    try:
        body = r.json()
    except ValueError:
        pass
    if r.status_code != 200:
        raise SystemExit(
            "REFRESH FAILED %s: %s\nIf this says the token is expired or "
            "invalid, capture a fresh one from the browser."
            % (r.status_code, body.get("error_description")
               or body.get("error") or r.text[:200]))
    access = body.get("access_token") or body.get("id_token") or ""
    if not access:
        raise SystemExit("REFRESH returned no usable token; keys: %s"
                         % sorted(body))
    return access, body.get("refresh_token", "")


def shape(value, depth=0, path="") -> list[str]:
    """Describe a JSON structure without dumping personal data wholesale."""
    pad = "  " * depth
    out: list[str] = []
    if isinstance(value, dict):
        for k, v in list(value.items())[:24]:
            if isinstance(v, (dict, list)):
                kind = "object" if isinstance(v, dict) else f"array[{len(v)}]"
                out.append(f"{pad}- `{k}`: {kind}")
                if depth < 2:
                    sample = v[0] if isinstance(v, list) and v else v
                    if isinstance(sample, (dict, list)):
                        out += shape(sample, depth + 1, f"{path}.{k}")
            else:
                out.append(f"{pad}- `{k}`: {brief(v)}")
        if len(value) > 24:
            out.append(f"{pad}- _…{len(value) - 24} more keys_")
    elif isinstance(value, list):
        out.append(f"{pad}array of {len(value)}")
        if value and depth < 2:
            out += shape(value[0], depth + 1, path)
    else:
        out.append(f"{pad}{brief(value)}")
    return out


def brief(v) -> str:
    if isinstance(v, str):
        v = v.replace("\n", " ")
        return f"`{v[:MAX_STR]}…`" if len(v) > MAX_STR else f"`{v}`"
    return f"`{v}`"


def main() -> None:
    email = os.environ.get("LALIGA_EMAIL", "")
    password = os.environ.get("LALIGA_PASSWORD", "")
    refresh_tok = os.environ.get("LALIGA_REFRESH_TOKEN", "").strip()
    auth_code = os.environ.get("LALIGA_AUTH_CODE", "").strip()
    verifier = os.environ.get("LALIGA_CODE_VERIFIER", "").strip()

    if auth_code and verifier:
        print("exchanging authorization code…")
        body = exchange_code(auth_code, verifier)
        token = body.get("access_token") or body.get("id_token") or ""
        new_refresh = body.get("refresh_token", "")
        print("exchange OK — keys returned: %s" % sorted(body))
        if not new_refresh:
            print("WARNING: no refresh_token in the response. This run will "
                  "work, but nothing can be stored for next time.")
        else:
            Path("/tmp/ff_refresh_token").write_text(new_refresh,
                                                     encoding="utf-8")
            gh_out = os.environ.get("GITHUB_OUTPUT")
            if gh_out:
                with open(gh_out, "a", encoding="utf-8") as fh:
                    fh.write("rotated=true\n")
            print("refresh token captured — the workflow will store it")
        if not token:
            raise SystemExit("no usable access token in the response")
    elif refresh_tok:
        print("authenticating with refresh token…")
        token, rotated = refresh(refresh_tok)
        print("auth OK — token acquired (not logged)")
        if rotated and rotated != refresh_tok:
            # Never printed. The workflow can pick this up to update the
            # secret; without that, the next run uses a dead token.
            gh_out = os.environ.get("GITHUB_OUTPUT")
            if gh_out:
                with open(gh_out, "a", encoding="utf-8") as fh:
                    fh.write("rotated=true\n")
            Path("/tmp/ff_refresh_token").write_text(rotated, encoding="utf-8")
            print("refresh token was rotated — new one written to "
                  "/tmp/ff_refresh_token for the workflow to store")
    elif email and password:
        print("authenticating with password…")
        token = authenticate(email, password)
        print("auth OK — token acquired (not logged)")
    else:
        raise SystemExit(
            "set LALIGA_REFRESH_TOKEN (social login) or "
            "LALIGA_EMAIL + LALIGA_PASSWORD (local password)")

    client = httpx.Client(
        base_url=API, timeout=45,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/json",
                 "User-Agent": "liga-five-guys-probe/0.1"})

    lines: list[str] = [
        "# API probe — %s" % datetime.now(timezone.utc)
        .strftime("%Y-%m-%d %H:%M UTC"), "",
        "Read-only. GET requests only; no bid, offer, sell or buyout call is "
        "made by this script.", "",
    ]
    found: dict[str, object] = {}

    def probe(label: str, path: str, keep: str | None = None):
        try:
            r = client.get(path)
        except Exception as exc:                       # noqa: BLE001
            lines.extend([f"## {label}", "", f"`GET {path}`", "",
                          f"**request failed:** {exc}", ""])
            print(f"{label}: ERROR {exc}")
            return None
        lines.extend([f"## {label}", "", f"`GET {path}` → **{r.status_code}**",
                      ""])
        print(f"{label}: {r.status_code}")
        if r.status_code != 200:
            lines.extend([f"```\n{r.text[:300]}\n```", ""])
            return None
        try:
            data = r.json()
        except ValueError:
            lines.extend(["_not JSON_", ""])
            return None
        lines.extend(shape(data) + [""])
        if keep:
            found[keep] = data
        return data

    me = probe("Who am I", "/v4/user/me?x-lang=es", keep="me")
    probe("Current week", f"{CMP}/week/current?x-lang=es")
    leagues = probe("My leagues", f"{CMP}/leagues?x-lang=es", keep="leagues")

    # Pull the first league id out of whatever shape the list arrives in.
    league_id = os.environ.get("FF_LEAGUE_ID", "")
    if not league_id and leagues is not None:
        items = leagues if isinstance(leagues, list) else \
            (leagues.get("data") or leagues.get("elements") or [])
        if isinstance(items, list) and items and isinstance(items[0], dict):
            league_id = str(items[0].get("id") or items[0].get("leagueId") or "")
    if not league_id:
        lines += ["_No league id found — set FF_LEAGUE_ID and re-run to probe "
                  "league-scoped endpoints._", ""]
        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("wrote", OUT)
        return

    lines += [f"_Using league `{league_id}`._", ""]
    probe("League standing", f"{CMP}/leagues/{league_id}/standing?x-lang=es",
          keep="standing")
    probe("League activity (page 0)",
          f"{CMP}/leagues/{league_id}/activity/0?x-lang=es")
    market = probe("Market — who is actually purchasable",
                   f"{CMP}/league/{league_id}/market?x-lang=es", keep="market")

    # Squad and cash for my own team, if the standing exposed a team id.
    team_id = os.environ.get("FF_TEAM_ID", "")
    if not team_id:
        st = found.get("standing")
        items = st if isinstance(st, list) else \
            ((st or {}).get("data") or (st or {}).get("elements") or [])
        if isinstance(items, list):
            for row in items:
                if not isinstance(row, dict):
                    continue
                team = row.get("team") if isinstance(row.get("team"), dict) \
                    else row
                tid = team.get("id") or team.get("teamId")
                if tid:
                    team_id = str(tid)
                    break
    if team_id:
        lines += [f"_Using team `{team_id}` (first in standing)._", ""]
        probe("Team squad", f"{CMP}/leagues/{league_id}/teams/{team_id}"
                            "?x-lang=es")
        probe("Team money", f"{CMP}/teams/{team_id}/money?x-lang=es")

    # Does a player record carry value history? That decides whether the
    # backfill comes from here or from futbolfantasy's charts.
    player_id = ""
    m = found.get("market")
    items = m if isinstance(m, list) else \
        ((m or {}).get("data") or (m or {}).get("elements") or [])
    if isinstance(items, list):
        for row in items:
            if isinstance(row, dict):
                pl = row.get("playerMaster") or row.get("player") or {}
                if isinstance(pl, dict) and pl.get("id"):
                    player_id = str(pl["id"])
                    break
    if player_id:
        probe(f"Player detail ({player_id}) — looking for value history",
              f"{CMP}/player/{player_id}/league/{league_id}?x-lang=es")
    else:
        lines += ["_No player id found in the market payload; set "
                  "FF_PLAYER_ID to probe a player directly._", ""]

    lines += ["---", "", "Next question for whatever we build on this: does "
                        "any payload above carry a dated price history? If "
                        "not, the value backfill has to come from "
                        "futbolfantasy's per-player charts.", ""]

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
