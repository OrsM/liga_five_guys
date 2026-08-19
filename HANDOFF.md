# liga_five_guys — handoff, 2026-08-20

Private repo `OrsM/liga_five_guys`, working tree clean, **pushed**, 28 suites pass.

## Start here, and do not open with an audit

**This file is the map. Read it, run the one command below, then start on item 1 of
"What to do next".** The last session ended with everything committed, deployed and
verified; there is nothing to discover about the current state that is not written
here, and a session that spends its first twenty tool calls re-deriving it has spent
them on something Miguel already paid for once.

One command confirms the whole state — the suites, the pipeline and the two outputs:

    cd ~/claude_projects/liga_five_guys && \
      LFG_NO_FETCH=1 LFG_NO_COMMIT=1 ~/.local/bin/lfg-run 2>&1 | tail -4

If that says `28 suites pass` and publishes 2 files, nothing has rotted. Read
`README.md` only when you need the WHY behind a design decision — it is 1,085 lines
and is reference, not orientation.

## Run it

`uv`, never pip — this box has no pip and no python3-venv, and installing them needs
sudo.

    PYTHONPATH=src FF_ROOT=./data uv run --frozen python src/<module>.py

Every module self-tests under `__main__`; several need `--selftest` (report, sim,
league, ingest, crosswalk, digest, methodology). There is no pytest and no test
directory. **Work TDD**: add the failing assertion to the module's own `_selftest()`,
watch it fail, then implement.

- `~/.local/bin/lfg-run` — 28 suites, fetch, generate, publish, commit.
  `LFG_NO_FETCH=1`, `LFG_NO_COMMIT=1`, `LFG_PUSH=1` are the switches.
- `src/run.py` — the ten stages in ONE interpreter. `python src/run.py sim digest`
  runs a subset, which is how a failure gets bisected.
- **The scheduled timer is OFF** (disabled 2026-08-19 at Miguel's request). The report
  runs on demand: the phone's "Run again" button, which `lfg-watch.timer` polls for
  every 60s, or `lfg-run` by hand. `systemctl --user enable --now lfg.timer` brings
  the schedule back. Consequence to remember: every reading is as old as the last
  press, which is what the freshness gates and the traffic-light table are for.

## The reports — there are two

    reports/decisions.json    THE report. The phone draws it: position, the XI change
                              list, the ladder, the league table with cash, the
                              warnings. Written by src/sim.py.
    reports/METHOD.md         The methodology. Stitched by src/digest.py from
                              fragments in .runtime/parts/ — build artifacts, not
                              reports.

Nothing else is generated and nothing else is published. **If you add a file to
`reports/`, publish it or do not write it.** REPORT.md was the board's content as
markdown and went on 2026-08-20 along with `reports/history/`: two renderings of one
answer is how they come to disagree, which happened twice in one evening.

## Where it stands

Live: cash **15.98M**, squad **220.04M**, formation **4-5-1** (which is what he is
already fielding), finish 1.24, P(win) 76%, season band 1,612–1,979.

What the last session changed, all of it measured and none of it cosmetic:

1. **The fielded XI comes from the app.** `/v1/competition/1/teams/{team}/lineup/week/
   {n}` returns the eleven, the formation and `teamSnapshotTookOn`. The repo believed
   no such endpoint existed because every guess had been made under the LEAGUE path.
   `inputs/lineup.txt` and its checklist machinery are deleted; `ffcore.league.
   app_fielded` is the one reader, and it returns [] unless EVERY man resolves.
2. **The eleven is a change list**: PUT ON / TAKE OFF against what you are fielding,
   or one line saying there is nothing to change. Not a team sheet you have to diff in
   your head.
3. **A name is not a player.** Three names of 651 are two men; keyed `name@club` now,
   by one rule (`ffcore.tidy.shared_names` / `row_key`) shared by four indexes.
   SusoGattuso's squad was 19.73M light because of it.
4. **The rate's own error is simulated** — one draw per season, sd = pool cv over
   √(matches + K). Band 259 → 365 points, P(win) 74% → 64%. The mean is unchanged by
   construction.
5. **Rivals are credited what the app actually pays** (`flat_income`, measured 1.27M
   on the one account that states a balance), not a guessed daily bonus.
6. **The API tables are gated on freshness** and a quiet feed says so — in the
   headline, in the warnings, and as a traffic light in METHOD.md keyed on when a page
   was last ASKED FOR, not on the re-stamp.

## What to do next

1. **Delete `ffcore/bid.py`. Measure first, then delete.** 630 lines and five
   constants (`MAX_LAG_H`, `ROUND_TO`, `FLOOR_EPS`, `HOLD_DAYS`, `MIN_DRIFT_N`)
   inferring what a rival will bid from the roundness of past prices. Its own
   docstring records that inference being inverted and wrong. The measurement: stub it
   out, run `src/run.py` on the same store, diff `reports/decisions.json` and
   `reports/METHOD.md`. If nothing moves, delete it and its callers; if something
   moves, say what and stop. Half an hour, and it is the whole task.

2. **Split model from render at `decisions.json`.** The one real refactor.
   `report.py` scores AND renders, `sim.py` simulates AND renders, `methodology.py`
   re-reads every tidy table to describe what the other two did — three modules
   re-deriving the same numbers, which is why two surfaces could disagree about the
   formation. Target: the model writes `decisions.json`, every renderer reads only
   that and cannot reach the tidy store. Groundwork is done — the fragments are
   already build artifacts and there is one document left to assemble. Do it in one
   sitting and diff the outputs: they should be byte-identical the day it lands.

3. **Waiting on data — judge nothing yet.** `FIX_BAND` (±12%) and `HOME_EDGE` (+4%)
   are unfitted guesses with n=1 per bucket. `DOUBT_FACTOR = 0.5` is a hardcoded
   decision rule in a repo whose standing instruction forbids them. Club correlation
   in the season risk needs per-jornada per-club history: the pool is at 96 matches
   and grows ~200 a round, and `MIN_POOL = 200` is the same threshold that takes the
   shape prior off its seed — one measurement unlocks both, in about a week.

4. **Fetched but unread.** `api_stats` (per-week components including `mins_played`,
   which is what could grade P(start) against minutes rather than a binary),
   `player_status` (the app's own fitness, against two editorial scrapes), and the bid
   counts. Each is a MODEL decision needing grading in METHOD.md, not plumbing.

## Traps that cost real time — do not rediscover these

- **"THE SOURCE DOES NOT PUBLISH IT" IS A GUESS UNTIL YOU HAVE PROBED THE SHAPE.**
  The fielded XI hangs off `/teams/{team}/...`, not the league path every guess used.
  That guess became a fact by repetition — three docstrings and a handoff — and cost a
  hand-maintained file and a class of wrong report. Vary the path shape, not just the
  noun, before designing around an absence.
- **A claim read off the code is not a finding.** Measure it, then say it. Miguel has
  called this out twice and been right twice.
- **A gate that only refuses is half a fix.** `[]` arrives downstream as "nothing is
  for sale": aged three days the report said "market 0th percentile · a poor week"
  about a market it could not see. Whatever you gate, give it a sentence next to the
  numbers it explains.
- **One row can hold two kinds of fact.** `api_standings` carries a balance that must
  be today's and a points total that only grows. Gating both zeroed everyone's season.
- **`observed_at` is the sweep that CARRIED a page, not when it was fetched.** parse
  re-stamps a carried-forward document, so a page nobody has requested since midnight
  reads as minutes old. The manifest's `seen` is the honest column.
- **Freshness bounds come from the timer, not from a round number.** The legs are 11h
  and 13h, so 0.5 days condemns a healthy feed every night. `EVERY_RUN_FRESH_DAYS`.
- **The app stamps a whole day's deals with the same minute.** Break ties on its own
  id read as an integer — "15676725" sorts before "9629986" as text.
- **The app's price is an independent identifier and it catches wrong joins** (0.2%
  agreement when right, 603% out when wrong) — and it is also what tells two men of
  one name apart. But check a GUESS only: an exact name match is the strongest
  evidence there is and money must never overrule it.
- **Two copies of one fact is how a number gets corrected in one and not the other.**
  The headline cash and league.md's cash were two independent reads until the gate
  moved one of them.

## Standing instructions

Be sceptical of the numbers, including mine. Every substantive bug in the last two
sessions was found by distrusting output, not by reading code. **When Miguel says a
claim of mine is wrong, re-derive it from the data rather than defending it — he has
been right every time.**

Do not hardcode a decision rule. If a rule is needed, it is a sign the metric is wrong.

No prose in the reports. Tables.

And do not explain away a bug by pointing at what the user should have maintained.
The checklist that went stale was ours to remove, not his to re-tick.
