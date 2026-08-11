"""
llf_client.py — LaLiga Fantasy Oficial API client + point-in-time snapshot ingest.

Design rules:
  * Raw responses are written to an immutable, timestamped directory. Never rewritten.
  * Nothing is parsed on the way in. Parsing is a downstream, re-runnable step.
  * Token is cached and refreshed; credentials live outside the repo.

Setup (Asus box, no sudo):
    uv venv ~/.local/venvs/llf && source ~/.local/venvs/llf/bin/activate
    uv pip install httpx duckdb

    mkdir -p ~/.config/llf && chmod 700 ~/.config/llf
    cat > ~/.config/llf/env <<'EOF'
    LLF_EMAIL=you@example.com
    LLF_PASSWORD=...
    EOF
    chmod 600 ~/.config/llf/env

Usage:
    python llf_client.py bootstrap tok.json   # once, if you log in via Facebook/Google
    python llf_client.py snapshot             # daily pull, 00:20 Europe/Madrid
    python llf_client.py load                 # register snapshots as DuckDB views
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

ROOT = Path(os.environ.get("LLF_ROOT", Path.home() / "fantasy"))
RAW = ROOT / "raw"
TOKEN_CACHE = Path(
    os.environ.get("LLF_TOKEN_CACHE", Path.home() / ".config" / "llf" / "token.json")
)

API = "https://fantasy-api.llt-services.com/api"
COMPETITION_ID = "1"  # 1 = LaLiga EA Sports
CMP = f"/v1/competition/{COMPETITION_ID}"

AUTH_HOST = "https://login.laliga.es/laligadspprob2c.onmicrosoft.com/oauth2/v2.0/token"
POLICY_PASSWORD = "B2C_1A_ResourceOwnerv2"
POLICY_REFRESH = "B2C_1A_5ULAIP_PARAMETRIZED_SIGNIN"
CLIENT_ID = "af88bcff-1157-40a0-b579-030728aacf0b"  # email/native client
WEB_CLIENT_ID = "6457fa17-1224-416a-b21a-ee6ce76e9bc0"  # miliga.laliga.com web client
REDIRECT_URI = "authredirect://com.lfp.laligafantasy"

HEADERS = {
    "Accept": "application/json",
    "X-Lang": "es",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Firefox/128.0",
}

# Be a good citizen: one sweep a day, sequential, with a gap between calls.
REQUEST_DELAY = 0.4
TIMEOUT = 20.0


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def _password_grant(email: str, password: str) -> dict:
    r = httpx.post(
        f"{AUTH_HOST}?p={POLICY_PASSWORD}",
        data={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "scope": f"openid {CLIENT_ID} offline_access",
            "redirect_uri": REDIRECT_URI,
            "username": email,
            "password": password,
            "response_type": "id_token",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def _refresh_grant(refresh_token: str, client_id: str) -> dict:
    """
    Refresh MUST use the same B2C client that issued the token. Tokens captured
    from the miliga.laliga.com browser session belong to WEB_CLIENT_ID; tokens
    from the password grant belong to CLIENT_ID.
    """
    r = httpx.post(
        f"{AUTH_HOST}?p={POLICY_REFRESH}",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "scope": "openid offline_access",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def bootstrap(path: str | None = None) -> None:
    """
    One-time setup for accounts that sign in with Facebook, Google or Apple.

    The password grant does not work for federated accounts. Instead, capture
    the token response once from a browser session (see the README section in
    laliga-fantasy-system.md), save the JSON to a file, and run:

        python llf_client.py bootstrap /tmp/token.json

    From then on the daily job refreshes headlessly. B2C rotates the refresh
    token on every use and the sliding window is measured in weeks, so a job
    that runs daily keeps the session alive indefinitely.
    """
    raw = Path(path).read_text() if path else sys.stdin.read()
    payload = json.loads(raw)
    payload.setdefault("client_id", WEB_CLIENT_ID)
    if not payload.get("refresh_token"):
        sys.exit("No refresh_token in that JSON — capture the full token "
                 "response, not just the bearer value.")
    _store(payload)
    print(f"stored; refreshing via client_id={payload['client_id']}")


def get_token() -> str:
    """Return a valid id_token, refreshing or re-authenticating as needed."""
    now = time.time()
    cached = json.loads(TOKEN_CACHE.read_text()) if TOKEN_CACHE.exists() else {}

    if cached.get("expires_at", 0) > now + 300:
        return cached["id_token"]

    if cached.get("refresh_token"):
        try:
            fresh = _refresh_grant(
                cached["refresh_token"], cached.get("client_id", WEB_CLIENT_ID)
            )
            fresh.setdefault("client_id", cached.get("client_id", WEB_CLIENT_ID))
            return _store(fresh)
        except httpx.HTTPStatusError:
            # Refresh window expired. Federated accounts must re-bootstrap from
            # the browser; password accounts fall through below.
            if cached.get("client_id") == WEB_CLIENT_ID:
                sys.exit("Refresh token expired — re-run the browser capture "
                         "and `python llf_client.py bootstrap <file>`.")

    env = {**_read_env_file(Path.home() / ".config" / "llf" / "env"), **os.environ}
    email, password = env.get("LLF_EMAIL"), env.get("LLF_PASSWORD")
    if not email or not password:
        sys.exit("No usable session. Facebook/Google/Apple accounts: run "
                 "`bootstrap`. Email accounts: set LLF_EMAIL / LLF_PASSWORD.")
    out = _password_grant(email, password)
    out["client_id"] = CLIENT_ID
    return _store(out)


def _store(payload: dict) -> str:
    token = payload.get("id_token") or payload.get("access_token")
    if not token:
        raise RuntimeError(f"No token in auth response: {list(payload)}")

    prev = json.loads(TOKEN_CACHE.read_text()) if TOKEN_CACHE.exists() else {}
    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(json.dumps({
        "id_token": token,
        # B2C rotates refresh tokens; if a response omits one, keep the last
        # good value rather than nulling the session out.
        "refresh_token": payload.get("refresh_token") or prev.get("refresh_token"),
        "client_id": payload.get("client_id") or prev.get("client_id", WEB_CLIENT_ID),
        # Typically ~3600s; be conservative if absent.
        "expires_at": time.time() + int(payload.get("expires_in", 3600)),
    }))
    TOKEN_CACHE.chmod(0o600)
    return token


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

class Fantasy:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=API, headers=HEADERS, timeout=TIMEOUT)
        self.client.headers["Authorization"] = f"Bearer {get_token()}"

    def get(self, path: str, **params):
        params.setdefault("x-lang", "es")
        for attempt in range(3):
            r = self.client.get(path, params=params)
            if r.status_code in (429, 403):
                # Back off immediately rather than hammering.
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 401 and attempt == 0:
                self.client.headers["Authorization"] = f"Bearer {get_token()}"
                continue
            r.raise_for_status()
            time.sleep(REQUEST_DELAY)
            return r.json()
        r.raise_for_status()

    # --- read endpoints ---------------------------------------------------
    def me(self):                       return self.get("/v4/user/me")
    def leagues(self):                  return self.get(f"{CMP}/leagues")
    def standing(self, lg):             return self.get(f"{CMP}/leagues/{lg}/standing")
    def activity(self, lg, i=0):        return self.get(f"{CMP}/leagues/{lg}/activity/{i}")
    def team(self, lg, tm):             return self.get(f"{CMP}/leagues/{lg}/teams/{tm}")
    def money(self, tm):                return self.get(f"{CMP}/teams/{tm}/money")
    def lineup(self, tm):               return self.get(f"{CMP}/teams/{tm}/lineup")
    def players(self):                  return self.get(f"{CMP}/players")
    def player(self, pid, lg):          return self.get(f"{CMP}/player/{pid}/league/{lg}")
    def market(self, lg):               return self.get(f"{CMP}/league/{lg}/market")
    def calendar(self, week):           return self.get(f"{CMP}/calendar", weekNumber=week)
    def current_week(self):             return self.get(f"{CMP}/week/current")

    # --- write endpoints: deliberately not wired up ----------------------
    # Bids, sales and clause changes exist on this API (POST .../market/{id}/bid,
    # POST .../market/sell, PUT .../buyout/player). Keep the system read-only
    # until you've watched its proposals for several weeks.


# --------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------

def _write(dest: Path, name: str, obj) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with gzip.open(dest / f"{name}.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)


def snapshot() -> Path:
    """Pull everything once and write it immutably under raw/dt=<utc timestamp>/."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    dest = RAW / f"dt={stamp}"
    if dest.exists():
        print(f"{dest} already exists; refusing to overwrite a snapshot.")
        return dest

    f = Fantasy()
    leagues = f.leagues()
    _write(dest, "leagues", leagues)

    # Adapt if the payload is wrapped — shape changed in the 26/27 migration.
    items = leagues.get("elements", leagues) if isinstance(leagues, dict) else leagues
    league_id = str(items[0]["id"])
    my_team_id = str(items[0].get("team", {}).get("id", ""))

    _write(dest, "players", f.players())
    _write(dest, "market", f.market(league_id))
    _write(dest, "standing", f.standing(league_id))
    _write(dest, "activity", f.activity(league_id, 0))
    _write(dest, "current_week", f.current_week())
    if my_team_id:
        _write(dest, "my_team", f.team(league_id, my_team_id))
        _write(dest, "my_lineup", f.lineup(my_team_id))
        _write(dest, "my_money", f.money(my_team_id))

    # Fixtures for the next few jornadas, for the fixture-difficulty features.
    week = f.current_week()
    wk = int(week.get("weekNumber", week) if isinstance(week, dict) else week)
    _write(dest, "calendar", {str(w): f.calendar(w) for w in range(wk, min(wk + 5, 39))})

    (dest / "_SUCCESS").touch()
    print(f"snapshot written: {dest}")
    return dest


def load() -> None:
    """Expose every snapshot to DuckDB as a partitioned, point-in-time view."""
    import duckdb

    con = duckdb.connect(str(ROOT / "warehouse.duckdb"))
    for entity in ("players", "market", "activity", "standing", "my_team"):
        pattern = str(RAW / "dt=*" / f"{entity}.json.gz")
        con.execute(f"""
            CREATE OR REPLACE VIEW raw_{entity} AS
            SELECT *, regexp_extract(filename, 'dt=([^/]+)', 1) AS observed_at
            FROM read_json_auto('{pattern}', filename=true,
                                union_by_name=true, ignore_errors=true)
        """)
    print(con.execute("SHOW TABLES").fetchall())
    con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
    if cmd == "bootstrap":
        bootstrap(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        {"snapshot": snapshot, "load": load}[cmd]()
