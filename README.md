# liga_five_guys

Data-driven decision system for LaLiga Fantasy Oficial. Private league of 5
managers. Runs entirely on GitHub Actions — no local machine, phone only.

Personal use only. Don't redistribute the scraped data.

## The one button

**Actions → report → Run workflow.** That is the whole interface. It fetches a
snapshot, parses it, runs every generator in dependency order, and stitches the
output into **`reports/REPORT.md`** — the only file you need to read.

**ONE NUMBER RUNS IT.** λ is the exchange rate between XI points and cash —
index points per million euros — measured every run by spending your actual
balance down the unowned pool, best rate first. It is printed in the header and
every table is priced against it: buy above it, sell below it. Before λ the buy
rule was "does he improve the eleven", which bought any upgrade at any price,
and there was no sell rule at all.

That report is five tables, in the order the decisions get made:

1. **Field these eleven.** Your marks first, then who on today's slate would
   improve them, in the same columns.
2. **Buy today.** The slate priced in pts/M, against the hurdle, with who else
   can compete for him.
3. **What you give up by spending now.** The ladder λ was measured off — what
   the same million buys if you wait for it.
4. **Sell these.** Your bench, each priced as "what he adds" against "what his
   sale proceeds would buy". Never a sale that leaves you unable to field a
   legal eleven.
5. **Exceptions.** Fitness, with an explicit *no data* state so silence never
   reads as fitness, and the players the two probable-XI sources disagree about.

Everything else is reference and is **linked**, not reprinted.

It also runs itself twice a day (22:40 and 09:40 UTC), so most of the time
there is nothing to press at all. The run summary shows what needs a decision,
the rival cash table, and any warnings, so you can read the important part in
the GitHub mobile app without opening a file.

Three optional inputs, all off by default:

| Input | When to use it |
|---|---|
| `fetch` | On by default. Turn **off** to rebuild reports from stored HTML without hitting the site. |
| `baseline` | Once a season. Refreshes the season points baseline. |
| `lookup` | Paste comma-separated names to resolve app spellings to CSV names. |
| `seen` | Paste today's market slate. The report then leads with those players, priced — see below. |

`report` is the only workflow you press; `commands` runs itself when you comment
on the pinned issue (see [Commands](#commands)). The `api probe` spike and `src/fantasy_api.py`
were deleted: the official API is unreachable (see below), the probe produced no
report, and nothing depended on it. `docs/design.md` §3 keeps the token-flow
recipe for if that ever changes, and git history keeps the code.

## What you edit

Four files under `inputs/`. Everything else is generated.

| File | What goes in it |
|---|---|
| `transactions.csv` | Append a row for every buy and sell, yours and theirs. This is the source of truth for who owns whom. |
| `lineup.txt` | **The marks, never the names.** `[x]` fielded, `[ ]` benched. Regenerated from the ledger every run; your marks survive, sold players vanish, and anyone you just bought arrives benched. |
| `cash.txt` | Any balance you actually observe. One rival balance turns their whole cash estimate from an estimate into arithmetic. |
| `rosters_initial.txt` | The starting rosters. Write once, never edit. |

`league.ini` (thresholds and the starting budget) is the one you touch
occasionally. `deadline.txt` is now a **fallback**: the lock is derived from
the next kickoff in `data/tidy/fixtures.csv`, and the report says which of the
two it used. A typed deadline goes stale silently — the one in the file had
expired and the report was reading it as passed. `lookup.txt` and
`seen.txt` are scratch. `squad.txt` is **generated** — a fallback so
`report.py` still works if the ledger fails to load; don't hand-edit it.

Every file under `inputs/` states in its own header what it is for, who writes
it and who reads it.

**The old `bench.txt` is gone.** It named who was *not* in your XI, which stopped
scaling the moment the bench was four names, and once `lineup.txt` existed the
two could contradict each other — silently, since whichever was read first
won. One file describes your eleven now.

## Reading the slate off your phone

The watchlist ranks everyone nobody owns, but the app only deals a limited
slate each cycle, so most of it isn't buyable today. To close that gap, comment
**`/market`** on the pinned Commands issue with the market screenshots attached
— see [Commands](#commands) below. The older path still works: long-press the
market screenshot, **Copy Text** (iOS Live Text), and paste it into the `seen`
input when you run the workflow.

**Paste a slate and the slate becomes the report.** `reports/REPORT.md` then
opens with one table covering only the players on offer, sorted by what each
one is worth to your eleven:

| Column | What it tells you |
|---|---|
| Bid | The floor (= market value) plus the premium this league has actually paid over it. Capped by your recorded cash. |
| ΔxPts/j | Change in the XI ranking index from owning him, after re-picking the formation. **Frequently negative** for a player the watchlist ranks highly — your own eleven is the benchmark, not the league. |
| pts/M | That change divided by what he costs. The one currency. |
| vs λ | pts/M over the hurdle. Above 1.00× the money is better spent here than waiting; below it you are paying over the going rate. |
| Competition | Which rivals are structurally short in his position, so who you are bidding against. |
| Verdict | `Bid` if he beats the hurdle, `pass` if he doesn't, `Cover` if he doesn't but you cannot field a legal XI in his position without him, `No` if you can't reach the floor. |

The watchlist collapses to the same players, unfiltered — no start-probability
or budget cuts, because a 40%-start player on today's slate is still a choice
you are making — and `rivals.md` §5 restricts its demand forecast to them.

OCR output is expected to be bad and that's fine — `Inigo Ruiz Galarreta`
resolves to `Iñigo Ruiz de Galarreta`. Where the name alone is ambiguous,
ownership settles it: the app deals free agents, so a candidate somebody in the
league already holds is not the one on offer. `Llorente` therefore resolves to
Marcos, because Diego Javier is owned. Every such placement is printed under
"Placed by ownership, not by the name" so you can catch it, since it is only as
good as `transactions.csv` is current.

What it still will never do is guess between candidates the ledger can't
separate: a bare `Dani` with ten Danis unowned is reported under "Names I could
not place" with the candidates, because a wrong player costs real money.

Every slate you paste is appended to `data/decisions/slate_log.csv`. A player
who sat on the slate and never appears in `transactions.csv` is one nobody
would pay the floor for — the closest thing to a losing-bid ceiling that
doesn't require typing anything. Nothing reads it yet; a fortnight of slates is
not a base rate.

**Names only, never prices.** Values are already scraped to the euro, and the
minimum legal bid *is* the market value, so OCR only needs to tell you *who*.
An OCR'd price can silently disagree with a correct one we already hold.

`inputs/seen.txt` is git-ignored and cleared on every run that doesn't paste
one. It is scratch, not state — that is what stops it drifting.

## Commands

The Run-workflow form is the wrong interface for a phone: one line, no images,
and the run summary does not push-notify. So there is a pinned issue titled
**Commands**, and a comment on it runs the pipeline.

**`/market` + screenshots of the market screen.** The images are OCR'd on the
runner (`tesseract -l spa --psm 4`), the names resolved exactly as a pasted
slate is, the report rebuilt, and the bot replies with what it read, what it
could not place, a completeness check (`market: 12/12`), and the priced buy
table. The reply is the notification — the GitHub app pushes comments.

Each shot is cropped to the middle column and inverted before OCR, because the
app is dark-mode only and the two columns either side of the name are the whole
problem: the player photo turns rows into noise tesseract skips, and the FSYP
badge reads as `ssvo` glued to the surname, which then matches nothing. On the
first real slate that lifted the read from 1 name to 17. The crop is fractions
of the image (`shots.CROP`), measured on a 1080x2424 Android capture.

Send as many shots as the slate needs; every capture is a set, so order never
matters and **resending is always safe**. Duplicates collapse after name
resolution, not on the raw text, because two scrolled shots spell the same
player differently and only the resolver knows they are one man. No fetch runs:
the slate is priced against the market snapshot already on file.

Send the market screen and nothing else. Any other screen resolves too — a
squad, a rival's bids — and its players are then priced as if you could buy
them, so a count over 12 is reported as a wrong shot, not as a fuller slate.

Two guards. The workflow ignores any comment not written by the repo owner on
that one issue — a public repo where anyone can type would otherwise let anyone
write the inputs your money is spent from. And the comment body reaches Python
through `env:` only, never through `${{ }}`: interpolation is textual, so a name
carrying a quote breaks the step and one carrying a semicolon runs what follows.

`/xi`, `/standings`, `/deals` and `/undo` are specified in issue #30 and not
built. They land on this same spine: guard, fetch the images, OCR, resolve, run
the chain, reply. `/deals` will be the only gated one — a wrong ledger row
poisons ownership, cash and premiums downstream, so it will propose and wait for
`/ok` rather than commit. It is also the only command that must read a number,
because a price paid above market value exists nowhere else, and that is the
second reason for the gate.

Resending a shot of the Activity feed on a later day must not re-log the deals
already in it, so a proposed row is dropped when `inputs/transactions.csv`
already holds one with the same **date, player, from and to**. The app stamps
every operation to the minute and a player cannot move twice in one minute along
the same edge, so those four identify a deal. Price is deliberately not part of
the key: it is the field OCR gets wrong, and if it were, a misread digit would
append a second copy of a deal already logged instead of showing up as a
disagreement to confirm.

## Routine

- **Most days:** open `reports/REPORT.md`. Usually nothing to do.
- **Market cycle:** comment `/market` on the pinned Commands issue with the
  screenshots. The reply is the priced slate.
- **Thursday/Friday:** probable XIs firm up. This is when the report earns its
  keep and when to spend.
- **After any deal:** add the row to `inputs/transactions.csv`. Everything —
  ownership, cash, premiums — is replayed from that file.
- **Whenever a rival mentions a balance:** put it in `inputs/cash.txt`.

## Layout

```
src/                 sources.py (the registry: futbolfantasy, Analítica, Club Elo)
                     ingest.py (fetch, parse, prune — the only network code)
                     squads.py  report.py  rivals.py  watch.py
                     digest.py (stitches REPORT.md)  find_slug.py
                     points.py  xi.py  seen.py  methodology.py
                     shots.py (screenshots in a comment → the slate)
src/ffcore/          shared core: parse (numbers)  text (names)  tidy (IO+time)
                     league (ownership+cash)  score (ratings+XI)
                     fixture (next opponent, difficulty)
                     bid (λ, premiums, bid bands, XI gain, the sell test)
inputs/              you edit these — see above
data/raw/dt=….tar.xz  raw HTML, deduplicated — append-only, never delete
data/tidy/market.csv  values, disposable — rebuilt from raw every run
data/tidy/lineups.csv probable XI + fitness, one row per player per source
data/tidy/fixtures.csv kickoffs, as published — the deadline is derived here
data/tidy/elo.csv     Club Elo ratings, Spanish top flight — the fixture rank
data/decisions/      append-only logs of estimates, for scoring later
reports/REPORT.md    ← read this
reports/latest.md    the five tables (report.py) — carried into REPORT.md
reports/rivals.md    how rivals bid: premiums, drift, projected XIs (rivals.py)
reports/squads.md    every squad, deal history, cash basis (squads.py)
reports/watchlist.md everyone unowned, ranked (squads.py)
reports/methodology.md  the formula and how it is tracking (methodology.py)
docs/design.md       architecture, data sources, modelling plan
```

## Tests

No test directory and no pytest. Each module self-tests under
`if __name__ == "__main__"`, and the `report` workflow runs them on every push
to `src/`:

```
python src/ffcore/parse.py                      # number parsing + formatting
PYTHONPATH=src python src/ffcore/tidy.py        # the player view over tidy CSV
PYTHONPATH=src python src/sources.py            # parsers + signatures
PYTHONPATH=src python src/ingest.py --selftest   # archives + carry-forward
PYTHONPATH=src python src/ffcore/league.py --selftest   # config + cash
PYTHONPATH=src python src/ffcore/fixture.py             # difficulty, Elo, team join
PYTHONPATH=src python src/ffcore/score.py               # the blend + fixture
PYTHONPATH=src python src/ffcore/bid.py                 # λ, premiums, bands, sell test
PYTHONPATH=src python src/digest.py --selftest          # report stitching
PYTHONPATH=src python src/xi.py --selftest              # XI from bench
PYTHONPATH=src python src/seen.py --selftest            # OCR name matching
PYTHONPATH=src python src/shots.py --selftest           # comment → slate
PYTHONPATH=src python src/points.py --selftest          # per-jornada diffs
PYTHONPATH=src python src/methodology.py --selftest     # forecast-vs-actual join
PYTHONPATH=src python src/rivals.py --selftest          # rival XI arithmetic
PYTHONPATH=src python src/report.py --selftest          # the cells that judge
```

## Design notes

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
everyone the ledger says nobody owns, so it cannot go stale. `inputs/seen.txt`
is not that mistake returning: it is OCR'd rather than typed, it is names only,
and it is deleted on every run that doesn't paste one, so it can never be
mistaken for state.

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

**One currency: everything is priced in ΔxPts/j per million.** λ is measured,
not configured. `ffcore.bid.frontier()` spends your recorded balance down the
unowned pool, best rate first, recomputing each gain against the eleven as it
stands after the purchase above it, and λ is the rate of the last rung it could
afford. Anything worse than that is worse than what the same money would do
elsewhere, so buying it is a loss **even when the XI gain is positive** — which
was the old rule, `gain > 0`, buying any upgrade at any price. Selling is the
same test read backwards: hold a player only while what he adds beats what his
proceeds would buy, which is why there is no second threshold to tune. Three
things make this safe on an uncalibrated index: it is a RATIO, so the index's
arbitrary scale cancels; nothing multiplies the index by a number of jornadas,
which would be a fiction with a unit on it; and the ladder is short by
construction, because an eleven has eleven slots so at most eleven purchases
can improve it. λ is a RESERVATION rate — the app deals twelve random free
agents a cycle, so the ladder is what you would buy if you could buy anything,
which biases it in the direction that says *wait*. The ladder is printed in
question 3 so that bias can be seen instead of argued about, `lambda_buffer` in
`league.ini` is the one haircut on it, and every run appends the rate it judged
with to `data/decisions/lambda_log.csv` so the rule itself can be graded.

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

## The official API

Unreachable. There is no web version of LaLiga Fantasy — `fantasy.laliga.com`
is a download splash and `miliga.laliga.com` is a different product. No
browser session means no token, on any device. `docs/design.md` §3 documents
the endpoints for if that ever changes; futbolfantasy's values match the app
to the euro, so the API isn't needed for pricing anyway.
