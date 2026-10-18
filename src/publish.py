"""The publish stage: the report to the phone (~/.local/bin/lfg-publish),
then its ping: the board's (LFG_NOTIFY=1) or the line-up reminder
(LFG_REMIND=1). LFG_NO_PUBLISH=1 sends nothing. A ping never fails a run."""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from collections.abc import Callable
from pathlib import Path

import remind

__all__ = ["main", "notify"]

PUBLISH = Path.home() / ".local/bin/lfg-publish"
ACCESS_ENV = Path.home() / ".config/lemonworlds-capture.env"


def _access(env: Path) -> dict[str, str]:
    """Headers for the notes host: Access's service token (KEY=VALUE file)."""
    got = {}
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            k, eq, v = line.strip().partition("=")
            if eq and not k.startswith("#"):
                got[k] = v.strip().strip("'\"")
    return {"CF-Access-Client-Id": got.get("CF_ACCESS_CLIENT_ID", ""),
            "CF-Access-Client-Secret": got.get("CF_ACCESS_CLIENT_SECRET", ""),
            # Cloudflare refuses Python's own User-Agent (error 1010: 403 before Access)
            "User-Agent": "liga_five_guys/lfg-run"}


def _post(url: str, headers: dict[str, str], body: bytes | None) -> str:
    req = urllib.request.Request(url, data=body if body is not None else b"", headers=headers,
                                 method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 -- a fixed https host
        return str(r.read().decode("utf-8", "replace"))


def notify(text: str | None = None, env: Path = ACCESS_ENV,
           post: Callable[[str, dict[str, str], bytes | None], str] = _post) -> bool:
    """Send text (none: the board's ping); True only on {"sent": true}."""
    if os.environ.get("LFG_NO_PUBLISH") == "1":
        print("  (no ping: LFG_NO_PUBLISH=1)")
        return False
    host = os.environ.get("LFG_NOTES_HOST", "notes.lemonworlds.com")
    headers = _access(env)
    body = None
    if text is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps({"text": text}).encode()
    try:
        answer = post("https://%s/api/fantasy-notify" % host, headers, body)
        sent = json.loads(answer).get("sent") is True
    except (OSError, ValueError, AttributeError) as e:
        print("  notify failed: %s (the report is still live, just no ping)" % e)
        return False
    print("  ping sent" if sent else "  notify failed: %s" % answer[:200])
    return sent


def main() -> dict[str, int]:
    """Publish, then ping as asked."""
    if os.environ.get("LFG_NO_PUBLISH") == "1":
        print("  (skipped: LFG_NO_PUBLISH=1)")
        return {"published": 0}
    if subprocess.run([str(PUBLISH)], check=False).returncode != 0:
        raise SystemExit("publish failed: the report is still in the repo")
    sent = 0
    if os.environ.get("LFG_NOTIFY") == "1":
        sent = int(notify())
    elif os.environ.get("LFG_REMIND") == "1":
        said = remind.text()
        if said:
            sent = int(notify(said))
        else:
            print("  reminder: the line-up is your best eleven, nothing to say")
    return {"published": 1, "pinged": sent}


def _selftest() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / "a.env"
        env.write_text("# a comment\nCF_ACCESS_CLIENT_ID='abc'\nCF_ACCESS_CLIENT_SECRET=\"xyz\"\nOTHER=1\n")
        assert _access(env) == {"CF-Access-Client-Id": "abc", "CF-Access-Client-Secret": "xyz",
                                "User-Agent": "liga_five_guys/lfg-run"}, "Python's own User-Agent is refused"
        assert _access(Path(tmp) / "none")["CF-Access-Client-Id"] == ""
        seen: list[tuple[str, dict[str, str], bytes | None]] = []

        def answer(said: str) -> Callable[[str, dict[str, str], bytes | None], str]:
            def post(url: str, headers: dict[str, str], body: bytes | None) -> str:
                seen.append((url, headers, body))
                return said
            return post
        assert notify("hi", env, answer('{"sent": true}'))
        url, headers, body = seen[-1]
        assert url.endswith("/api/fantasy-notify") and headers["CF-Access-Client-Id"] == "abc"
        assert body is not None and json.loads(body) == {"text": "hi"}
        assert notify(None, env, answer('{"sent": true}')) and seen[-1][2] is None, \
            "no text: the server sends the board's ping"
        assert not notify("hi", env, answer('{"sent": false}')), "a 200 that did not send"
        assert not notify("hi", env, answer("<html>login</html>")), "Access's login page"

        def down(url: str, headers: dict[str, str], body: bytes | None) -> str:
            raise OSError("no route")
        assert not notify("hi", env, down), "a network failure is no ping, never a crash"
    print("publish self-test OK")


if __name__ == "__main__":
    _selftest()
