# liga_five_guys
Data-driven deci# fantasy — LaLiga Fantasy decision system

Private. Personal use only. Do not redistribute the scraped data.

## What's here

| File | Does |
|---|---|
| `ff_ingest.py` | Fetches + parses public futbolfantasy data. **No login.** |
| `optimise.py` | MILP: best XI, squad plan, bid reservation price |
| `llf_client.py` | Official API client. Parked — needs a token we can't get yet. |
| `.github/workflows/snapshot.yml` | Runs the snapshot twice daily on GitHub's machines |
| `data/raw/dt=…/` | Gzipped HTML, immutable. Never edit or delete these. |
| `data/tidy/*.csv` | Parsed output. Disposable — regenerated from raw. |
| `docs/design.md` | Full architecture, data sources, modelling plan |

## Setup (5 minutes, all from a phone)

1. **Create the repo** — GitHub app → New → Private.
2. **Upload** the four files. `snapshot.yml` goes in `.github/workflows/`.
3. **Actions tab → daily snapshot → Run workflow.**
4. Green tick + a new `data/raw/dt=…` folder = you're collecting.

That's it. No secrets, no token, nothing to rotate.

## What it collects

- **Market values** for every player, daily: value, 1-day delta, 1-day %,
  position, team. This is the price series the whole value model needs, and
  it can't be backfilled — every day missed is gone.
- **Probable XIs** for all 20 teams: starter/sub, start %, injury and doubt
  flags. This is the single highest-value input for "who do I field".

## Design notes

**Raw HTML is kept forever; parsed CSV is disposable.** Scrapers rot. When the
markup changes, or you want a field you didn't think to extract, fix `parse`
and re-run over the whole history. If you'd only kept the CSV, that's gone.

**Key on `player_slug`, not name.** Names get re-spelled and accented
differently between pages; the URL slug is stable.

**An empty parse is an error, not an empty result.** A silently-empty probable
XI would set every start probability to zero and quietly bench your best
players. `parse` exits non-zero instead.

**Be a good citizen.** One sweep per run, sequential, 1.5–3s between requests,
and it aborts immediately on 403/429. Someone maintains that site for free.

## Roadmap

- [x] **Phase 0 — collect.** Twice-daily snapshots. The only irreversible bit.
- [ ] **Phase 1 — decide.** "Start these 11" from probable XI + shrunk
      5-jornada mean points. Beats manual play on its own.
- [ ] **Phase 2 — value.** Odds → team λ, points decomposition, price model.
- [ ] **Phase 3 — optimise.** Multi-week planning, reservation-price bidding.

Phases 2+ can't be validated until there are ~8 jornadas to backtest against,
so building them now would be guessing.

## The parked auth path

There is no web version of LaLiga Fantasy any more — `fantasy.laliga.com` is a
download splash, and `miliga.laliga.com` is a different product (MILIGA fan
rewards, `fz-` prefixed keys, no refresh token). So the browser token capture
in `docs/design.md` §3 can't be done at all, on phone or desktop.

Options when there's a desktop to hand: run Externoak/LaLigaApp and read the
session it stores, or put mitmproxy in front of the native app and see whether
it pins certificates. Until then the API only adds your own squad, your cash,
and rival bid history — none of which the model needs.
