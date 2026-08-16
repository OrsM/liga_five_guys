"""shots.py — screenshots pasted into an issue comment become today's slate.

    COMMENT_BODY="$(cat body.md)" python src/shots.py
    python src/shots.py --selftest

The market screen is the one input that cannot be scraped: it is private league
state, and the official API is unreachable (README). It has always arrived by
hand, typed into a one-line workflow input from a phone — the worst step of the
routine. A comment on the pinned Commands issue carries images natively, so the
phone does what the phone is good at and this module does the rest: pull the
attachment URLs out of the comment, OCR them, and hand the names to seen.py,
which already knows how to turn mangled text into player keys.

NAMES ONLY, NEVER PRICES. Values are scraped to the euro and the minimum legal
bid IS the market value, so the only thing a screenshot has to tell us is who
is on offer. seen.py's docstring has the long version.

Every capture is a set, so the order of the images never matters and resending
is free. Duplicates are collapsed AFTER resolution, not on the raw text: two
scrolled shots of the same slate spell the same player differently ("Inigo Ruiz
Galarreta" and "I. Ruiz de Galarreta"), and only the resolver knows they are one
man.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.league import League  # noqa: E402
from ffcore.text import norm  # noqa: E402
from ffcore.tidy import input_path  # noqa: E402
from seen import match, read_names  # noqa: E402

# How many players the app deals per cycle. The reply says 12/12 or names what
# is missing, because a slate short by three is indistinguishable from three
# players nobody bid on — and the report would price the short list as if it
# were the whole market.
SLATE = 12

# GitHub rewrites every attachment to one of these hosts. Anything else in the
# comment is a link somebody pasted, not a screenshot of the market.
HOSTS = ("github.com/user-attachments/", "githubusercontent.com/")

# Markdown ![](url) and the HTML <img src="url"> the mobile app sometimes
# produces. Both, because which one you get depends on how the image was added.
IMG_RE = re.compile(r'!\[[^\]]*\]\(([^)\s]+)\)|<img[^>]+src="([^"]+)"')


def image_urls(body: str) -> list[str]:
    """Attachment URLs in a comment body, in order, without repeats.

    Order is preserved only to make the reply readable; the parse takes the
    union, so it carries no meaning.
    """
    out = []
    for md, html in IMG_RE.findall(body or ""):
        url = md or html
        if any(h in url for h in HOSTS) and url not in out:
            out.append(url)
    return out


def ocr(path: Path) -> str:
    """Spanish-language OCR of one screenshot.

    Fails loudly on a non-zero exit rather than returning nothing: a silent
    empty read would drop players from the slate, and a short slate reads as
    "nobody else is on offer", which is the opposite of the truth.
    """
    r = subprocess.run(["tesseract", str(path), "stdout", "-l", "spa"],
                       capture_output=True, text=True, check=True)
    return r.stdout


def slate_lines(keys: set, players: dict, unresolved: list,
                ambiguous: list, resolved: list, images: int) -> list[str]:
    """The reply body: what was read, what resolved, what did not.

    The completeness line is the point of the whole reply. Everything else
    explains it.
    """
    names = sorted(players[k].get("name", k) for k in keys if k in players)
    out = ["**/market** — read %d image(s)." % images, ""]

    mark = "✓" if len(keys) == SLATE else "—"
    out.append("market: %d/%d %s" % (len(keys), SLATE, mark))
    if len(keys) < SLATE:
        out.append("")
        out.append("_Short of %d: send another shot, or the names below never "
                   "resolved._" % SLATE)
    out.append("")

    if names:
        out += ["On offer: " + ", ".join(names), ""]
    for raw, cands in ambiguous:
        out.append('- "%s" matches %s — resend with a surname.'
                   % (raw, ", ".join(cands)))
    for raw in unresolved:
        out.append('- "%s" matched nothing.' % raw)
    for line in resolved:
        out.append("- " + line)
    if ambiguous or unresolved or resolved:
        out.append("")
    return out


def write_slate(keys: set, players: dict, unresolved: list,
                path: Path | None = None) -> None:
    """inputs/seen.txt — resolved names, plus the raw text of what didn't.

    Resolved names go in canonical spelling, so a resend of the same slate
    produces the same file. The unresolved lines go in as typed, because
    report.py reports them too, and a name only this module knew about would
    vanish from the report the moment the comment scrolled away.
    """
    lines = sorted(players[k].get("name", k) for k in keys if k in players)
    (path or input_path("seen.txt")).write_text(
        "\n".join(lines + sorted(set(unresolved))) + "\n", encoding="utf-8")


def main() -> int:
    body = os.environ.get("COMMENT_BODY", "")
    urls = image_urls(body)
    if not urls:
        print("no attachments in the comment — nothing to read.")
        return 1

    import httpx

    text = []
    with tempfile.TemporaryDirectory() as tmp:
        with httpx.Client(timeout=30.0, follow_redirects=True) as c:
            for i, url in enumerate(urls):
                r = c.get(url)
                r.raise_for_status()
                shot = Path(tmp) / ("shot%d.png" % i)
                shot.write_bytes(r.content)
                text.append(ocr(shot))

    lg = League.load()
    by_key = lg.market.latest() if lg.market else {}
    keys, unresolved, ambiguous, resolved = match(
        read_names("\n".join(text)), by_key, lg.owner)
    write_slate(keys, by_key, unresolved)

    out = slate_lines(keys, by_key, unresolved, ambiguous, resolved, len(urls))
    reply = os.environ.get("REPLY_FILE")
    if reply:
        Path(reply).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    return 0


def _selftest() -> None:
    n = 0

    # Both image shapes, deduplicated, in order.
    body = ("/market\n"
            "![](https://github.com/user-attachments/assets/aaa)\n"
            '<img src="https://github.com/user-attachments/assets/bbb">\n'
            "![](https://github.com/user-attachments/assets/aaa)\n")
    assert image_urls(body) == [
        "https://github.com/user-attachments/assets/aaa",
        "https://github.com/user-attachments/assets/bbb"], image_urls(body)
    n += 3

    # A link that is not an attachment is not a screenshot.
    assert image_urls("/market ![](https://example.com/market.png)") == []
    assert image_urls("/market see https://github.com/OrsM/x/issues/30") == []
    assert image_urls("") == []
    n += 3

    players = {
        norm("Álvaro Valles"): {"name": "Álvaro Valles"},
        norm("Dani Martínez"): {"name": "Dani Martínez"},
    }
    keys = {norm("Álvaro Valles")}

    # Completeness is stated whether or not it is met, and a short slate says
    # what to do about it.
    full = slate_lines(set(players), players, [], [], [], 2)
    assert "read 2 image(s)" in full[0], full[0]
    short = "\n".join(slate_lines(keys, players, [], [], [], 1))
    assert "market: 1/%d" % SLATE in short, short
    assert "Short of %d" % SLATE in short, short
    assert "On offer: Álvaro Valles" in short, short
    n += 4

    # Unresolved, ambiguous and ownership-resolved names all reach the reply:
    # a name the resolver dropped is a player you would otherwise think is not
    # on offer.
    msg = "\n".join(slate_lines(keys, players, ["Xyzzy"],
                               [("Dani", ["Dani Martínez", "Dani Lorenzo"])],
                               ["**Dani** → Dani Martínez — the only one"], 1))
    assert '"Xyzzy" matched nothing' in msg, msg
    assert '"Dani" matches Dani Martínez, Dani Lorenzo' in msg, msg
    assert "the only one" in msg, msg
    n += 3

    # seen.txt carries canonical spellings plus whatever didn't resolve, once
    # each, so a resend of the same slate writes the same file.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "seen.txt"
        write_slate(keys, players, ["Xyzzy", "Xyzzy"], path)
        got = path.read_text(encoding="utf-8").split("\n")
    assert got[:2] == ["Álvaro Valles", "Xyzzy"], got
    n += 1

    print("shots self-test OK (%d cases)" % n)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
