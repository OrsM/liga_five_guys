# LaLiga Fantasy Oficial — a data-driven decision system

**Context:** private league, 5 managers, 100M budget, season 26/27 starting J1 on 15/08.
**Goal:** a reproducible pipeline that produces, every day, a ranked list of *buy / sell / bid-how-much / start-who* decisions from expected points and expected price movement — free to run, on hardware you already have.

---

## 0. TL;DR of the recommendation

| Layer | Choice | Why |
|---|---|---|
| Game data | Official API `fantasy-api.llt-services.com` + Azure B2C refresh-token loop | Headless after a one-time browser capture. Same API the app uses. |
| Context data | futbolfantasy.com (onces probables, set pieces, injuries, market curve) | The only free source of *starting probability at decision time*. |
| Underlying stats | FBref / Understat / Sofascore via `soccerdata` (py) or `worldfootballR` (R) | xG, xA, shots, minutes → per-90 rates that survive small samples. |
| Match context | Bookmaker odds (The Odds API free tier) + Club Elo | Decision-time λ_for / λ_against; clean-sheet probability. |
| Storage | Immutable dated JSON snapshots → DuckDB + Parquet | Point-in-time correctness, ~zero RAM, no server process. |
| Orchestration | systemd **user** timers + `uv` venv under `~/.local` | Matches your Asus box constraints (no sudo, 2GB free RAM). |
| Modelling | Two-stage: team match model → minutes model → points model | Same structure as AIrsenal / OpenFPL; interpretable and debuggable. |
| Optimisation | MILP (PuLP + CBC, or `highspy`) | XI selection, multi-week transfer plan, and bid reservation prices. |
| Output | Quarto → static HTML behind your existing cloudflared tunnel | You make decisions from your phone; the report must live there. |

**The single most important design rule:** every artefact is written as an immutable snapshot with the timestamp it was *observed*, never overwritten. Everything downstream joins **as-of the decision moment**. Without this, backtests are fiction — you'll accidentally use Sunday's lineup news to "predict" Saturday.

---

## 1. What game you are actually playing

Three things make this materially different from FPL, and they change what the optimiser should maximise.

**(a) It's a market game, not a salary-cap game.** Player values move daily (the game recomputes at ~00:15 Europe/Madrid) driven by *global* demand across all users of the game — bids, buys, sells — plus recent performance. Your budget is not fixed at 100M: it compounds. A manager who consistently buys pre-rise and sells pre-fall ends February with meaningfully more spending power than one who doesn't. So the objective is not "expected points this week" but:

```
maximise   Σ_t γ^t · E[points_t]    subject to    budget_t evolving with E[Δvalue]
```

and the two terms must be traded off with an explicit **exchange rate**: how many euros is one expected point worth? Get this from the dual variable on the budget constraint in your MILP — don't guess it.

**(b) Only 3 managers.** ~600 eligible players, at most ~75 owned. Scarcity is almost nil. Consequences:

- Differential/variance strategies (core to 10,000-manager FPL leagues) are **wrong here**. With 2 rivals over 38 jornadas, maximising expected points dominates. Only switch to variance-seeking if you're clearly behind after ~J30.
- The binding constraint is budget and *market rotation* (which players are actually offered to you on a given day), not competition for a scarce asset.
- Buyout clauses matter less than in a 10-manager league, but they are not free: with 2 rivals holding large cash piles, a cheap clause on a player who has doubled in value is a real exposure. Track clause-to-value ratio as a risk metric.

**(c) Rival bidding is learnable.** The `/leagues/{id}/activity/{index}` endpoint returns the league's transaction history. With 2 rivals you can build an empirical distribution of *bid premium over market value*, conditioned on position and price band, within ~10 jornadas. That turns bid sizing from a guess into a straightforward expected-surplus calculation.

> **To verify in-app before you trust the optimiser:** (i) whether the lineup locks per-match (at each player's kickoff) or once per jornada; (ii) whether a captain multiplier exists in this format; (iii) bench auto-substitution rules; (iv) the exact penalty for selling at a loss. These four change the constraint set, and I'd rather you confirm them than have me assert them.

---

## 2. Data sources, judged by "is it available at decision time?"

This is the filter that kills most fantasy-football projects. A source that only publishes *after* the fact is fine for training and useless for deciding.

| Source | What you get | At decision time? | Cost | Fragility |
|---|---|---|---|---|
| **Official Fantasy API** | Squads, market offers, all player values + value history, points per jornada, points breakdown, calendar, league activity, your cash | ✅ yes, real-time | free | Medium — endpoints moved hosts for 26/27 |
| **futbolfantasy.com** `/laliga/equipos/{slug}` | Onces probables (probable XI), injuries, suspensions | ✅ 24–48h before | free | HTML scrape, breaks on redesign |
| **futbolfantasy.com** `/analytics/laliga-fantasy/mercado` | Daily risers/fallers, "curve acceleration" indicator | ✅ daily | free | HTML scrape |
| **FBref / Understat** via `soccerdata` | xG, npxG, xA, shots, key passes, minutes, per-90s | ✅ (lagged to last completed match) | free | Rate-limited; cache aggressively |
| **Odds** (The Odds API free tier, ~500 req/mo) | 1X2 + O/U 2.5 for upcoming fixtures | ✅ from ~7 days out | free tier | Quota — 1 call/day for the whole jornada is enough |
| **football-data.co.uk** `SP1.csv` | Historical results + closing odds | ❌ post-hoc only | free | Stable; **use for backtesting only** |
| **Club Elo** via `soccerdata` | Team strength ratings | ✅ | free | Stable |

Deliberately **not** recommended: paid football APIs (unnecessary), Selenium/Playwright browser emulation (heavier, more brittle, and unnecessary once you have the token flow below), and the various "LaLiga Fantasy scraper" repos from 2022–2023 — most are dead, because the API moved.

---

## 3. Access with a Facebook login

The account signs in through Facebook, so it is **federated** into LaLiga's Azure AD B2C tenant. That rules out the password grant (`B2C_1A_ResourceOwnerv2`) — there is no local password for B2C to check. But it does *not* rule out headless automation, because the tokens B2C issues after a Facebook login are ordinary OAuth tokens with an ordinary refresh token.

**The pattern: bootstrap once in a browser, refresh headlessly forever.**

### One-time capture

1. In Firefox, open DevTools (F12) → **Network**, tick **Preserve log**, filter to **Fetch/XHR**.
2. Navigate to `https://miliga.laliga.com/` — the web entry point to the game. **Don't log in yet**; get the DevTools panel open first, or the request you need scrolls past before recording starts.
3. Now log in with Facebook.
4. Filter the network panel for `token?p=B2C_1A_5ULAIP_PARAMETRIZED_SIGNIN`. One request from `login.laliga.es` will match.
5. Open its **Response** tab and copy the whole JSON body — you need `id_token` *and* `refresh_token`, not just the bearer string.
6. Save to a file and run `python llf_client.py bootstrap tok.json`.

### Then it's automatic

```
POST https://login.laliga.es/laligadspprob2c.onmicrosoft.com/oauth2/v2.0/token?p=B2C_1A_5ULAIP_PARAMETRIZED_SIGNIN
grant_type=refresh_token
refresh_token=<stored>
client_id=6457fa17-1224-416a-b21a-ee6ce76e9bc0   # the miliga.laliga.com web client
scope=openid offline_access
```

Two details that will bite you if you miss them:

- **Use the client that issued the token.** A token captured from the web session belongs to the web client `6457fa17-…`. Refreshing it with the mobile client id fails with `invalid_grant`, which looks exactly like an expired session and will send you chasing the wrong bug.
- **B2C rotates refresh tokens.** Each refresh returns a *new* refresh token, and the old one is burned. Persist it every time, and never null it out if a response happens to omit one — `llf_client.py` now keeps the last good value. Get this wrong and you'll be back in DevTools every day instead of every few weeks.

The refresh window is a sliding one measured in weeks, so a job that runs daily keeps the session alive indefinitely. Expect to re-do the browser capture only if the job is down for a long stretch or LaLiga invalidates sessions. Make the daily job fail loudly (a notification, not a silent log line) when the refresh 400s, so you find out on a Tuesday rather than at 21:00 on matchday.

### Two alternatives, if the capture proves awkward

- **Add an email/password credential** to the account in LaLiga's account settings, if it lets you. Federated B2C accounts sometimes can't, but if yours can, the fully headless password grant comes back and `llf_client.py` already supports it via `LLF_EMAIL` / `LLF_PASSWORD`.
- **Re-register with email.** Normally terrible advice, but your league is `-/3` with 0 points and J1 hasn't kicked off — you'd lose nothing but the two minutes it takes to rejoin. Worth considering purely because it removes a recurring manual step from a system you want running unattended for 38 jornadas.

Not recommended: driving a headless Firefox through the Facebook login on a schedule. Facebook aggressively fingerprints automation, you'd be storing Facebook credentials on the Asus box, and a checkpoint challenge would break the job anyway. The refresh-token loop is strictly better.

### API surface (26/27)

Base: `https://fantasy-api.llt-services.com/api`, and most routes gained a competition segment: `/v1/competition/1/...` (1 = LaLiga EA Sports). The 25/26 host `api-fantasy.llt-services.com` with `/v3`, `/v4` routes is frozen — that's why the older GitHub scrapers all fail.

| Purpose | Route |
|---|---|
| Me | `GET /v4/user/me` |
| My leagues | `GET /v1/competition/1/leagues` |
| Standings | `GET …/leagues/{leagueId}/standing[/{week}]` |
| League activity (bids/sales history) | `GET …/leagues/{leagueId}/activity/{index}` |
| A team's squad | `GET …/leagues/{leagueId}/teams/{teamId}` |
| All players | `GET …/players` |
| Player detail (values, points, stats) | `GET …/player/{playerId}/league/{leagueId}` |
| Calendar | `GET …/calendar?weekNumber={n}` |
| Current week | `GET …/week/current` |
| Market offers | `GET …/league/{leagueId}/market` |
| My cash | `GET …/teams/{teamId}/money` |
| Lineup read / write | `GET`/`PUT …/teams/{teamId}/lineup` |
| Place bid | `POST …/league/{leagueId}/market/{marketId}/bid` `{money}` |
| List for sale | `POST …/league/{leagueId}/market/sell` `{playerId, salePrice}` |
| Raise clause | `PUT …/league/{leagueId}/buyout/player` |

The open-source [Externoak/LaLigaApp](https://github.com/Externoak/LaLigaApp) is the best living reference for this surface — it's actively maintained through the 26/27 migration and its comments document which routes changed shape. Read its `src/services/api.js` when something 404s.

**Be a good citizen:** one full player sweep per day, sequential, with a small delay; back off immediately on 429/403; cache everything. You're one user, not a scraping farm — and the ToS permit personal use, not redistribution of the data.

---

## 4. Pipeline architecture

> **Not what was built.** This section describes a local box (`miguelito-asus`),
> DuckDB, Parquet, R/`targets` and systemd timers. None of it exists. What runs
> is one GitHub Actions workflow, dated `tar.xz` snapshots of raw HTML, and CSV
> under `data/tidy/`; there is no local machine in the loop and no database. The
> built layout is in `README.md`, which is the accurate document. Kept here
> because the *reasoning* below — point-in-time correctness, the language split,
> the memory argument — is still what would justify moving off CSV if the data
> ever outgrows it. Read it as a proposal, not a description.

```
~/fantasy/                        # proposed, never built
├── raw/                          # immutable, never rewritten
│   └── dt=2026-08-11T00:20Z/
│       ├── players.json.gz
│       ├── market.json.gz
│       ├── my_team.json.gz
│       ├── activity.json.gz
│       └── onces.html.gz
├── warehouse.duckdb              # views over parquet, not a copy
├── silver/                       # parquet, one file per entity per day
│   ├── player_value_daily/
│   ├── player_points_jornada/
│   └── lineups/                  # built as data/tidy/lineups.csv, one row
│                                 # per source per player, not one dir per source
├── models/                       # fitted objects + backtest metrics
├── R/                            # targets pipeline + Quarto report
└── py/                           # ingest, features, optimiser
```

**Language split** — plays to both your tools and keeps memory low:

- **Python** (`uv` venv) for I/O and optimisation: auth, API client, HTML parsing, MILP. `uv` installs to `~/.local`, no sudo.
- **DuckDB** as the contract between languages. Both `duckdb` (py) and `duckdb` (R) read the same file; no server process, no Postgres, ~50MB RSS.
- **R + `targets` + Quarto** for modelling and the report — which is exactly the pipeline layer you're already standardising on. `targets` gives you the invalidation graph so a re-run after new data only refits what changed; `renv` pins it.

**Scheduling** via systemd user timers, mirroring the `study-repl@`/`study-term@` split you already run:

| Timer | When | Does |
|---|---|---|
| `fantasy-ingest.timer` | 00:20 Europe/Madrid daily | Pull API snapshot right after the value recompute |
| `fantasy-context.timer` | 08:00, 18:00 daily | Onces probables, injuries, odds |
| `fantasy-decide.timer` | 07:00 daily + T-3h before each kickoff | Refit → optimise → render report |

Render to `~/public/fantasy/index.html` and expose it through the existing cloudflared tunnel under Cloudflare Access. Decisions you can't see on your phone at 22:50 on a Friday are decisions you won't make.

**Memory discipline** (this box will OOM if you're careless): aggregate in DuckDB SQL, never `pd.read_parquet` the whole history; fit models on jornada-level aggregates (~600 players × 38 = 23k rows — trivial); avoid `xgboost`'s default thread pool (`n_jobs=2`).

---

## 5. The model

Don't predict fantasy points directly with one big regressor. Decompose — it's more accurate at this sample size, and far easier to debug when a recommendation looks insane.

### 5.1 Team layer → match context

For each fixture, get `λ_for` and `λ_against`:

- **Primary (decision time):** de-vig the 1X2 + Over/Under 2.5 market, solve for the Poisson pair that reproduces the implied probabilities. Bookmakers aggregate injury/rotation/motivation information you cannot easily replicate — this is the highest-value, lowest-effort input in the whole system.
- **Secondary:** Dixon-Coles fit on results with time decay, or Club Elo. Useful as a sanity check and for the preseason cold start.

From λ you get, in closed form, `P(clean sheet)`, `E[goals conceded]`, and the fixture-difficulty term for every attacking player.

### 5.2 Minutes layer → the biggest source of edge

A non-starter scores ~0. In practice this layer beats the fancy points model:

```
P(start)  ← logistic on: futbolfantasy probable XI flag,
                          starts in last 5, minutes trend,
                          injury/suspension status from the API,
                          days since last match (congestion),
                          is the fixture a likely rotation game
E[minutes] = P(start)·E[min|start] + P(bench)·E[min|bench]
```

Calibrate this and check reliability curves. If your P(start)=0.8 bucket actually starts 55% of the time, everything downstream is wrong and no amount of xG modelling saves it.

### 5.3 Player points layer

Model *rates per 90*, then scale by expected minutes:

- **Attacking returns:** player's share of team shots/xG from FBref (shrunk toward position × team priors — James-Stein or a simple hierarchical prior; the small-sample noise early in the season is brutal), × team λ_for → E[goals], E[assists].
- **Set pieces & penalties:** binary flags from futbolfantasy's balón parado page. A penalty taker is worth ~1.5 pts/match more than an identical non-taker. This is cheap, public, and routinely ignored.
- **Defensive/technical points:** the API's own points *breakdown* per jornada is the ideal training target — regress each component on player per-90 rates and opponent context. These components are much more stable than goals, which is why defenders and defensive mids are often the best €/point in this scoring system.
- **Cards:** Poisson on player fouls/90 × referee strictness (futbolfantasy publishes referee data).

Then `E[points] = Σ components`, and keep the **variance** too — you'll want it for the "am I behind, should I gamble" decision late in the season.

### 5.4 Price model

Separate target: `Δvalue_{t+1}`. Features: last-k daily deltas, curve acceleration, last jornada's points, days since last points update, price band, position. Gradient boosting is fine here (it's a smooth, mechanical function of global demand — much more predictable than points). Value it in the objective as expected capital gain over your holding horizon.

### 5.5 Backtesting — non-negotiable

- **Rolling origin by jornada.** Train on J1..J_k, predict J_{k+1}. Never random K-fold.
- **As-of joins only.** Every feature must carry the timestamp it was observed and the join must respect it. This is where your daily snapshot discipline pays off.
- **Metrics that match the decision**, not just MAE:
  - Spearman rank correlation *within* jornada (you're ranking, not forecasting levels)
  - realised points of the optimiser's XI vs. hindsight-optimal XI, as a % gap
  - calibration of P(start)
  - price model: hit rate on sign of Δvalue, and € captured
- **Baselines you must beat:** (1) last-5-jornada mean points; (2) points-per-million ranking; (3) "just play the 11 most expensive players you own". If the ML doesn't beat #3, ship #3 — it's free and it works.

---

## 6. Optimisation

### 6.1 Weekly XI
MILP: binary `x_p` per owned player, maximise `Σ E[points_p]·x_p`, subject to exactly 11 selected, one GK, and formation validity. Small enough that you could enumerate formations, but MILP keeps it uniform with the harder problems.

### 6.2 Multi-week squad planning
Horizon H ≈ 5 jornadas, discount γ ≈ 0.85:

```
max  Σ_t γ^t [ Σ_p E[pts_{p,t}]·start_{p,t} ]  +  λ_€ · Σ_p E[Δvalue_p]·own_p
s.t. budget_t = budget_{t-1} + Σ sells − Σ buys
     squad size / position quotas
     buy_{p,t} ≤ available_{p,t}       # only what the market actually offers
     clause exposure ≤ tolerance
```

`λ_€` is the points-per-euro exchange rate — take it from the budget constraint's shadow price and sanity-check it against "how many points would 1M extra actually buy me over the remaining season".

### 6.3 Bid sizing — where a 3-man league is won

For a market player, your **reservation price** is the marginal value of adding them:

```
R = [ Δ(optimised horizon points from adding p) ] / λ_€  +  E[capital gain over holding period]
```

Then bid `argmax_b (R − b) · P(win at b)`, where `P(win at b)` comes from the empirical rival-bid distribution you learn from `/activity`. Early season, before you have that history, assume rivals bid market value + 0–15% and bid at the top of that band only when `R` comfortably exceeds it.

The discipline this enforces matters more than the precision: it stops you overpaying for a name and stops you missing a bargain by 100k.

---

## 7. Cold start — you have four days

There is no 26/27 fantasy data yet, so J1–J3 run on priors:

1. **Ingest today.** Get the token flow working and start the daily snapshot *now*. Preseason value drift is already informative, and every day you don't snapshot is a day of training data you can never recover.
2. **Priors from 25/26** FBref per-90s + minutes, shrunk hard. For the promoted sides (Racing, Deportivo and Málaga appear in the 26/27 lists) use Segunda per-90s with a division adjustment — roughly a 25–35% haircut on attacking output — and expect wide error bars.
3. **Weight the minutes model heavily** for J1. Preseason friendly lineups and the onces probables are the best signal you have; underlying stats are nearly useless on 0 matches.
4. **Don't spend the full 100M on J1.** Value is at its noisiest and the market rotation will keep offering players. Hold ~20–25% cash for the first fortnight, when mispricings are largest and your model is worst.
5. **M