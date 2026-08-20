"""
auth.py — the bearer token for LaLiga's own API, and the one thing that can
lose it.

The only module in this repo that holds a credential: everything else reads
public pages anonymously.

THE ROTATION TRAP is why this file is careful. B2C issues a refresh token good
for 90 days and ROTATES IT ON EVERY EXCHANGE — spend one and it dies, with the
replacement in the response. Lose that and re-authenticating needs a human at
a browser. So:

  * the write is atomic — temp file, fsync, rename — because a half-written
    token file is indistinguishable from no token file, and costs a re-login;
  * the new token is written BEFORE the caller is handed anything, so a crash
    in the caller cannot lose it;
  * the file is 0600 and lives outside the repo, because `git add -A data` is
    in the daily job and a credential must never be one glob away from a push.

The first token needs an interactive Facebook login, so it is a human job,
once: `python -m ffcore.auth --login`.

httpx is imported inside the fetching functions, so the test job can import
this module without a network client.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

__all__ = ["TokenStore", "authorize_url", "TENANT",
           "CLIENT_ID", "SIGNIN_POLICY", "REDIRECT_URI", "API_BASE"]

# --- the tenant -----------------------------------------------------------
# Lifted from Externoak/LaLigaApp (GPL-3.0) src/services/authService.js and
# then verified against the live tenant on 2026-08-18. Recorded here rather
# than in a config file because they are not tuning knobs: change any one of
# them and you are talking to a different product.
TENANT = ("https://login.laliga.es/laligadspprob2c.onmicrosoft.com"
          "/oauth2/v2.0")
CLIENT_ID = "af88bcff-1157-40a0-b579-030728aacf0b"   # public client, no secret
SIGNIN_POLICY = "B2C_1A_5ULAIP_PARAMETRIZED_SIGNIN"
# jwt.ms is one of this client's REGISTERED reply URLs — verified: every other
# value comes back AADB2C90006, and the error is itself delivered to jwt.ms.
# That is what makes the one-time login a plain browser tab instead of an
# Electron app registering the authredirect:// scheme.
REDIRECT_URI = "https://jwt.ms"

# The /api prefix is NOT decoration. Without it every path 404s with a
# {"code","message"} body that looks exactly like a permissions failure and
# sends you back to re-check the token you just minted.
API_BASE = "https://fantasy-api.llt-services.com/api"

TOKEN_PATH = Path(
    os.environ.get("LFG_TOKEN",
                   Path.home() / ".config" / "liga_five_guys" / "token.json"))

# Refresh this many seconds before the access token actually dies. A sweep
# takes seconds, but a token that expires mid-sweep fails half the sources and
# looks like site rot.
SKEW = 300


class TokenStore:
    """The token file, and the only thing allowed to write it."""

    def __init__(self, path: Path = TOKEN_PATH):
        self.path = Path(path)

    # -- reading ----------------------------------------------------------
    def load(self) -> dict:
        if not self.path.exists():
            raise FileNotFoundError(
                f"no token at {self.path}. Run `python -m ffcore.auth "
                f"--login` and follow it — it needs a browser once.")
        with open(self.path) as fh:
            return json.load(fh)

    # -- writing, atomically ----------------------------------------------
    def save(self, tokens: dict) -> None:
        """Replace the token file in one step, or not at all.

        Written to a temp file in the same directory (so rename cannot cross a
        filesystem), fsynced, then renamed over the target. rename is atomic on
        POSIX: a reader sees the old file or the new one, never a truncated
        one. Without the fsync the rename can land before the bytes do, which
        on a box that loses power is the same as losing the token.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        tmp = self.path.with_suffix(".json.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(tokens, fh, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    # -- the exchange ------------------------------------------------------
    def refresh(self, post=None) -> dict:
        """Spend the refresh token, persist what comes back, return it.

        `post` is injectable so the self-test can drive the rotation logic
        without a network or a real credential.
        """
        cur = self.load()
        rt = cur.get("refresh_token")
        if not rt:
            raise RuntimeError(
                f"{self.path} has no refresh_token — re-login required.")
        form = {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": rt,
            # Asking for the client_id as a scope is B2C's way of saying "and
            # an access token for that app, please". Without it the response
            # carries an id_token only. Both are accepted as bearers by this
            # API today, but relying on that would be relying on an accident.
            "scope": f"openid {CLIENT_ID} offline_access",
        }
        res = (post or _post)(f"{TENANT}/token?p={SIGNIN_POLICY}", form)
        if not res.get("refresh_token"):
            # Never persist a response that would leave us unable to refresh
            # again; the old token may still be good.
            raise RuntimeError(
                "refresh response carried no refresh_token; keeping the old "
                "one. If this repeats, re-login.")
        res["obtained_at"] = int(time.time())
        self.save(res)                     # persist BEFORE returning
        return res

    # -- what callers actually want ---------------------------------------
    def bearer(self, post=None) -> str:
        """A valid access token, refreshing only when the held one is stale."""
        try:
            cur = self.load()
        except FileNotFoundError:
            raise
        tok = cur.get("access_token")
        got = int(cur.get("obtained_at") or 0)
        ttl = int(cur.get("expires_in") or 0)
        if tok and got and time.time() < got + ttl - SKEW:
            return tok
        fresh = self.refresh(post=post)
        return fresh.get("access_token") or fresh["id_token"]

    def expiry_days(self) -> float | None:
        """Days until the REFRESH token dies — the one that needs a human.

        None when the file does not say. The report prints this so a login
        that is about to be needed is visible before it is needed.
        """
        try:
            cur = self.load()
        except FileNotFoundError:
            return None
        got = int(cur.get("obtained_at") or 0)
        life = int(cur.get("refresh_token_expires_in") or 0)
        if not got or not life:
            return None
        return (got + life - time.time()) / 86400.0


def _post(url: str, form: dict) -> dict:
    import httpx
    r = httpx.post(url, data=form, timeout=30,
                   headers={"Content-Type":
                            "application/x-www-form-urlencoded"})
    if r.status_code != 200:
        # The body carries B2C's own error code (AADB2C…), which is the only
        # thing that distinguishes "expired, re-login" from "wrong client".
        raise RuntimeError(f"token refresh failed {r.status_code}: "
                           f"{r.text[:300]}")
    return r.json()




def authorize_url(verifier: str | None = None) -> tuple[str, str, str]:
    """(url, verifier, state) for the one interactive login.

    PKCE S256. The verifier never leaves this machine and is what stops a
    stolen authorization code from being redeemable by anyone else.
    """
    import base64
    import hashlib
    import secrets
    import urllib.parse

    def b64(b):
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    verifier = verifier or b64(os.urandom(32))
    challenge = b64(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_hex(8)
    q = urllib.parse.urlencode({
        "p": SIGNIN_POLICY, "client_id": CLIENT_ID, "response_type": "code",
        "redirect_uri": REDIRECT_URI, "scope": "openid offline_access",
        "code_challenge": challenge, "code_challenge_method": "S256",
        "state": state, "nonce": state,
    })
    return f"{TENANT}/authorize?{q}", verifier, state


# --------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if "--login" in sys.argv:
        url, verifier, state = authorize_url()
        print("1. Open this, sign in, and copy the address bar you land on:\n")
        print(url)
        print(f"\n2. Then run:\n   python -m ffcore.auth --code '<that URL>' "
              f"--verifier {verifier} --state {state}")
        sys.exit(0)

    if "--code" in sys.argv:
        import urllib.parse
        a = sys.argv
        got = a[a.index("--code") + 1]
        verifier = a[a.index("--verifier") + 1]
        want = a[a.index("--state") + 1] if "--state" in a else None
        q = urllib.parse.parse_qs(urllib.parse.urlparse(got).query)
        if "error" in q:
            sys.exit(f"B2C said: {q['error'][0]} — "
                     f"{q.get('error_description', [''])[0]}")
        if want and (q.get("state") or [None])[0] != want:
            sys.exit("state mismatch — that reply is not to this request.")
        code = (q.get("code") or [got])[0]
        res = _post(f"{TENANT}/token?p={SIGNIN_POLICY}", {
            "grant_type": "authorization_code", "client_id": CLIENT_ID,
            "code": code, "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier, "scope": "openid offline_access"})
        res["obtained_at"] = int(time.time())
        TokenStore().save(res)
        print(f"stored in {TOKEN_PATH} — refresh token good for "
              f"{int(res.get('refresh_token_expires_in', 0)) / 86400:.0f} days")
        sys.exit(0)

    if "--status" in sys.argv:
        s = TokenStore()
        d = s.expiry_days()
        print(f"{s.path}: refresh token "
              f"{'unknown age' if d is None else f'{d:.1f} days left'}")
        sys.exit(0)

    # -- self-test: rotation, atomicity, and the refusals ------------------
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "token.json"
        s = TokenStore(p)

        # A missing file names the fix rather than raising KeyError somewhere.
        try:
            s.load()
            raise AssertionError("missing token should raise")
        except FileNotFoundError as e:
            assert "--login" in str(e), e

        s.save({"refresh_token": "R1", "access_token": "A1",
                "expires_in": 3600, "refresh_token_expires_in": 7776000,
                "obtained_at": int(time.time())})
        assert oct(p.stat().st_mode)[-3:] == "600", oct(p.stat().st_mode)

        # A live access token is reused; no exchange, so no rotation.
        calls = []

        def never(url, form):
            calls.append(form)
            raise AssertionError("should not have refreshed")

        assert s.bearer(post=never) == "A1"
        assert calls == []

        # An expired one refreshes, and the ROTATED refresh token is what
        # lands on disk. This is the whole point of the module.
        s.save({"refresh_token": "R1", "access_token": "A1",
                "expires_in": 3600, "refresh_token_expires_in": 7776000,
                "obtained_at": int(time.time()) - 4000})

        def rotate(url, form):
            assert form["refresh_token"] == "R1", form
            assert form["client_id"] == CLIENT_ID, form
            assert CLIENT_ID in form["scope"], form
            return {"access_token": "A2", "refresh_token": "R2",
                    "expires_in": 3600, "refresh_token_expires_in": 7776000}

        assert s.bearer(post=rotate) == "A2"
        assert json.load(open(p))["refresh_token"] == "R2", "did not rotate"

        # A response with no refresh_token must NOT be persisted: the old one
        # may still work, and overwriting it would force a browser login.
        def no_rt(url, form):
            return {"access_token": "A3", "expires_in": 3600}

        s.save({"refresh_token": "R2", "access_token": "A2",
                "expires_in": 3600, "obtained_at": int(time.time()) - 4000})
        try:
            s.bearer(post=no_rt)
            raise AssertionError("should refuse a response with no refresh")
        except RuntimeError as e:
            assert "no refresh_token" in str(e), e
        assert json.load(open(p))["refresh_token"] == "R2", "clobbered!"

        # A failed write leaves the previous token intact.
        before = p.read_text()
        try:
            s.save({"bad": {1, 2}})            # a set is not JSON
        except TypeError:
            pass
        assert p.read_text() == before, "a failed save damaged the token"
        assert not list(Path(d).glob("*.tmp")), "left a temp file behind"

        # expiry_days reads the window a human cares about.
        s.save({"refresh_token": "R", "refresh_token_expires_in": 86400 * 10,
                "obtained_at": int(time.time())})
        assert 9.9 < s.expiry_days() < 10.1, s.expiry_days()

    # The authorize URL carries a real S256 challenge, not the verifier.
    import base64
    import hashlib
    url, ver, st = authorize_url("v" * 43)
    want = base64.urlsafe_b64encode(
        hashlib.sha256(("v" * 43).encode()).digest()).rstrip(b"=").decode()
    assert f"code_challenge={want}" in url, url
    assert "code_challenge_method=S256" in url
    assert ("v" * 43) not in url, "verifier must never be in the URL"
    assert f"state={st}" in url and f"nonce={st}" in url

    print("auth.py: all self-tests passed")
