# Liga Five Guys — how the numbers are made — 2026-09-24 07:38 CEST




## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| Jornada 6 is half played — 18 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.301 | a clause runs a median 1.02x market value here (n=2 real raids) and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
| "Your eleven" (top of the ladder) compares only the NEXT jornada, off real confirmed lineups/injuries | the standings table below simulates the other 31 jornadas too, where nobody has lineup news yet and squad value dominates — the two can point opposite ways (this week's confirmed news vs. the season's average squad quality) without either being wrong |
| Beyond the next jornada, P(start) reverts to his own season-standing rate | a suspension or a knock is dated to the match it was announced for — nothing here predicts a FUTURE one not yet known, e.g. who gets injured in March |
| Rivals never transfer | a steal that guts a squad assumes its manager does not simply buy someone back — flatters the steal |
| Teammates score independently, MATCH TO MATCH | two defenders of one club still land on opposite ends of the per-match pool in the same round — only their SEASON-LONG rating (club_rel) is shared, not one week's luck |
| Cash scores zero | nothing models the market next cycle, so holding money looks worthless and a standalone sale can never look good |
| p_win's season-long spread uses DRIFT_FRAC=0.04514680029230099 (fit from real data this run) | see "Season-long drift" below for the fit itself — every published win-probability model checked (538's NBA/NHL/MLB) is far more humble than 70%+ about a full season this early regardless of the exact value, which is what 1.0 as an unfitted default already reflects |
| Shape prior | shape from 2125 observed matches |
| P(start) fit | P(start) is futbolfantasy's own figure: on 654 confirmed starts the fitted version did not beat it out of sample (Brier 0.114 fitted vs 0.113 raw) |
| win % and finish are single simulated draws | at FINAL_TRIALS=3000, the same real inputs have been measured (2026-08-31) to swing roughly ±7 points (e.g. 19% to 26% on one real board) run to run — read the headline number as a band that wide, not a precise reading |



## Where the numbers come from

🟢 asked for within its own cadence · 🟡 it has missed a turn and what you are reading is the last answer · 🔴 the readers have dropped it and the report is on its fallback · ⚪ not fetched at all, built here from the rest.

**Newest row** is the snapshot that carried the reading, which is not when it was fetched: a page nobody asked for is carried into the next sweep and re-stamped. The light is on the asking.

| | Table | What it is used for | Fetched from | Rows | Newest row | Fetching |
|---|---|---|---|--:|---|---|
| 🟢 | api_activity | every transfer, which is what the ledger replays — one row per deal, so the newest is the last deal and not the last sweep | LaLiga Fantasy API | 246 | 23 Sep 23:55 CEST | fetched 1 minute ago |
| 🟢 | api_leagues | your cash and the league's id | LaLiga Fantasy API | 237 | 24 Sep 07:38 CEST | fetched 1 minute ago |
| 🟢 | api_lineup | the eleven you have actually fielded, and the formation the app says you are playing | LaLiga Fantasy API | 2,354 | 24 Sep 07:38 CEST | fetched 1 minute ago |
| 🟢 | api_market | what is on offer, and the bids on it | LaLiga Fantasy API | 7,349 | 24 Sep 07:38 CEST | fetched 1 minute ago |
| 🟢 | api_players | names for players nobody owns any more — one row per player, first sighting kept | LaLiga Fantasy API | 148 | 23 Sep 22:38 CEST | fetched 9 hours ago |
| 🟢 | api_standings | the league table — position, points, squad value, and your balance | LaLiga Fantasy API | 1,185 | 24 Sep 07:38 CEST | fetched 1 minute ago |
| 🟢 | api_stats | what the app scored each player, broken into what he did — one row per player per week per stat, a correction being a later row rather than an overwrite | LaLiga Fantasy API | 15,310 | 23 Sep 22:38 CEST | fetched 1 minute ago |
| 🟢 | api_teams | all five squads | LaLiga Fantasy API | 17,146 | 24 Sep 07:38 CEST | fetched 1 minute ago |
| ⚪ | clubs | the same, for clubs | src/crosswalk.py | 20 | — | rebuilt every run from the tables above |
| 🟢 | elo | team strength, which ranks the fixture term | clubelo.com | 4,336 | 24 Sep 07:38 CEST | fetched 3 hours ago |
| 🟢 | fixtures | who plays whom next, for the fixture term | analiticafantasy.com | 2,949 | 24 Sep 07:38 CEST | fetched 3 hours ago |
| 🟢 | lineups | probable XI percentages, both sources | analiticafantasy.com, futbolfantasy.com ×40 | 25,560 | 24 Sep 07:38 CEST | fetched 19 minutes ago |
| 🟢 | market | price, value, position, fitness — every player in the game | futbolfantasy.com | 28,967 | 24 Sep 07:38 CEST | fetched 1 minute ago |
| 🟢 | matches | fixtures, kickoffs, results | futbolfantasy.com | 91,200 | 24 Sep 07:38 CEST | fetched 3 hours ago |
| ⚪ | players | the crosswalk: one key per player across all four spellings | src/crosswalk.py | 725 | — | rebuilt every run from the tables above |
| 🟢 | points | realised points per jornada, the actuals in every table below | futbolfantasy.com | 2,206 | 21 Sep 07:44 CEST | fetched 1 minute ago |
| 🟢 | starters | confirmed elevens, which is what P(start) is graded on | futbolfantasy.com | 299,854 | 24 Sep 07:38 CEST | fetched 14 hours ago |

### The model, as configured right now

| Term | Setting | Fitted? |
|---|---|---|
| Formula | `xPts/j = shrunk pts-per-match × fixture × P(start)` | — |
| Shrinkage | K = 8 matches, applied twice: last season toward the positional prior, then this season toward that | yes |
| Fixture factor | per-club attack/defense fitted from real goals (17 of 20 clubs; the rest fall back to ±12% by squad-value rank, MIN_AD_MATCHES not yet met) | yes, for 17 of 20 |
| Home advantage | +13.3% | yes — fit from 1281 real matches (home 1.53, away 1.17 goals/match, ratio 1.306) |
| Team strength | summed squad value — Club Elo was scraped but did not cover every club in the market, and half a league ranked by Elo is not a ranking | — |
| P(start) read from | `futbolfantasy` | see the Brier table |
| Fixture applies to | fielding only — never a buy, a sale or the line | — |
| Season spread, match to match | each round resamples a real per-match score, rescaled to the player's rate | from 2125 observed matches |
| Season spread, RATE ERROR | the rate is a mean of a few matches, so each simulated season multiplies it by one draw of cv/√(matches+K) held all year — median ±50% of a rate across the squads | derived, not fitted |

### How to read the tables

**The ladder** — one table, read top to bottom: a plan, not a menu. The funding is implicit — sell the SELL rows and the BUY rows are what the money reaches. `Start` is the probable-XI read, recalibrated and blended, the same figure the forecast multiplies by. `xPts/j` already has that applied. `€` on a KEEP or SELL row is what it raises; on a SAVE row, how far short you are. On a BUY or RAID row it is his market price, plus (on a RAID) the extra premium a clause forces above it — his real worth and what taking him costs, not a net cash figure: a raid can leave you holding more cash than it spent, and reading that surplus as the price would say the opposite of what happened. `Season` is simulated: extra points over the jornadas left, the same seasons with and without the move — on a KEEP or SELL row, "without" is his best REAL replacement, not nothing, so a negative number there can still mean keep him: his own best alternative costs more than he does, not that he scores less than zero. `pts/M€` is Season per million actually spent — how CHEAPLY a gain arrived, not how big it is, so read Season first: there are only eleven starting shirts, and a tiny gain at a tiny price can still carry a flattering rate. A dash means the move is net cash-neutral-or-positive (nothing to divide by) or, on a SAVE row, a shortfall nobody can act on yet. `Where` names who holds him, and on a payable clause also the rival closest to affording it — a first name and `today`/`~Nd`, an ESTIMATE off their reconstructed balance, the app's daily allowance, and how fast that manager has actually raised money this season, never a prediction he actually wants the player. `PAR` is season points above the LEAGUE's own replacement level at his slot (the score of the last man the league can start there, pooled across every squad, not just yours) — comparable across positions on that basis, and a different question from `Season`: a good player in a deep position can show a modest PAR while still being the right one to keep.

**The league table** — `Pts` is the real league total today. `Season` is the simulated total and its 10-90 band — a mean with no band beside it reads as a prediction it is not. `P(above)` is how often the simulation has you finish above them.

### Forecast vs actual — last 21 days

| Measure | Value |
|---|--:|
| Player-intervals scored (2026-27) | 53 |
| Predicted, total | 187 pts |
| Actual, total | 260 pts |
| **Mean absolute error (per match played)** | **3.3 pts** |
| Pairs predating the fixture term | 7 of 53 |

_Read every xPts/j in this report as ± the error above, at least. Only predictions logged before an interval are scored, so hindsight is excluded by construction; the sample is your own squad and grows about 15 pairs a jornada._

_21 of 53 intervals overpredicted, 32 underpredicted (mean signed error -1.4 pts) — the "Biggest miss" table below is the tail, not the whole picture._

_vs. a trivial guess (everyone scores the sample's own mean, 4.9 pts/match, no player identity at all): ours 3.28 MAE, that guess 3.30 MAE — does not clearly beat it yet (90% CI on the gap: -0.93 to +0.88 pts, straddles zero)._

_Not enough data yet to break out by scoring band (needs 20+ per bucket) — will start showing once more jornadas have locked._

| Next fixture (the blended factor actually applied — attack/defense where fitted, else the ±12% rank fallback) | n | Mean forecast | Mean actual | Error |
|---|--:|--:|--:|--:|
| harder | 24 | 3.6 | 6.9 | -3.3 |
| neutral | 11 | 4.0 | 4.5 | -0.4 |
| easier | 11 | 3.9 | 2.7 | +1.2 |

_Per player-match. Positive error on **easier** together with negative on **harder** means the band is too wide; the reverse, too narrow; both near zero, about right. Judge nothing on a single-digit n._

| Biggest miss | Forecast | Actual | Error |
|---|--:|--:|--:|
| Robin Le Normand | 3.6 | 18 | -14.4 |
| Omar El Hilali | 2.6 | 14 | -11.4 |
| Beñat Turrientes | 1.1 | 10 | -8.9 |
| Ionut Radu | 6.0 | 14 | -8.0 |
| Iñigo Vicente | 3.0 | 10 | -7.0 |

### Season-long drift

**Fit from real data this run: 0.05** (h1 var 0.516, h3 var 0.520 (rate_rel-normalised, n=42/29) -> drift_frac 0.05).

### Rate uncertainty floor

Still the stated default (0.50) — too few graded pairs with a logged pj (n=0, need >=30) — keeping 0.50.

### Who to believe about the eleven

| Source | Calls | Mean claim | Hit | Brier |
|---|--:|--:|--:|--:|
| **starts** — 1496 confirmed, 65 locked round(s) | | | | |
| analitica | 1232 | 81% | 64% | 0.236 |
| futbolfantasy ←read | 3488 | 34% | 35% | 0.095 |
| our forecast | 726 | 43% | 42% | 0.119 |
| analitica — named, no number | 128 | — | 38% | — |

| **starts, same population AND same instances as our forecast only** — the real fair comparison (n=726 claims) | | | | |
| analitica | 285 | 84% | 61% | 0.281 |
| futbolfantasy ←read | 694 | 42% | 45% | 0.159 |
| our forecast | 726 | 43% | 42% | 0.119 |
_vs analitica: 90% CI on the Brier gap -0.201 to -0.130 — beats it._
_vs futbolfantasy: 90% CI on the Brier gap -0.064 to -0.025 — beats it._

_Every Brier above excludes claims within 10 points of 50% — a source's hardest, most genuinely uncertain calls, which never enter any number shown here._

| **appearances** — the wider, blunter sample; a 20-minute substitute counts | | | | |
| analitica | 2996 | 81% | 20% | 0.595 |
| futbolfantasy ←read | 8209 | 34% | 12% | 0.220 |
| our forecast | 2135 | 42% | 16% | 0.273 |
| analitica — named, no number | 305 | — | 12% | — |
| futbolfantasy — named, no number | 3 | — | 0% | — |

| Not graded | Calls |
|---|--:|
| within 10 points of 50%, on starts | 962 |
| jornada 1 — its opener kicked off before this repo saw a kickoff for it, so there is no honest cutoff | all |
| within 10 points of 50%, on appearances | 2010 |

_Brier: mean squared error of the probability, 0 perfect and 0.25 a coin flip. Claims are scored as last published before the round's first kickoff. Lower Brier **on starts** earns `LINEUP_SOURCE` in ffcore/tidy.py; appearances break ties only._

_Our forecast vs. trivial baselines, n=877 — a DIFFERENT sample than the tables above (this one joins the rate and start sides on jornada via golden_rows(), no 10-point undecided-band exclusion): ours 0.141, a flat 50% guess 0.250, a constant 44% guess 0.246 (Brier, lower is better) — beats both, adding real information._
