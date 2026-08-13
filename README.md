# liga_five_guys

Data-driven decision system for LaLiga Fantasy Oficial. Private league of 5
managers. Runs entirely on GitHub Actions — no local machine, phone only.

Personal use only. Don't redistribute the scraped data.

## The one button

**Actions → report → Run workflow.** That is the whole interface. It fetches a
snapshot, parses it, runs every generator in dependency order, and stitches the
output into **`reports/REPORT.md`** — the only file you need to read.

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
| `seen` | Paste today's market slate. Marks which of the watchlist you can actually buy — see below. |

`api probe` is the only other workflow. It is a manual spike against the
official LaLiga API, which is unreachable (see below) — it produces no report
and nothing depends on it.

## What you edit

Four files under `inputs/`. Everything else is generated.

| File | What goes in it |
|---|---|
| `transactions.csv` | Append a row for every buy and sell, yours and theirs. This is the source of truth for who owns whom. |
| `cash.txt` | Any balance you actually observe. One rival balance turns their whole cash estimate from an estimate into arithmetic. |
| `rosters_initial.txt` | The starting rosters. Write once, never edit. |
| `bench.txt` | Who is **not** in your XI. You own 12 and field 11, so this is one name. The XI is derived. |

## Reading the slate off your phone

The watchlist ranks everyone nobody owns, but the app only deals a limited
slate each cycle, so most of it isn't buyable today. To close that gap:
long-press the market screenshot, **Copy Text** (iOS Live Text), and paste it
into the `seen` input when you run the workflow. Those players get a ✅ and
sort to the top of each position.

OCR output is expected to be bad and that's fine — `Inigo Ruiz Galarreta`
resolves to `Iñigo Ruiz de Galarreta`. What it will never do is guess: a bare
`Dani` is reported under "Names I could not place" with the five candidates,
because a wrong player costs real money.

**Names only, never prices.** Values are already scraped to the euro, and the
minimum legal bid *is* the market value, so OCR only needs to tell you *who*.
An OCR'd price can silently disagree with a correct one we already hold.

`inputs/seen.txt` is git-ignored and cleared on every run that doesn't paste
one. It is scratch, not state — that is what stops it drifting.

`league.ini` holds thresholds and the starting budget. `lookup.txt` is a
scratch input for the name resolver.
`squad.txt` is **generated** — a fallback so `report.py` still works if the
ledger fails to load; don't hand-edit it.

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
inputs/              you edit these — see above
data/raw/dt=…/       gzipped HTML — immutable, never edit or delete
data/tidy/*.csv      parsed output — disposable, rebuilt from raw
data/decisions/      append-only logs of estimates, for scoring later
reports/REPORT.md    ← read this
reports/*.md         the individual sections REPORT.md is built from
docs/design.md       architecture, data sources, modelling plan
```

## Tests

No test directory and no pytest. Each module self-tests under
`if __name__ == "__main__"`, and the `report` workflow runs them on every push
to `src/`:

```
python src/ffcore/parse.py                      # number parsing
PYTHONPATH=src python src/ffcore/league.py --selftest   # config + cash
PYTHONPATH=src python src/digest.py --selftest          # report stitching
PYTHONPATH=src python src/xi.py --selftest              # XI from bench
PYTHONPATH=src python src/seen.py --selftest            # OCR name matching
```

## Design notes

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
everyone the ledger says nobody owns, so it cannot go stale.

**Bid logging was removed, deliberately.** `inputs/bids.csv` asked you to type
a bid and then come back and type its outcome. Nobody comes back: both rows in
it said `pending` while the ledger already showed one won and one lost. A field
you have to revisit is a field that drifts. Winning bids are captured
automatically — a win *is* a transaction — so the only thing lost is losing
bids, and `rivals.py` infers rival premiums from the ledger instead. Restore it
from git history if the premium curve ever needs the losses.

**Empty results say why.** A silently-blank probable XI would set every start
probability to zero and quietly bench your best players, so each section
explains itself when it has nothing.

**Scripts prefer `inputs/<file>` but fall back to the repo root**, so a
partial move doesn't break a run.

**Be a good citizen.** One sweep per run, 1.5–3s between requests, aborts on
403/429.

## Known gaps

- **Injury status is never parsed.** Every row in `data/tidy/probable_xi.csv`
  says `status=ok` because `parse_team` reads `.jugador.tipo_lista` classes,
  while injuries live in a separate `elemento lesionado` block it never opens.
  The markup is in the retained HTML, so this is recoverable retroactively.
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
