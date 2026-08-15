# liga_five_guys

Data-driven decision system for LaLiga Fantasy Oficial. Private league of 5
managers. Runs entirely on GitHub Actions — no local machine, phone only.

Personal use only. Don't redistribute the scraped data.

## The one button

**Actions → report → Run workflow.** That is the whole interface. It fetches a
snapshot, parses it, runs every generator in dependency order, and stitches the
output into **`reports/REPORT.md`** — the only file you need to read.

That report answers four questions, in this order, each in one table:

1. **Am I fielding the right eleven?** Your marked XI first, the model's
   swaps second.
2. **Is anyone injured, suspended, or doubtful?** Every squad member, with an
   explicit *no data* state — silence never reads as fitness.
3. **Is everyone expected to start?** Start probability for every mark.
4. **Anything to do in the market?** The slate, priced, with who can compete.

Everything else is reference and is **linked**, not reprinted.

It also runs itself twice a day (22:40 and 09:40 UTC), so most of the time
there is nothing to press at all. The run summary shows what needs a decision,
the rival cash table, and any warnings, so you can read the important part in
the GitHub mobile app without opening a file.

Three optional inputs, all off by default:

| Input | When to use it |
|---|---|
| `fetch` | On by default. Turn **off** to rebuild reports from stored HTML without hitting the site. |
| `history` | Once a season. Refreshes the season points baseline. |
| `lookup` | Paste comma-separated names to resolve app spellings to CSV names. |
| `seen` | Paste today's market slate. The report then leads with those players, priced — see below. |

`api probe` is the only other workflow. It is a manual spike against the
official LaLiga API, which is unreachable (see below) — it produces no report
and nothing depends on it.

## What you edit

Four files under `inputs/`. Everything else is generated.

| File | What goes in it |
|---|---|
| `transactions.csv` | Append a row for every buy and sell, yours and theirs. This is the source of truth for who owns whom. |
| `lineup.txt` | **The marks, never the names.** `[x]` fielded, `[ ]` benched. Regenerated from the ledger every run; your marks survive, sold players vanish, and anyone you just bought arrives benched. |
| `cash.txt` | Any balance you actually observe. One rival balance turns their whole cash estimate from an estimate into arithmetic. |
| `rosters_initial.txt` | The starting rosters. Write once, never edit. |

`deadline.txt` (one line, each jornada) and `league.ini` (thresholds and the
starting budget) are the two you touch occasionally. `lookup.txt` and
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
slate each cycle, so most of it isn't buyable today. To close that gap:
long-press the market screenshot, **Copy Text** (iOS Live Text), and paste it
into the `seen` input when you run the workflow.

**Paste a slate and the slate becomes the report.** `reports/REPORT.md` then
opens with one table covering only the players on offer, sorted by what each
one is worth to your eleven:

| Column | What it tells you |
|---|---|
| XI gain | Change in the XI ranking index from owning him, after re-picking the formation. **Frequently negative** for a player the watchlist ranks highly — your own eleven is the benchmark, not the league. |
| Bid | The floor (= market value) plus the premium this league has actually paid over it. Capped by your recorded cash. |
| Competition | Which rivals are structurally short in his position, so who you are bidding against. |
| Verdict | `Bid` if he improves the XI, `pass` if he doesn't, `No` if you can't reach the floor. |

The watchlist collapses to the same players, unfiltered — no start-probability
or budget cuts, because a 40%-start player on today's slate is still a choice
you are making — and `behaviour.md` §5 restricts its demand forecast to them.

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

## Routine

- **Most days:** open `reports/REPORT.md`. Usually nothing to do.
- **Thursday/Friday:** probable XIs firm up. This is when the report earns its
  keep and when to spend.
- **After any deal:** add the row to `inputs/transactions.csv`. Everything —
  ownership, cash, premiums — is replayed from that file.
- **Whenever a rival mentions a balance:** put it in `inputs/cash.txt`.

## Layout

```
src/                 ff_ingest.py (scrape+parse)  squads.py  report.py
                     rivals.py  watch.py
                     digest.py (stitches REPORT.md)  find_slug.py
                     history.py  fantasy_api.py  optimise.py (unused — Phase 3)
src/ffcore/          shared core: parse (numbers)  text (names)  tidy (IO+time)
                     league (ownership+cash)  score (ratings+XI)
                     bid (premiums, bid bands, XI gain)
inputs/              you edit these — see above
data/raw/dt=…/       gzipped HTML — immutable, never edit or delete
data/tidy/*.csv      parsed output — disposable, rebuilt from raw
data/decisions/      append-only logs of estimates, for scoring later
reports/REPORT.md    ← read this
reports/latest.md    the four questions (report.py) — carried into REPORT.md
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
python src/ffcore/parse.py                      # number parsing
python src/ff_ingest.py --selftest              # fitness + market parsing
PYTHONPATH=src python src/ffcore/league.py --selftest   # config + cash
PYTHONPATH=src python src/ffcore/bid.py                 # premiums, bands, XI gain
PYTHONPATH=src python src/digest.py --selftest          # report stitching
PYTHONPATH=src python src/xi.py --selftest              # XI from bench
PYTHONPATH=src python src/seen.py --selftest            # OCR name matching
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


**Raw HTML is kept forever; parsed CSV is disposable.** Scrapers rot. When the
markup changes, fix the parser and re-run over all history. Keeping only the
CSV would lose that option.

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

**Empty results say why.** A silently-blank probable XI would set every start
probability to zero and quietly bench your best players, so each section
explains itself when it has nothing.

**Scripts prefer `inputs/<file>` but fall back to the repo root**, so a
partial move doesn't break a run.

**Be a good citizen.** One sweep per run, 1.5–3s between requests, aborts on
403/429.

## Known gaps

- **No outcome data yet.** Nothing records which XI you actually fielded or
  what it scored, so no prediction can be scored against reality. Until that
  exists, every model here is unvalidated.
- **`start_pct` is an editorial bucket**, not a live probability — it moved for
  only 22 of 511 players across the snapshots taken so far.

## Roadmap

- [x] **Phase 0 — collect.** Twice-daily snapshots. The only irreversible part.
- [x] **Phase 0.5 — report.** Squad, momentum, cheap likely starters, rivals.
- [ ] **Phase 1 — decide.** Expected points from probable XI + shrunk points
      mean; best-XI recommendation. Needs a few jornadas played.
- [ ] **Phase 2 — value.** Odds → team λ, points decomposition, price model.
- [ ] **Phase 3 — optimise.** Multi-week planning, reservation-price bidding.
      `src/optimise.py` is the skeleton; it needs a coach (`entrenador`) slot.

Phases 2+ can't be validated until ~8 jornadas exist to backtest against.

## The official API

Unreachable. There is no web version of LaLiga Fantasy — `fantasy.laliga.com`
is a download splash and `miliga.laliga.com` is a different product. No
browser session means no token, on any device. `docs/design.md` §3 documents
the endpoints for if that ever changes; futbolfantasy's values match the app
to the euro, so the API isn't needed for pricing anyway.
