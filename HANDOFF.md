# liga_five_guys — handoff, 2026-08-19 (evening)

Private repo `OrsM/liga_five_guys`, working tree clean, **pushed** through this handoff.
Push by default and when in doubt — Miguel's instruction, 2026-08-19. The timer has been
doing it all along (`LFG_PUSH=1` is in `lfg.service`, and `lfg-run` only logs a push when
it FAILS, which is why the journal looks silent); what was lagging was the by-hand work in
a session. So: commit as you go, and push before you hand over.
Read `README.md` first — it holds the design decisions and why each was made.

## Run it

**THE TIMER IS OFF.** `lfg.timer` was disabled 2026-08-19 at Miguel's request:
the report is run on demand now, from the phone's "Run again" button — which
`lfg-watch.timer` still polls for every 60s — or by hand. Nothing else changed;
`lfg-run` is the same command. Re-enable with
`systemctl --user enable --now lfg.timer`. The unit file carries the same note.

Because nothing runs on a schedule, EVERY READING IS AS OLD AS THE LAST TIME
somebody pressed the button — which is exactly why the freshness gates and the
traffic-light table went in first. Expect ambers in the feed table on a report
generated hours after its sweep; that is the table working.

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

Live as of this handoff: cash **-29K** (red), formation **4-5-1**, finish 1.62, P(win) 51%,
season band 1,516–1,775. The report is still the simulation and still one grouped table.
(The headline cash moved -33K → -29K on purpose: it reads League's estimator now, which
accrues the daily allowance since the anchor, instead of a second raw read of the app's
balance.)

**A later session, same evening, did the freshness work item 6 below asked for.** Three
commits: the API tables are gated on age like Club Elo already was; a gated feed that goes
quiet now says so in the headline and in the warnings that reach the phone; and the never
used per-manager budget override is gone. Nothing in the live report moved except that
cash figure — the whole of it shows up only when the app stops answering, which is the
point. The degradation path was measured, not argued: aged three days, the report keeps
the real 17 points and projects 1,644 against 1,646 fresh, and says twice why it is not
the app's reading.

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

1. **DONE 2026-08-20 — a shared name is keyed `name@club`.** Three names of 651
   belonged to two players each, and one of them was owned: SusoGattuso's Álvaro
   García read as the Villarreal reserve at 0.50M when the app says he holds the
   Rayo one at 20.23M. His squad was 19.73M light, his projected season 27 points
   light, and your odds against him 4 points flattering.

   Four indexes keyed the same rows and now share one rule
   (`ffcore.tidy.shared_names` / `row_key`): the market index, the player index,
   the scorer's lookup and the crosswalk. The market slug was NOT the answer —
   two of the six colliding rows have none. What decides is the club, and where
   a caller has no club the app's own stated value does it instead
   (`Market.key_for(name, value=...)`, the same evidence `api_key` already
   trusts). A shared name asked without either REFUSES rather than picking one.

   Two follow-ons, both small: the crosswalk drops a bare key once the market
   says two men answer to it (it merges rather than rebuilds, so the stale key
   otherwise lives for ever carrying one of their app ids), and
   `inputs/rosters_initial.txt` accepts `alvaro garcia (Rayo)` — the one file a
   human still types a name into.

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
6. **`inputs/` is three files and all three now earn it — measured, on the path that
   actually happens.** The previous handoff suspected `rosters_initial.txt` and `cash.txt`
   were kept for a scenario that could not occur, because "API gone" had been tested with
   an EMPTY feed while a dead token only makes the store STALE. That was right about the
   test and wrong about the conclusion: gating the API tables (this session) is what turns
   stale into empty, so the empty case is now the real degradation path, and both files
   were re-measured against it.

   With the store aged three days and the gate on, against a fresh run:

   | Input | Fresh run | Stale run (the path that happens) |
   |---|---|---|
   | `rosters_initial.txt` | no change at all | squad 15 players → 5, 240.73M → 134.96M |
   | `cash.txt` | no change at all | 49K "known" → -518K "estimated", 0.57M and the label |
   | `lineup.txt` | **deleted** — the app publishes the fielded XI after all | — |

   `lineup.txt` was deleted the same evening. "Nothing we fetch publishes a
   fielded flag" was wrong, and it was wrong because the guesses were made
   under the LEAGUE path: `/v1/competition/1/teams/{team}/lineup/week/{n}`
   returns the eleven, the formation and `teamSnapshotTookOn`. It is fetched
   every run now and three readers share it. The file is the fallback for a
   quiet API, and the next measurement to make is whether that fallback is
   worth keeping at all — the same question, asked of the same file, with the
   answer now pointing the other way.

   So nothing here is a candidate for deletion on the ownership side. What is left of the direction is
   to keep asking what each one buys in units — `cash.txt`'s answer shrinks every time the
   app's own balance is fresh, and it is worth re-running that measurement if the anchor
   ever goes a week untouched.

7. **The remaining API feeds are ungated, and one of them may want it.** `api_leagues` no
   longer reaches the report (the headline cash reads League's estimator now), but the
   table is still swept and still nothing checks its age. `api_activity`, `api_stats` and
   `api_players` are histories and must NOT be gated — that is written in their docstrings
   so it does not get "fixed". `fixtures` is a snapshot and is not gated: a stale fixture
   list would blank the sim entirely, and whether that is more honest than a day-old
   kickoff time has not been measured. Do not change it on the argument alone.


## Traps that cost real time — do not rediscover these

Everything in the previous handoff still holds. New this session:

- **The freshness bound for an every-run feed is NOT half a day, and 0.5 was already in
  the code.** `lfg.timer` fires at 00:40 and 11:40 local, so the two legs are 11h and 13h
  and `RandomizedDelaySec` adds five minutes to either. A feed answering every single
  sweep is 13h10m old at its oldest — measured on the store, the largest gap over 21
  sweeps was 13.0h with nothing missed — so 0.5 days condemns a healthy feed every night.
  `EVERY_RUN_FRESH_DAYS = 0.6` clears the long leg by an hour and still catches a missed
  sweep inside the day.
- **A gate that only refuses is half a fix.** Handing every reader `[]` is honest about
  the data and silent about the reason, and `[]` arrives downstream as "nothing is for
  sale". Aged three days, the report still came out in full and its headline read "market
  0th percentile · a poor week" — a claim about a market it could not see. Whatever you
  gate, give it a `stale_feeds()` sentence next to the numbers it explains.
- **One row can hold two kinds of fact.** `api_standings` carries a balance, which must be
  today's, and a season-to-date point total, which only grows. Gating the row threw both
  away and simulated all five managers from nought — a wrong number where a slightly old
  one was available. `last_api_standings()` is the ungated reader for the history half.

- **"THE APP DOES NOT PUBLISH IT" WAS A GUESS THAT BECAME A FACT BY REPETITION.**
  It was in three docstrings and a handoff, and it was never true: the fielded
  XI hangs off `/teams/{team}/lineup/week/{n}`, not off the league path every
  guess had used. A whole hand-maintained file, and a class of wrong report,
  existed because of it. When a source "must not have" something, spend ten
  minutes probing before designing around the absence.
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
