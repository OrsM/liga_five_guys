# liga_five_guys

Data-driven decision system for LaLiga Fantasy Oficial. Private league
"Some Guys", 3 managers. Runs entirely on GitHub Actions — no local machine.

Personal use only. Don't redistribute the scraped data.

## Layout

```
src/                 ff_ingest.py  report.py  offers.py  bids.py
                     find_slug.py  optimise.py (unused — Phase 3)
inputs/              squad.txt  offers.txt  lookup.txt  bids.csv   ← you edit these
data/raw/dt=…/       gzipped HTML — immutable, never edit or delete
data/tidy/*.csv      parsed output — disposable, rebuilt from raw
reports/*.md         generated; read these on your phone
docs/design.md       architecture, data sources, modelling plan
HANDOFF.md           current state + prompt for a fresh session
```

## Workflows

| Workflow | When | Does |
|---|---|---|
| **daily snapshot** | 22:40 + 09:40 UTC, or manual | Fetch → parse → report → bid log → commit |
| **lookup players** | manual | Resolve app names to CSV names |
| **offers** | manual | Rank what's currently purchasable |

## Routine

- **Most days:** glance at `reports/latest.md`. Usually nothing to do.
- **Thursday/Friday:** probable XIs firm up. This is when the report earns its
  keep and when to spend.
- **Before bidding:** type what's on offer into `inputs/offers.txt`, run
  *offers*, read `reports/offers.md`.
- **After bidding:** add a row to `inputs/bids.csv`; set `outcome` when it
  resolves. Record losses too — they're what reveal rivals' premiums.
- **After buying/selling:** update `inputs/squad.txt`.

## Design notes

**Raw HTML is kept forever; parsed CSV is disposable.** Scrapers rot. When the
markup changes, fix the parser and re-run over all history. Keeping only the
CSV would lose that option.

**Names are the only join key.** Neither futbolfantasy page exposes player
links — just photo URLs, and photo-less players all share `00.png`. Don't
attempt a slug-based join.

**Empty results say why.** A silently-blank probable XI would set every start
probability to zero and quietly bench your best players, so each section
explains itself when it has nothing.

**Scripts prefer `inputs/<file>` but fall back to the repo root**, so a
partial move doesn't break a run.

**Be a good citizen.** One sweep per run, 1.5–3s between requests, aborts on
403/429.

## Roadmap

- [x] **Phase 0 — collect.** Twice-daily snapshots. The only irreversible part.
- [x] **Phase 0.5 — report.** Squad, momentum, cheap likely starters, bid log.
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
