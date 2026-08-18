# liga_five_guys

Data-driven decision system for LaLiga Fantasy Oficial. Private league of 5
managers. Runs on the Asus box, twice a day, unattended.

Personal use only. Don't redistribute the scraped data.

## Where it runs, and why it moved

**It used to run entirely on GitHub Actions — no local machine, phone only.**
That ended on 2026-08-18, when the league's own API came within reach.

The API is what finally supplies the three things no public page publishes:
the market as the app actually deals it, every transaction, and the balances.
It is reached with an OAuth token that **rotates on every single use**, and
persisting a rotating secret back into repo secrets means a PAT, an API call,
and a write that fails closed — one missed write and the next run has nothing.
On this box it is a file the job rewrites in place. That is the whole reason.

    systemctl --user status lfg.timer     # is it running
    systemctl --user start lfg.service    # run it now
    journalctl --user -u lfg.service -n 50   # what happened

`~/.local/bin/lfg-run` is the chain; `lfg-publish` pushes the finished report
to the phone, which serves it at **notes.lemonworlds.com/fantasy** behind
Cloudflare Access.

**There is no GitHub Actions workflow any more.** Its schedule went when the
report moved here, and its test job went with it on 2026-08-18: with nothing
pushing, a job that runs on push is not a schedule. The self-tests run at the
top of every `lfg-run` instead, and a failure stops the run rather than
publishing a report built by code that does not pass its own checks.

## The one table

**`reports/REPORT.md`** is the only file you need to read. One table — the
board — ranking every asset you could hold, owned or not, against cash.

**ONE METRIC RUNS IT: pts/M**, points above replacement per million euros.
Replacement level is fixed by the rules — five squads, and the shapes each can
legally field — so it does not move when your eleven does. **CASH is a row in
the same table**, with a rate of its own: above the line an asset beats the
money, below it the money beats the asset.

λ came before this and is **retired**. It measured the same exchange rate
against *your current eleven*, off a ladder built from the whole unowned pool,
so the baseline moved: the same player was worth different amounts on
consecutive days for reasons that had nothing to do with him, and the ladder
priced a market you cannot shop in. That is how the report came to hold Fornals
on the board and sell him three tables later, in the same unit. The code went
with it on 2026-08-18 — `Lambda`, `Rung`, `frontier()`, `verdict()`,
`sell_test()`, `Sale`, `marginal()` and three functions in report.py, none of
them reachable from anything. They were kept for a fortnight on the argument
that `lambda_log.csv` was the only evidence for whether replacing λ helped;
that argument does not survive being written down, because the LOG holds the
evidence and it is still here. git remembers the code.

The five numbered sections it replaced are still generated, in
`reports/latest.md`, as workings.

Everything else is reference and is **linked**, not reprinted.

### The second table, on trial

Underneath the board, REPORT.md now carries **the simulation** —
`reports/sim.md`, from `src/sim.py`. It answers the board's question by
playing the rest of the league out a few thousand times for every move you
could make, and ranking them by **Δ expected finishing position** and
**Δ P(winning)** rather than by a rate. There is no metric to explain and no
threshold to tune, because the column *is* the answer.

**Both are printed, and the board is still in charge.** The point of a
side-by-side jornada is to compare them on real data before the old one goes;
the board is the thing that currently works, and the forecast under the
simulation still rests on approximations that all flatter a lead. They are
listed at the foot of `sim.md` on every run, read off the data rather than
remembered — including which shape prior is in use, and whether a jornada is
half played.

What the simulation can express and no per-player rate can: **a steal is worth
roughly twice what the same player is worth from the free pool**, because
taking a rival's man raises your total *and* lowers his. On the day it was
first published, every one of the top eight moves was a steal or a swap aimed
at SusoGattuso — the only rival inside the noise — and the best of them moved
P(win) 36 points, from a 140-point swing against a difference-of-totals spread
of 132.

When the board goes, the deletions are listed in `sim.py`'s docstring and in
the handoff: `pts/M`, `above repl`, `the line`, the basket, `THIN`,
`MAX_SLOT` as a decision rule, and every verdict string.

The manual workflow keeps two inputs: `fetch` (off = rebuild from stored HTML)
and `baseline` (once a season). `lookup` and `seen` went with the tools that
read them — `find_slug.py` resolved app spellings the API now supplies on both
sides, and `seen` was the OCR'd market slate.

## What you edit

**One file.** `inputs/lineup.txt` — the marks, never the names. `[x]` fielded,
`[ ]` benched. It is regenerated every run: your marks survive, sold players
vanish, anyone you just bought arrives benched.

That is the whole routine now. Three of the four files that used to need you
are derived from the league's own API:

| File | Was | Now |
|---|---|---|
| `transactions.csv` | append a row after every deal | **generated** from the app's activity feed by `src/ledger.py` |
| `cash.txt` | a balance you read off a screen | your balance comes from the app, to the euro, every run |
| `rosters_initial.txt` | the starting rosters, written once | ownership comes from the app directly — no replay to seed |
| `seen.txt` | OCR the market screenshot | **deleted** — the market feed, all 41 rows, with bid counts |
| `squad.txt` | generated fallback roster | **deleted** — see below |
| `deadline.txt` | typed lock time | **deleted** — the next kickoff is the lock |

**Why this changed, in one incident.** `transactions.csv` was the one input
that could silently fall behind, and on 2026-08-17 it was three days behind.
Ownership and cash are both replayed from it, so the report offered a **63.29M
budget against a real 23.60M** and recommended selling a player who had already
gone. The file was never wrong; it was late, which for a decision system is the
same thing. A feed cannot forget.

`cash.txt` and `rosters_initial.txt` are **kept, not deleted**: the API states
`teamMoney` for the account that asks and `null` for every other team, so
rivals' cash is still an estimate and one overheard balance still turns it into
arithmetic. The `~` in the reports is still honest.

`league.ini` (thresholds and the starting budget) is the one you touch
occasionally.

**Three files were deleted rather than kept, and each for the same reason: a
fallback that cannot fire is worse than none.**

- `squad.txt` was a generated copy of your roster, read only when `League`
  failed to load — but `squads.py` is what wrote it and needs `League` too, so
  a fresh one could never exist at the moment it was wanted. All it could do
  was serve a stale squad, silently, exactly when you needed to be told.
- `deadline.txt` was read when no fixture was available, and was wrong the
  moment it expired and stayed wrong until noticed — which happened: it held a
  lapsed date and the report read it as "deadline passed". The next kickoff in
  `fixtures.csv` is the lock; if that cannot answer, the report says so.
- `lookup.txt` fed `find_slug.py`, which resolved app spellings to CSV names.
  The API now gives both sides of that join.

Every file under `inputs/` states in its own header what it is for, who writes
it and who reads it.

**The old `bench.txt` is gone.** It named who was *not* in your XI, which stopped
scaling the moment the bench was four names, and once `lineup.txt` existed the
two could contradict each other — silently, since whichever was read first
won. One file describes your eleven now.

## The slate

**You no longer read this off your phone.** The league's market feed carries
everything on offer — 41 rows the day it landed, against the dozen a
screenshot showed — and it distinguishes the free agents the app deals
(`marketPlayerLeague`) from players a manager has listed (`marketPlayerTeam`),
which is 28 of those 41 and was invisible before. It also carries
`numberOfBids`: how many people are already bidding, which nothing else in this
repo could ever see.

`seen.py` was 348 lines — two input shapes, exact-then-substring-then-token
matching, an ambiguity report, and an ownership prune to break ties the string
could not. All of it careful, and all of it in service of reading text off a
photograph. It is gone, and the fallback went with it: the token lasts 90 days,
the report warns 14 days out, and renewing it is a browser tab and a paste.
Kept "just in case", 348 lines exercised roughly never would be broken by the
time they mattered — and worse than broken, they would quietly serve a stale
slate from whatever `seen.txt` still held while the report looked normal.

## Routine

- **Most days:** open `reports/REPORT.md`. Usually nothing to do.
- **Thursday/Friday:** probable XIs firm up. This is when the report earns its
  keep and when to spend.
- **After any deal:** ~~add the row to `inputs/transactions.csv`~~ — nothing.
  The feed has it before you could have typed it.
- **Whenever a rival mentions a balance:** put it in `inputs/cash.txt`. Still
  worth doing: theirs is the one balance the API will not tell you.
- **Every ~90 days**, or when the report says the login is close to expiring:
  `python -m ffcore.auth --login`. It needs a browser once.

## Layout

```
src/                 sources.py (the registry: futbolfantasy, Analítica,
                       Club Elo, and the league's own API)
                     ingest.py (fetch, parse, prune — the only network code)
                     ledger.py (the activity feed -> transactions.csv)
                     squads.py  report.py  rivals.py  slate.py
                     decide.py (every move, ranked by Δ P(finish above))
                     sim.py (that ranking, written out as reports/sim.md)
                     digest.py (stitches REPORT.md)
                     points.py  xi.py  methodology.py
src/ffcore/          shared core: parse (numbers)  text (names)  tidy (IO+time)
                     auth (the B2C token — the only credential here)
                     league (ownership+cash)  score (ratings+XI)
                     fixture (next opponent, difficulty)
                     bid (premiums, bid bands, XI gain, the basket)
                     season (LeagueState, simulate, best_xi)
                     forecast (Forecaster: expected() / draw())
                     render (names, for display — never a key)
inputs/              you edit these — see above
data/raw/dt=….tar.xz  raw HTML, deduplicated — append-only, never delete
data/tidy/market.csv  values, disposable — rebuilt from raw every run
data/tidy/lineups.csv probable XI + fitness, one row per player per source
data/tidy/fixtures.csv kickoffs, as published — the deadline is derived here
data/tidy/elo.csv     Club Elo ratings, Spanish top flight — the fixture rank
data/tidy/matches.csv  the season's 380 matches, with the score once played
data/tidy/starters.csv who actually started — what grades the probable XIs
data/tidy/api_market.csv   the market as the app deals it, with bid counts
data/tidy/api_teams.csv    every squad, from the app; your balance
data/tidy/api_activity.csv every deal, as the app recorded it
data/tidy/api_players.csv  id -> name, append-only; names the feed's history
data/decisions/      append-only logs of estimates, for scoring later
.runtime/alerts.md   gitignored; exists only when something wants a decision
reports/REPORT.md    ← read this
reports/latest.md    the five tables (report.py) — carried into REPORT.md
reports/sim.md       the simulation (sim.py) — carried into REPORT.md, on trial
reports/rivals.md    how rivals bid: premiums, drift, projected XIs (rivals.py)
reports/squads.md    every squad, deal history, cash basis (squads.py)
reports/watchlist.md everyone unowned, ranked (squads.py)
reports/methodology.md  the formula and how it is tracking (methodology.py)
docs/design.md       architecture, data sources, modelling plan
```

## Tests

No test directory and no pytest. Each module self-tests under
`if __name__ == "__main__"`, and `lfg-run` runs all twenty-three before it
fetches anything. Twenty seconds, and a failure aborts the run.

**Work TDD.** Add the failing assertion to the module's own `_selftest()`,
watch it fail, then implement. The three bugs in the Design notes below were
all found that way and none of them by reading the code.

Dependencies are `uv`, not pip: this box has no `pip` and no `python3-venv`,
and installing them needs sudo. `uv sync` once, then `uv run --frozen python …`
— the same pattern `n2t-api.service` uses. `--frozen` so a run never tries to
re-resolve dependencies, because a boot without network would otherwise hang.

```
python src/ffcore/parse.py                      # number parsing + formatting
PYTHONPATH=src python src/ffcore/auth.py        # token rotation, atomicity
PYTHONPATH=src python src/ledger.py --selftest  # the derived ledger + guards
PYTHONPATH=src python src/ffcore/tidy.py        # the player view over tidy CSV
PYTHONPATH=src python src/sources.py            # parsers + signatures
PYTHONPATH=src python src/ingest.py --selftest   # archives + carry-forward
PYTHONPATH=src python src/ffcore/league.py --selftest   # config + cash
PYTHONPATH=src python src/ffcore/fixture.py             # difficulty, Elo, team join
PYTHONPATH=src python src/ffcore/score.py               # the blend + fixture
PYTHONPATH=src python src/ffcore/bid.py                 # premiums, bands, the basket
PYTHONPATH=src python src/digest.py --selftest          # report stitching
PYTHONPATH=src python src/xi.py --selftest              # XI from bench
PYTHONPATH=src python src/slate.py --selftest           # what is on offer
PYTHONPATH=src python src/points.py --selftest          # per-jornada diffs
PYTHONPATH=src python src/methodology.py --selftest     # forecast-vs-actual join
PYTHONPATH=src python src/rivals.py --selftest          # rival XI arithmetic
PYTHONPATH=src python src/report.py --selftest          # the cells that judge
PYTHONPATH=src python src/ffcore/forecast.py            # the sampler + its shape
PYTHONPATH=src python src/ffcore/season.py              # shapes, best XI, standings
PYTHONPATH=src python src/ffcore/render.py              # folded names, made readable
PYTHONPATH=src python src/decide.py --selftest          # candidates, steals, ranking
PYTHONPATH=src python src/sim.py --selftest             # the simulation's report
```

## Design notes

**A round in progress was being paid out twice.** The simulator plays every
jornada that is not finished, and the app's carried points already include the
matches inside that round which *have* been played. On 2026-08-18, four of
jornada 1's ten matches were in — so every manager was credited a second time
for them, and not equally: seven of BurtonGM89's eleven had played, against
three of mine. He was being handed 20.3 phantom points a round to my 7.8.
`decide.rounds_left()` now keeps the round and drops the clubs inside it that
are done. It still lets everybody re-pick an eleven that is in fact already
locked, for one round out of thirty-eight; that one is in Known gaps.

The join that fix needs is the trap underneath it: **one club has three
spellings**. The market says `Rayo`, the fixture page `rayo-vallecano`, and the
probable-XI page files twenty-eight players under the first and one under the
second. Folding case and punctuation is not enough — `rayo` and `rayo
vallecano` are still two strings — so both sides go through `club_key()`,
which resolves against the *market's* list of clubs, the one canonical
spelling this repo has. Matched against the raw pool instead, the slug
resolves to itself, one player is excluded and twenty-eight are not, and the
double count comes back wearing a different name.

**The same season was not the same season in another process.** `simulate()`
promises that one seed is one season, which is what makes two candidate squads
comparable — but `Bootstrap.draw` walked the players in dict order, one rng
feeding the whole round, so the *order* decided which player got which number.
The callers build that dict by iterating a set, and set order over strings
moves with Python's per-process hash seed. Identical data therefore produced a
headline P(win) that drifted a point or two between runs, which is noise a
reader cannot tell from news. The order is sorted once at construction now.

**One join, in one place: `ffcore.league.api_key()`.** The app spells players
its own way — `A. Ferllo` is Álvaro Fernández, `Llorente` is one of two — and
`owner_from_api()` had the only correct resolution: `Market.key_for`, then the
ledger breaking a tie when exactly one candidate is already recorded against
*that* manager, then an exact market value searched across all history.
`decide.py` re-derived a weaker one of its own, and the cost was not
ownership — it was that the **buyout clause is on the row that would not
join**, so five rival players could not be bought at all. Two of them were
SusoGattuso's, and he is the only rival inside the noise. Extracting the
three-step join so both callers use it took the acquirable universe from 75
players to 82.

**Fitness is read from the panel, not from the classes.** `elemento lesionado
elemento_jugador` is the generic class on every tile of the pitch graphic — the
containers are called `jugadores-titulares-22421 mod lesionados`, and Barcelona
alone carries 40 of them. Selecting on it flags the whole squad. The real
signal is the "Estado físico de la plantilla" panel, and the *state* comes from
the icon's alt text (`Lesionado` / `Duda` / `Tocado`), not from any class.
Suspensions live in `section.mod.sancionados`, but that class is reused by a
transfer-listing box holding 214 elements league-wide, none of them suspended;
excluding `.mercado-box` is not optional. `Tocado` — a knock the site still
lists as available — folds into `doubt`, because it drives the same decision.

For the whole life of this repo the `status` column was dead: 14,765 rows, all
`ok`. Re-parsing the retained HTML recovered 1,702 flagged readings across 29
snapshots. Because the fix ran over raw, it repaired the history too. `parse`
now prints the status breakdown every run and warns when nothing at all is
flagged, which is what would have caught this in week one.

**One bench, and it is yours.** The report used to print the model's spare
players and your marked bench under the same word, in the same file, with
different names in each. What you are fielding is a fact; what the model would
field is advice. Question 1 leads with the fact.


**One registry, not one script per source.** `src/sources.py` holds a `Source`
entry per page we fetch — its URL, its parser, its content signature, its
cadence — and nothing else knows what a source is. `src/ingest.py` is the only
code that touches the network or the raw store. Adding a source is one entry,
one parse function and its self-test cases; it is not a new script, a new
workflow input, or a new output file to wire into the report.

That replaced three modules that all scraped the same site — and two of them
fetched the *same* points page the daily sweep already stored, which is how
`data/season/points_2025-26.csv` and `data/season/live/running_2025-26.csv`
came to hold the same 757 players with the same totals. `running_*.csv` is gone
— nothing read it, and `points_total` on every per-jornada row carries what it
held. One copy of a number, or it drifts. They carried four
different number parsers between them. `history.py` also imported `httpx` at
module level, so importing it broke the test job, which installs no network
client on purpose.

**Two probable-XI sources, stored side by side.** Analítica Fantasy is the
second, as twenty registry entries — one per team — plus their fixtures hub,
which cost two parse functions and their fixtures, and that is what the
registry was built for.

**Their team page has two shapes, and the second one is the better source.**
Close to kickoff it server-renders `Titulares <Team>` — the eleven they
predict, a final call with no number attached. The rest of the week it renders
`Consenso de alineaciones` instead: their editors' individual picks, published
as fractions (`2/3 titular`), split into *Unánimes* and *Más divididos*. The
first live sweep found sixteen of twenty pages in the Consenso shape, so
parsing only the first shape would have read as site rot four days out of
five. Both are parsed. A unanimous pick is stored as 100%; a divided one keeps
its own fraction; a `Titulares` row is stored with `start_pct` empty and
`role="starter"`, because a yes is not a percentage and turning it into one
would mean inventing the constant that converts them.

The same page carries `Candidato a capitán`, a *third* list nested as a
sibling of the first two. Walking up more than one ancestor to find the block
heading filed it under *Unánimes* and stored `Nico Williams1/3` as a certain
starter. `_af_section()` reads the immediate parent only, a fraction in the
name is a second guard, and the fixture in `sources.py` reproduces that exact
nesting.

No fitness panel at all, on either shape: `status` is `""`, meaning *not
stated*, never `ok` — a page that says nothing about fitness must not be
stored as a clean bill of health. Their position codes are not stored either:
the app's own positions are the ones the scorer uses.

**The report prints both, beside each other, and blends nothing.**
`LINEUP_SOURCE` stays `futbolfantasy` — it is what the scorer reads — and
question 1 gained an `AF` column next to `FF`. Question 3 is now exceptions
only: who is under the threshold, and the players where the two sources
contradict each other. Neither source has been checked against a played
jornada, so there is no weight to blend them by, and a disagreement is a
prompt to open the app rather than an average to take. Joins go by name —
exact, then `resolve()` — because neither site publishes the app's slug. Of
229 names measured, 151 joined exactly, 59 fuzzily and 19 not at all, and the
19 split three ways worth keeping distinct: a different spelling of the same
player (`Vini Jr.`), a genuinely ambiguous surname (`Simeone` — Diego and
Giuliano), and a player absent from the app's market. An unjoined name is
reported with its candidates and carries no cell. Never guessed.

`robots.txt` allows `/equipo/`; it disallows `/api/`, which is the reason this
reads the rendered page rather than hunting their endpoint. One request per
team per day, at the same 1.5–3s spacing. Verified against a live page: two
fetches forty-three bytes apart signed identically, so the deduplication holds
here as it does for futbolfantasy.

**Both team sweeps dropped to once a day.** Forty requests a day used to be
twenty futbolfantasy pages twice; it is now twenty of each, once — the same
budget answering more. `start_pct` moved for 22 of 511 players across a
fortnight of twice-daily reads, so the second daily sweep was buying almost
nothing. Market and points still run every sweep; those move daily and are two
requests. The fixtures hub is the forty-first page, once a day. Verified live:
the second sweep of the evening made **three** requests, not forty-three —
forty pages were "not due", which is the cadence doing its job.

**The lineups table names its source, and readers get exactly one.**
`probable_xi.csv` is now `lineups.csv` with a `source` column, stamped by the
parse function itself so the label cannot drift from the parser that wrote the
row. A second probable-XI site therefore lands *alongside* the first rather
than instead of it, and both are kept.

Which one the reports use is `ffcore.tidy.LINEUP_SOURCE`, enforced inside
`load_lineups()` rather than by each caller, because a reader that forgot the
filter would silently get one player twice with two different start
percentages — resolved by whichever row the file happened to list first.
**Nothing is blended.** Two sites that disagree about a starter are a fact
worth measuring against what actually happened, not an average to take.
`load_lineups(source="")` reads every row, which is the one job that wants
them all: comparing sources.

**And now they are compared, against a gate that can change the default.**
`reports/methodology.md` scores every claim either site made about a player who
then did or did not appear, by Brier score, off the appearance ground truth
`points.py` already produces — a player absent from a played interval did not
play, so no extra scrape was needed for it. Only claims logged strictly before
the interval count. Whichever source has the lower Brier over a real sample
earns `LINEUP_SOURCE`; until one does, `futbolfantasy` keeps it because it was
first, and the report says so rather than implying it won something. Two limits
are stated in the table's own footnote: it grades P(appear), not P(start) — a
twenty-minute substitute counts, which flatters both sites equally — and an
interval can span two jornadas.

The reshape rewrote all 14,823 rows anyway, so the CRLF/LF split ended here
too — `ingest._write_csv` now writes LF like `ffcore.tidy.write_csv`. Verified
row-for-row against the old file: same 14,823 rows, identical but for the new
column.

**One named view over the tidy CSV, not a glob and thirty guesses.**
`src/common.py` is gone. Its `load_players()` scanned *every* CSV in
`data/tidy` and mapped columns by trying thirty candidate header names, so it
could not tell you which file a field came from, and a renamed column simply
went missing rather than failing. `ffcore.tidy.load_players()` names the
columns it reads, per source, and reads the newest snapshot of each.

That last part is a real behaviour change, and it is a fix. The old merge took
the newest non-empty value *per field across all snapshots*, so a player who
had left the market stayed in the index forever on his last recorded value,
and — worse — anyone missing from the latest XI read kept a stale `start`
indefinitely. Measured against the 29 stored snapshots, the two agree on all
655 current players and differ only by five departed ones, none of which
reached any report. `fmt_money` moved to `ffcore/parse.py` next to the parser
it inverts, which absorbed the byte-identical copy `rivals.py` was carrying;
`rivals.pct` stays local because it prints a signed drift, not a level.

**Raw HTML is kept forever; parsed CSV is disposable.** Scrapers rot. When the
markup changes, fix the parser and re-run over all history. Keeping only the
CSV would lose that option.

**But "forever" had to be made affordable, and it is stored deduplicated.**
The first 29 snapshots were 638 pages and 60 MB, projecting to ~4.4 GB across a
season — past the point GitHub blocks a push, so the original policy did not
survive the season. Every one of those 638 files was byte-distinct, because the
pages carry ad ids, cache-busters and forum usernames, so plain deduplication
saves nothing.

A page is now stored only when its **input-surface signature** changes: a hash
of every string the parsers can reach — text, `href`, `img alt` — and nothing
else. That drops 59% of fetches. Hashing whole pages drops only 32%, because a
news ticker moves constantly. Hashing just the fields we extract today drops
87%, and is wrong: it silently breaks the promise above, since the page that
first carried a field we hadn't extracted yet would be thrown away for looking
unchanged. 59% is the honest middle. When the selectors match *nothing* the
signature is `None` and the page is stored unconditionally with a warning —
that is the selector-rot case, and the one time deduplication must not happen.

Each sweep is then one xz-compressed tar rather than a directory of gzips,
which halves the remainder twice over: xz beats gzip about 2:1, and a solid
archive sees across twenty team pages that share nearly all their boilerplate.
`data/raw` went 60 MB → 8.4 MB and the season projection ~4.4 GB → ~0.2 GB.
Both codecs are stdlib, so the test job still installs only `lxml` and
`cssselect`. You can no longer open a single page in the GitHub web UI; `parse`
reads whole snapshots anyway.

**The manifest, not the file listing, says what a snapshot observed.** Every
archive carries `MANIFEST.csv` naming every page current at that moment, and
`stored` points at the archive whose bytes hold it. A page listed but not
written is carried forward by `parse`, so the tidy CSV has exactly the rows it
always had — verified byte-identical across all 29 snapshots before and after
pruning. The consequence is that **deleting one archive corrupts every later
snapshot that carries a page forward from it.** This store was always
append-only; now it matters.

**`.git` does not shrink.** Git keeps every blob it has ever seen, so pruning
only slows future growth. The pages dropped from the working tree are still in
history and nothing is unrecoverable.

**Names are the only join key.** Neither futbolfantasy page exposes player
links — just photo URLs, and photo-less players all share `00.png`. Don't
attempt a slug-based join.

**Cash is an estimate, and says so.** Managers start with a free randomised
squad plus a separate cash balance, so the anchor is the whole budget less every
ledger row. `~` marks an estimate, `—` means the ledger overdraws the budget and
the number would be fiction. Never treat a `—` as "they are broke".

**The hand-typed offers list was removed too.** `inputs/offers.txt` asked you
to copy the app's market slate by hand, and 9 of its 21 names were already
owned. There is no external feed to replace it with: the slate is 12 free
agents drawn at random per league, on a clock set by the hour your league was
created, and every market endpoint is namespaced by private league id.
`reports/watchlist.md` covers the same ground without typing — it ranks
everyone nobody owns, so it cannot go stale.

That paragraph was written when there was no feed. There is one now: the
league's own market endpoint lists every offer, so the slate is neither typed
nor photographed nor inferred. `seen.txt` — the OCR'd middle step — went the
same way as `offers.txt`, for the same reason, one iteration later.

**Roundness never proved anything, and no count of floor wins is hardcoded.**
`rivals.py` used to read a non-round price as "the app's own valuation, so
nobody competed and you could have had him for the same money". That was
inverted: the five exact-priced buys on the ledger at the time went for +1.5%,
+2.6%, +2.6%, +9.2% and +12.7%, and only 0.7% of current market values are
divisible by 10k, so the app almost never hands you a round number in the first
place. A round bid does mean a human typed it — that half stands, as an
observation about how they type — but the premium over the floor measures the
thing directly.

The replacement prose then made the same class of mistake in the other
direction. It asserted that the floor had never won, which was true of the first
ten buys and false by the fifteenth, and it kept printing as a finding.
`Premiums.at_floor` now counts floor purchases on every run and both reports
state the current number. **When the ledger contradicts the prose, the prose is
the bug.** Note also that every row is a bid that *won*: the share of purchases
made at the floor is not the probability a floor bid wins, and nothing here can
measure that.

**The app randomises its own price by about a tenth.** Selling back to the
market does not pay the market value: the twelve priced sales in the ledger span
−9.4% to +9.8% of value at the time, five below and seven above. So a sale
raises the value give or take 10%, and the bench table says so rather than
printing the value as the money you will get. Whether the same randomiser also
bids against you for a free agent is *inferred, not measured* — every deal in
the ledger is a winning bid, so no losing bid has ever been observed.

**Who the counterparty was tells you who the player is.** A player sold by a
manager was in that manager's squad; a player bought from the market was in
nobody's; and a price has to be within a factor of two of the right player's
value. `ffcore/league.py:identify()` applies those three prunes to the
candidates `resolve()` hands back, which is the step the ledger's own notes
record by hand — `price confirms Fabio not Johnny`, where Fabio Cardoso at
925,408 and Johnny Cardoso at 6.31M are told apart by a 949,269 price. It runs
inside the ledger replay, so ownership is as it stood on the date of the row,
and a sale of a player nobody was holding is now a warning instead of a
no-op.

**Bid logging was removed, deliberately.** `inputs/bids.csv` asked you to type
a bid and then come back and type its outcome. Nobody comes back: both rows in
it said `pending` while the ledger already showed one won and one lost. A field
you have to revisit is a field that drifts. Winning bids are captured
automatically — a win *is* a transaction — so the only thing lost is losing
bids, and `ffcore/bid.py` infers premiums from the ledger instead. What losing
bids would buy is a P(win | bid) curve, and a fortnight of deals cannot fit one.
The ceiling half of the question has two free sources instead: `slate_log.csv`
records what was on offer and therefore what went unsold, and the sell-side
spread above measures what the app will pay when nobody else does. Restore
`bids.csv` from git history only when the sample is large enough to fit a curve.

**The forecast now has three terms, and one of them is a guess.** `xPts/j =
shrunk points-per-match x fixture x P(start)`. The shrinkage runs twice: last
season is pulled toward the positional median with `K=8`, and that result
becomes the prior for this season, shrunk the same way with the same K. There
is no switch-over date to pick and no second constant to guess, and with no
match played it collapses exactly to last season's number — which is the state
it is in today. The fixture term ranks the twenty squads by total market value
and maps the rank onto +/-12%, plus 4% for home. It is a RANK, not a ratio,
because the valuations are convex: Real Madrid's squad is 4.61x the median and
Elche's 0.46x, so a ratio would swing a forecast threefold and claim that
facing Real Madrid costs a defender four fifths of his points. The 12% and the
4% are **not fitted** — nothing has been played, so there is nothing to fit
them to. They are small enough that a wrong guess costs a fraction of a point,
`data/decisions/squad_log.csv` records the factor behind every score, and
`reports/methodology.md` buckets realised points by fixture difficulty so the
band can be widened, narrowed or deleted on evidence.

**The rank now comes from Club Elo, when it covers the league.** `api.clubelo.com`
publishes plain CSV, no key and no terms beyond "read it with a program", so one
request a day joins a result-based rating onto the twenty clubs. A wallet is a
transfer market's opinion; Elo is what the teams have actually done to each
other. **Partial coverage is refused**: one club that will not join sends the
whole board back to squad value, because half a league ranked by Elo and half by
wallet is not a ranking and the mixture would be silently wrong in the middle of
the table where most teams live. Which scale ran is printed in
`reports/methodology.md`, and the raw Elo gap is logged beside the factor on
every row — the +/-12% band is a guess, and re-fitting it later means having
kept the continuous rating rather than the rank it was flattened into.

**One currency: everything is priced in points above replacement per million.**
`ffcore.bid.basket()` spends idle cash down TODAY'S SLATE, best rate first, and
the line is the worst rate it could fund. Anything worse than that is worse
than what the same money would do elsewhere, so buying it is a loss **even when
the XI gain is positive** — which was the old rule, `gain > 0`, buying any
upgrade at any price. Selling is the same test read backwards: hold a player
only while what he adds beats what his proceeds would buy, which is why there
is no second threshold to tune.

Three things make this safe on an uncalibrated index: it is a RATIO, so the
index's arbitrary scale cancels; nothing multiplies the index by a number of
jornadas, which would be a fiction with a unit on it; and replacement level is
fixed by the rules, so two purchases do not compete for the same slot and the
walk is arithmetic rather than a search.

Every run appends the rate it judged with to `data/decisions/line_log.csv` so
the rule itself can be graded. `lambda_log.csv` sits beside it, frozen on the
day λ was retired, holding what the old rule would have said.

**Fielding is one round; buying is months.** So the fixture is inside the
fielding number and outside the buying one — and outside λ, and outside the
sell test. Question 1's purchase rows show
what a signing adds to the whole eleven with every fixture set to neutral, and
mark a kind or unkind next draw with a symbol instead of folding it into the
figure. The same table carries both, in the same columns, because "would this
player get into my team" is one question and it used to take two tables to
answer.

**Empty results say why.** A silently-blank probable XI would set every start
probability to zero and quietly bench your best players, so each section
explains itself when it has nothing.

**Scripts prefer `inputs/<file>` but fall back to the repo root**, so a
partial move doesn't break a run.

**Be a good citizen.** One sweep per run, 1.5–3s between requests, aborts on
403/429.

**The league's own API, and the one secret this repo has.** Four endpoints
behind LaLiga's Azure B2C tenant, reached with the account's own credential:
the market as dealt (41 rows the day it landed — 13 free agents the app deals
plus 28 players listed by managers, where the OCR slate saw a screenshot's
worth), the activity feed, every squad, and the balances. `ffcore/auth.py` is
the only module that holds a credential, and the token file lives outside the
working tree at `~/.config/liga_five_guys/token.json`, 0600.

**The refresh token rotates on every use**, so the write is atomic — temp file,
fsync, rename — and happens *before* the caller is handed anything. A
half-written token file is indistinguishable from no token file and costs an
interactive browser login. A refresh response that carries no new refresh token
is refused rather than persisted, because the old one may still be good.

Getting the first token needs a human and a browser, once per 90 days:

    python -m ffcore.auth --login     # prints a URL; sign in, paste back
    python -m ffcore.auth --status    # days left

**The registry needed one new concept and no new scripts.** `Source` gained
`auth: bool`. The entry declares *that* it needs the bearer; `ingest.py` knows
*how*, because `sources.py` is pure by design and a credential means a file to
read and a token to refresh. The bearer is a per-request header, never
client-wide — sending it to futbolfantasy would hand a third party the
credential to the league account, and there is a self-test asserting no public
source is marked `auth`.

**Discovery, not configuration.** Only the leagues endpoint is in the registry.
It yields a league id, and `league_sources()` turns that into the market, squad
and activity entries — exactly as the calendar turns into 380 match pages. So
there is no league id to paste into `league.ini` and none to go stale.

**Three joins, in falling order of trust.** The API names players its own way
and the rest of the repo keys on the market's spelling, so every API row is
joined through `Market.key_for`. When that is ambiguous — the app writes
"Cardoso" and the market has a Fabio and a Johnny — the ledger breaks the tie
if exactly one candidate is already recorded against that same manager. When
the name shares nothing at all — "A. Ferllo" for Álvaro Fernández, "Jonny Otto"
for Jonny Castro — an **exact market value** settles it, because
futbolfantasy's values match the app to the euro. Exact, with no tolerance, and
searched across all of history rather than the newest snapshot: the two feeds
are swept on different cadences, so within hours the app's figure is one the
market has already moved on from. That last detail made the join work for half
an hour and then stop.

**Half the feed's players cannot be named by anything else.** The activity feed
gives a player id and no name, and 24 of 50 ids belonged to players since sold
— in no squad and on no market page. Each is fetched once, ever, with the same
`"once"` cadence the match pages use, into `api_players.csv`. 50 requests on
the first sweep, none on the next.

**The app's balance is NOW.** It is an anchor like any in `cash.txt`, but
current, so nothing before the moment of the sweep may be applied to it. It is
stamped with the whole moment and parsed as UTC — truncating it to a date made
every deal later the same day subtract from a number that already counted it,
and reported 23.60M as 41.92M. Wrong in the generous direction, which is the
dangerous one for a thing that tells you what you can bid.

**Ownership from the app supersedes the replay, and the disagreement is
printed.** `replay()` still runs — it is what produces the prices and premiums
— but its ownership is a season's typed rows accumulated over a starting
roster, and the app simply states the answer. Where the two differ, the report
says so. An **empty** feed changes nothing at all: a token that expires
mid-season must degrade to the ledger, never announce that nobody owns anybody.
Without a market loaded the override is skipped entirely, because a
differently-keyed ownership map is worse than none.

## Known gaps

- **No outcome data yet.** 2026-27 has not kicked off, so no prediction can be
  scored against reality and every model here is unvalidated. The machinery to
  do it is in place and idle: `xi.py` logs the XI you fielded, `points.py`
  turns each snapshot pair into per-jornada points, and `methodology.py` joins
  them to the prediction logged *before* the matches. Until the first jornada
  lands, the fixture band and the two probable-XI sources are all ungraded.
- **`start_pct` is an editorial bucket**, not a live probability — it moved for
  only 22 of 511 players across the snapshots taken so far. That is true of
  both sources, so two of them agreeing is two editors agreeing, not evidence.
- **Rivals' cash is still an estimate.** The API states `teamMoney` for the
  account that asks and `null` for everyone else, so the `~` stays.
- **A 200,000 gap in the cash arithmetic, unexplained.** Rebuilding the balance
  from the feed lands 200,000 under what the app reports — exactly round, which
  smells like an app credit rather than a deal. It is 0.8% and changes no
  decision, but it means "rebuild cash purely from the feed" is not yet
  provably exact, which is why the app's own figure is the anchor.
- **The feed cannot name a counterparty.** Every row names one manager, and a
  manager-to-manager transfer is not a paired buy and sell — checked across all
  57 rows. So the derived ledger writes the pool as one side of every deal.
  Exact for ownership, prices and premiums; lossy only for who dealt with whom.
- **A round in progress re-picks an eleven that is already locked.** The
  clubs that have played are excluded from it, so their points are not counted
  twice — but the simulator still chooses the best eleven from whoever is
  left, when in reality the lineup for that round was locked before kickoff.
  It flatters everybody, for one round out of thirty-eight, and fixing it
  needs the fielded XI for the round rather than the squad.
- **The simulation cannot value cash, so it cannot value a sale.** Nothing
  models next cycle's market, so holding money scores zero and a standalone
  sale can never come out ahead. Every option it ranks is therefore a move
  that spends, and the ones that raise money are undervalued by exactly the
  amount nobody has measured.
- **Publishing to the phone is wired but unproven.** `lfg-publish` targets a
  private directory, deliberately not the public `/writing` path, and the phone
  was asleep when it was written. The phone-side route that serves it is not
  built yet.

## Roadmap

- [x] **Phase 0 — collect.** Twice-daily snapshots. The only irreversible part.
- [x] **Phase 0.5 — report.** Squad, momentum, cheap likely starters, rivals.
- [ ] **Phase 1 — decide.** Expected points from probable XI + shrunk points
      mean; best-XI recommendation. Needs a few jornadas played.
- [ ] **Phase 2 — value.** Odds → team λ, points decomposition, price model.
- [ ] **Phase 3 — optimise.** Multi-week planning, reservation-price bidding.
      The design is `docs/design.md` §6. `src/optimise.py` held an unwired
      skeleton and was deleted — it was never imported, its position quotas
      were assumed rather than checked, and it had no coach (`entrenador`)
      slot. Recover it from git history when Phase 1 actually produces
      expected points for it to consume.

Phases 2+ can't be validated until ~8 jornadas exist to backtest against.

## The official API — reached, 2026-08-18

It was written off here as "unreachable: no browser session means no token, on
any device". That was true of `fantasy.laliga.com`, which is a download splash,
and it does not generalise. **The token does not come from the app; it comes
from the B2C tenant behind it**, which serves an ordinary sign-in page to any
browser.

Two findings undid the blocker:

- **Facebook is a registered identity provider** on the tenant, alongside
  Google, Apple, Twitter, Twitch, Amazon and Instagram. The concern that the
  account was federated past reach came from LaLigaApp's own UI advertising
  Google and email/password only — a statement about their app, not the tenant.
- **`https://jwt.ms` is a registered redirect URI** for the mobile client.
  Every other value returns `AADB2C90006`, and the error is itself delivered to
  jwt.ms, which is how you can tell. So the one interactive login is a URL
  pasted into any browser: sign in, land on jwt.ms, copy the `code` out of the
  address bar. No app to build, no `authredirect://` scheme to register, and
  the box's 2GB of free RAM stops being a constraint.

| | |
|---|---|
| Tenant | `laligadspprob2c.onmicrosoft.com` on `login.laliga.es` |
| Policy | `B2C_1A_5ULAIP_PARAMETRIZED_SIGNIN` |
| client_id | `af88bcff-1157-40a0-b579-030728aacf0b` (public, no secret) |
| Base path | `https://fantasy-api.llt-services.com/api` |
| Tokens | access 24h, refresh **90 days**, rotates on every use |

**The `/api` prefix is not decoration.** Without it every path 404s with a
`{"code","message"}` body that looks exactly like a permissions failure and
sends you back to re-check a token that was fine.

Constants and the flow live in `ffcore/auth.py`; `docs/design.md` §3 keeps the
older recipe. futbolfantasy's values still match the app to the euro, so the
API is not needed for pricing — it is needed for the things no page publishes,
and for one of them, the exact join key.
