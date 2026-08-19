# liga_five_guys — handoff, 2026-08-19 (evening)

Private repo `OrsM/liga_five_guys`, working tree clean, committed through `43f6905`.
**Not pushed** — pushing is opt-in and stays that way.
Read `README.md` first — it holds the design decisions and why each was made.

## Run it

`uv`, never pip — this box has no pip and no python3-venv, and installing them needs sudo.

    cd ~/claude_projects/liga_five_guys
    PYTHONPATH=src FF_ROOT=./data uv run --frozen python src/<module>.py

Every module self-tests under `if __name__ == "__main__"`; there is no pytest and no test
directory. **Work TDD**: add the failing assertion to the module's own `_selftest()`, watch
it fail, then implement.

- `~/.local/bin/lfg-run` — 28 suites (parallel, 4 at a time), fetch, generate, publish,
  commit. `LFG_NO_FETCH=1`, `LFG_NO_COMMIT=1`, `LFG_PUSH=1` are the switches.
- `src/run.py` — the ten generator stages in ONE interpreter. `python src/run.py sim digest`
  runs a subset. Each stage is still runnable alone, which is how a failure gets bisected.
- `lfg.timer` 00:40 and 11:40 local; `lfg-watch.timer` polls the phone's rerun button every 60s.

## Where it stands

Live as of this handoff: cash **-33K** (red), formation **4-5-1**, finish 1.62, P(win) 51%,
season band 1,516–1,775. The report is still the simulation and still one grouped table.

**This session was an audit, not a feature.** Nine commits, and the report barely moved —
which is the point. What changed is that the inputs are honest about their own age, the
store is at the right grain, and half the API payload that was being parsed past is now in
tables. Two wrong numbers were found and fixed on the way.

### Done, in order

1. **Club Elo reads `clubelo.com/ESP`.** The CSV API did not move — its HOST died.
   `api.clubelo.com` still resolves to 37.128.134.74 and answers on neither port; the site
   moved to a new host that 302s every API path to its homepage. The country page embeds
   its ranking chart as a Vega-Lite spec, so the clubs come out of JSON with federation and
   division attached. 200 in 0.14s where the dead host cost 8.1s of an ~11s sweep.
2. **`load_elo()` refuses a stale rating.** A failed fetch left the last rows in place, all
   twenty still joined, and the board ranked the league on form from before the jornada for
   two days. `fresh_only()` gates it, and `methodology.FRESH["daily"]` imports the same
   number so the feed table cannot call a reading "ok" that the scorer threw away.
3. **`transactions.csv` moved to `data/tidy/`**, and three dead knobs left `league.ini`.
4. **The daily allowance accrues from the anchor, whatever labelled it.** It was applied
   only to ESTIMATED balances; an observed balance contains the bonuses paid before it was
   READ and none of the ones paid since. cash.txt's four-day-old anchor was 0.40M light
   while calling itself "known".
5. **Both names the app publishes are kept.** `nickname` AND `name`: 12 of 76 owned players
   join only on the nickname, 3 only on the full name.
6. **A fact is stored once, not once per sweep.** `api_activity` and `api_players` are
   immutable and the feed republishes them whole every time — 1,225 rows carrying 63 events.
   Quadratic, and committed. 103,825 → 5,402 bytes and 69,408 → 3,825.
7. **The rest of the squad feed.** `lastStats` → `data/tidy/api_stats.csv` (long: player ×
   week × stat, with what he did and what it scored). `playerStatus` → the app's own
   fitness. `shielded`. And the bid count, which was blank for 28 of 41 market rows because
   the two listing kinds count bidders under different names.
8. **`api_key` reads the crosswalk**, so an id resolved on any past sweep stays resolved.
9. **The league table moved to its own grain** — `api_standings.csv`, five rows a sweep.
10. **The price argues with a guessed name**, and namesakes are announced.
11. **The parse cache is keyed per parser**, so touching one no longer costs 40s.

## What to do next

1. **THE PRIMARY KEY IS A NORMALISED NAME AND A NAME IS NOT UNIQUE.** This is the biggest
   correctness item left. LaLiga fields an Álvaro García at Villarreal and another at Rayo;
   to this repo they are one player, with one price history built out of both their rows,
   worth 0.50M or 19.76M depending on which row a lookup reaches first. Four of 647 keys
   today, one of them owned. `crosswalk.namesakes()` prints them every run and marks the
   owned ones — that is a warning, not a fix. Fixing it means keying on something unique:
   the market's own slug is the candidate, `players.csv` already carries `market_slug`, and
   two of the four colliding pairs have an EMPTY slug on one side, so it is not a free
   substitution. It touches every reader in the repo. **Do not start this in the last hour
   of a session.**
2. **Nothing yet reads the new data, and wiring it is a MODEL change.** Three separate
   decisions, each needing grading in `METHOD.md` rather than plumbing:
   - `api_stats` — per-week components, including `mins_played`, from the scorer itself.
     This is what could finally grade P(start) against minutes rather than against a
     binary started/did-not.
   - `player_status` — the operator's own fitness, against two editorial scrapes. Store
     both, use one, compare when outcomes exist — the same rule `second.py` follows for AF.
   - `bids` — 41 of 41 rows now state it, and eight players had a live bid on the day this
     was written, two of them in the report's own BUY list. A bid you are about to outbid
     is worth knowing; a decision rule built on it is not (see the standing instruction).
3. **Club Elo's chart is a TOP-N of Spain, not the division.** A top-flight club that sinks
   below the cut simply drops out of the payload. `elo_strength` refuses partial coverage
   so it degrades honestly to squad value, but it would go quiet rather than loud. Worth a
   count in the feed table if it ever bites.
4. **Both fixture widths are still guesses** — ±12% band, +4% home, never fitted. The
   "Next fixture" table in METHOD.md is what will settle them; it has n=1 per bucket.
   Judge nothing yet.
5. **The shape prior is still the seed** (96 observed, 200 needed) — `pool_note()` prints
   which is in use, never assume.
6. **`inputs/` is three files now and two of them exist only for the degradation path.**
   `rosters_initial.txt` changes nothing at all while the API answers (measured: identical
   ownership), and carries it from 30 players to 79 when the API is gone. `cash.txt` is
   worth 0.07M of accuracy against 0.50M without it, and only since the allowance fix.
   `lineup.txt` is the one genuinely hand-written file — the app publishes no fielded flag
   in anything we fetch, so it cannot be derived. If the token ever dies, those two are
   what stands between the report and nonsense; test them before deleting either.

## Traps that cost real time — do not rediscover these

Everything in the previous handoff still holds. New this session:

- **A claim read off the code is not a finding.** I told Miguel `rosters_initial.txt`
  anchored every rival's cash; emptying it changed every balance by zero, because the
  method that would have used it had no callers. He called it out. Measure, then say.
- **A feed that republishes its whole history makes `latest_only` accidentally correct.**
  `load_api_activity` used it and worked only BECAUSE the store kept a copy per sweep.
  Deduplicate the store and the ledger silently collapses to the last few days.
- **The app stamps a whole day's deals with the same minute.** Any sort on the stamp alone
  leaves ties to file order, which is a diff every run. Break them on the app's own id, read
  as an integer — "15676725" sorts before "9629986" as text.
- **Two copies of one fact in two tables is how a number gets added to one and not the
  other.** I had `offers`/`listed_until` on the squad rows until I joined them: all 28
  matched a market row already in `api_market`, same expiry to the second. Deleted.
- **The two market listing kinds use different field names.** `numberOfBids` for an
  app-dealt free agent, `numberOfOffers` for a manager listing his own, and neither row
  carries the other. Reading one left 68% of the market saying "nobody knows".
- **The app's price is an independent identifier and it catches wrong joins.** The two
  sources agree to within 0.2% for a correct join; the wrong one was out by 603%. But
  check a GUESS only — an exact name match is the strongest evidence there is and the
  money must never overrule it.
- **`ast.get_source_segment` re-splits the whole file per node.** It was most of the cost
  of the parser-fingerprint pass: 0.285s → 0.043s slicing off a list split once.
- **A `running` written to a file is a claim that needs an age.** The phone's report page
  adopts run.json on load so a refresh does not lose the console; without a stamp, a run
  the unit killed would leave the button disabled for ever.

## The website, same day

`~/claude_projects/website`, committed `9af9405` and **deployed** (`./deploy.sh`, verified
on the phone). Refreshing the report page mid-run used to lose the console: `FantasyIndex`
initialised its run state to `idle` and the only code that asked `/api/fantasy-run` was the
poller, which is gated on already knowing a run is in flight. Pressing the button again
queued a SECOND run. It now adopts what the box says on mount, `run.json` carries `at`, and
`liveRun` downgrades a `running` older than the unit's TimeoutStartSec to `stale`. The log
renders for `failed` and `stale` too — it was gated on `running`, so the two moments you
most want it were the two that hid it.

## Standing instructions

Be sceptical of the numbers, including mine. Every substantive bug this session was found by
distrusting output, not by reading code. When something looks too good, check it against the
data before shipping it. **When Miguel says a claim of mine is wrong, re-derive it from the
data rather than defending it — he has been right both times.**

Do not hardcode a decision rule. If a rule is needed, it is a sign the metric is wrong.

No prose in the reports. Tables.
