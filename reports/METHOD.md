# Liga Five Guys — how the numbers are made — 2026-09-08 17:33 UTC




## What the simulation cannot see

| Not modelled | Which way it bends the answer |
|---|---|
| Jornada 6 is half played — 2 clubs are done | their points are already in `now`, so only the rest of the round is simulated, and it still re-picks an eleven that is in fact already locked |
| A buyout premium is charged at **0.000 places per million**, the median of every run that has measured it — today's own reading is 0.000 | a clause runs a median 1.52× market value here and the app pays back only the value, so the premium is gone for good. It is charged against the move, but priced off what more money would buy you today — most days, very little |
| "Your eleven" (top of the ladder) compares only the NEXT jornada, off real confirmed lineups/injuries | the standings table below simulates the other 33 jornadas too, where nobody has lineup news yet and squad value dominates — the two can point opposite ways (this week's confirmed news vs. the season's average squad quality) without either being wrong |
| Beyond the next jornada, P(start) reverts to his own season-standing rate | a suspension or a knock is dated to the match it was announced for — nothing here predicts a FUTURE one not yet known, e.g. who gets injured in March |
| Rivals never transfer | a steal that guts a squad assumes its manager does not simply buy someone back — flatters the steal |
| Teammates score independently, MATCH TO MATCH | two defenders of one club still land on opposite ends of the per-match pool in the same round — only their SEASON-LONG rating (club_rel) is shared, not one week's luck |
| Cash scores zero | nothing models the market next cycle, so holding money looks worthless and a standalone sale can never look good |
| p_win's season-long spread uses DRIFT_FRAC=1.0 (still the unfitted default) | see "Season-long drift" below for the fit itself — every published win-probability model checked (538's NBA/NHL/MLB) is far more humble than 70%+ about a full season this early regardless of the exact value, which is what 1.0 as an unfitted default already reflects |
| Shape prior | shape from 1237 observed matches |
| P(start) fit | P(start) fitted on 651 confirmed starts across 20 team sheets: futbolfantasy recalibrated (logit +0.0 +1.0x), blended 20% with analiticafantasy where it has an opinion (a named starter counts 77%). Brier improves 0.000 on line-ups the fit had not seen |


## Where the numbers come from

🟢 asked for within its own cadence · 🟡 it has missed a turn and what you are reading is the last answer · 🔴 the readers have dropped it and the report is on its fallback · ⚪ not fetched at all, built here from the rest.

**Newest row** is the snapshot that carried the reading, which is not when it was fetched: a page nobody asked for is carried into the next sweep and re-stamped. The light is on the asking.

| | Table | What it is used for | Fetched from | Rows | Newest row | Fetching |
|---|---|---|---|--:|---|---|
| 🟢 | api_activity | every transfer, which is what the ledger replays — one row per deal, so the newest is the last deal and not the last sweep | LaLiga Fantasy API | 152 | 08 Sep 17:31 | fetched 3 minutes ago |
| 🟢 | api_leagues | your cash and the league's id | LaLiga Fantasy API | 164 | 08 Sep 17:31 | fetched 3 minutes ago |
| 🟢 | api_lineup | the eleven you have actually fielded, and the formation the app says you are playing | LaLiga Fantasy API | 1,551 | 08 Sep 17:31 | fetched 3 minutes ago |
| 🟢 | api_market | what is on offer, and the bids on it | LaLiga Fantasy API | 4,936 | 08 Sep 17:31 | fetched 3 minutes ago |
| 🟢 | api_players | names for players nobody owns any more — one row per player, first sighting kept | LaLiga Fantasy API | 113 | 07 Sep 20:45 | fetched 21 hours ago |
| 🟢 | api_standings | the league table — position, points, squad value, and your balance | LaLiga Fantasy API | 820 | 08 Sep 17:31 | fetched 3 minutes ago |
| 🟢 | api_stats | what the app scored each player, broken into what he did — one row per player per week per stat, a correction being a later row rather than an overwrite | LaLiga Fantasy API | 7,646 | 08 Sep 05:59 | fetched 3 minutes ago |
| 🟢 | api_teams | all five squads | LaLiga Fantasy API | 11,595 | 08 Sep 17:31 | fetched 3 minutes ago |
| ⚪ | clubs | the same, for clubs | src/crosswalk.py | 20 | — | rebuilt every run from the tables above |
| 🟢 | elo | team strength, which ranks the fixture term | clubelo.com | 2,920 | 08 Sep 17:31 | fetched 12 hours ago |
| 🟢 | fixtures | who plays whom next, for the fixture term | analiticafantasy.com | 2,238 | 08 Sep 17:31 | fetched 12 hours ago |
| 🟢 | lineups | probable XI percentages, both sources | analiticafantasy.com, futbolfantasy.com ×40 | 131,060 | 08 Sep 17:31 | fetched 3 minutes ago |
| 🟢 | market | price, value, position, fitness — every player in the game | futbolfantasy.com | 131,122 | 08 Sep 17:31 | fetched 3 minutes ago |
| 🟢 | matches | fixtures, kickoffs, results | futbolfantasy.com | 63,460 | 08 Sep 17:31 | fetched 12 hours ago |
| ⚪ | players | the crosswalk: one key per player across all four spellings | src/crosswalk.py | 721 | — | rebuilt every run from the tables above |
| 🟢 | points | realised points per jornada, the actuals in every table below | futbolfantasy.com | 1,266 | 08 Sep 05:59 | fetched 3 minutes ago |
| 🟢 | starters | confirmed elevens, which is what P(start) is graded on | futbolfantasy.com | 115,389 | 08 Sep 17:31 | fetched 12 hours ago |

### The model, as configured right now

| Term | Setting | Fitted? |
|---|---|---|
| Formula | `xPts/j = shrunk pts-per-match × fixture × P(start)` | — |
| Shrinkage | K = 8 matches, applied twice: last season toward the positional prior, then this season toward that | yes |
| Fixture band | ±12% across the opponents by rank, not by ratio | **no, a guess** |
| Home advantage | +4% | **no, a guess** |
| Team strength | **Club Elo rating**, a result-based rating with no transfer fees in it | — |
| P(start) read from | `futbolfantasy` | see the Brier table |
| Fixture applies to | fielding only — never a buy, a sale or the line | — |
| Season spread, match to match | each round resamples a real per-match score, rescaled to the player's rate | from 1237 observed matches |
| Season spread, RATE ERROR | the rate is a mean of a few matches, so each simulated season multiplies it by one draw of cv/√(matches+K) held all year — median ±17% of a rate across the squads | derived, not fitted |

### How to read the tables

**The ladder** — one table, read top to bottom: a plan, not a menu. The funding is implicit — sell the SELL rows and the BUY rows are what the money reaches. `Start` is the probable-XI read, recalibrated and blended, the same figure the forecast multiplies by. `xPts/j` already has that applied. `€` is what you end up with for doing that row, funding included — a SELL row is what it raises, a BUY row is that money minus what he costs, a SAVE row is how far short you are. `Season` is simulated: extra points over the jornadas left, the same seasons with and without the move — on a KEEP or SELL row, "without" is his best REAL replacement, not nothing, so a negative number there can still mean keep him: his own best alternative costs more than he does, not that he scores less than zero. A `*` on a KEEP/SELL row's "vs X" name means the BUY list's own row for X is funded by somebody ELSE's sale, not his — two different players' money legitimately reaching the same man, not an error to reconcile. `pts/M€` is Season per million actually spent — how CHEAPLY a gain arrived, not how big it is, so read Season first: there are only eleven starting shirts, and a tiny gain at a tiny price can still carry a flattering rate. A dash means the move is net cash-neutral-or-positive (nothing to divide by) or, on a SAVE row, a shortfall nobody can act on yet. `Where` names who holds him, and on a payable clause also the rival closest to affording it — a first name and `today`/`~Nd`, an ESTIMATE off their reconstructed balance, the app's daily allowance, and how fast that manager has actually raised money this season, never a prediction he actually wants the player.

**The league table** — `Pts` is the real league total today. `Season` is the simulated total and its 10-90 band — a mean with no band beside it reads as a prediction it is not. `P(above)` is how often the simulation has you finish above them.

### Forecast vs actual — last 21 days

| Measure | Value |
|---|--:|
| Player-intervals scored (2026-27) | 63 |
| Predicted, total | 214 pts |
| Actual, total | 262 pts |
| **Mean absolute error** | **3.1 pts per player-match** |
| Pairs predating the fixture term | 9 of 63 |

_Read every xPts/j in this report as ± the error above, at least. Only predictions logged before an interval are scored, so hindsight is excluded by construction; the sample is your own squad and grows about 15 pairs a jornada._

_vs. a trivial guess (everyone scores the sample's own mean, 4.2 pts/match, no player identity at all): ours 3.10 MAE, that guess 3.20 MAE — beats it, adding real information._

| Forecast bucket | n | Mean forecast | Mean actual |
|---|--:|--:|--:|
| under 2 | 18 | 1.6 | 3.1 |
| 2–3 | 9 | 2.6 | 5.2 |
| 3–4 | 19 | 3.5 | 4.3 |
| 4+ | 17 | 5.6 | 4.6 |

| Next fixture (±12%, unfitted) | n | Mean forecast | Mean actual | Error |
|---|--:|--:|--:|--:|
| harder | 31 | 3.4 | 4.1 | -0.7 |
| neutral | 11 | 4.4 | 4.5 | -0.1 |
| easier | 12 | 3.8 | 4.8 | -0.9 |

_Per player-match. Positive error on **easier** together with negative on **harder** means the band is too wide; the reverse, too narrow; both near zero, about right. Judge nothing on a single-digit n._

| Biggest miss | Forecast | Actual | Error |
|---|--:|--:|--:|
| Iñigo Vicente | 3.0 | 12 | -9.0 |
| Beñat Turrientes | 1.1 | 10 | -8.9 |
| Orri Steinn Óskarsson | 1.7 | 10 | -8.3 |
| Íñigo Ruiz de Galarreta | 3.8 | 12 | -8.2 |
| Dani Martínez | 0.5 | 8 | -7.5 |

### Season-long drift

Still the unfitted default (1.00) — not enough graded pairs yet (h1=41, h3=15, need >=20 each) — keeping 1.00.

### Who to believe about the eleven

| Source | Calls | Mean claim | Hit | Brier |
|---|--:|--:|--:|--:|
| **starts** — 902 confirmed, 5 locked round(s) | | | | |
| analitica | 638 | 84% | 69% | 0.201 |
| futbolfantasy ←read | 2030 | 37% | 37% | 0.084 |
| our forecast | 48 | 71% | 79% | 0.094 |
| analitica — named, no number | 99 | — | 67% | — |

| **starts, same population as our forecast only** — the fair comparison | | | | |
| analitica | 60 | 93% | 77% | 0.211 |
| futbolfantasy ←read | 102 | 68% | 74% | 0.118 |
| our forecast | 48 | 71% | 79% | 0.094 |
| **appearances** — the wider, blunter sample; a 20-minute substitute counts | | | | |
| analitica | 2513 | 80% | 21% | 0.577 |
| futbolfantasy ←read | 8581 | 37% | 11% | 0.239 |
| our forecast | 331 | 64% | 19% | 0.409 |
| analitica — named, no number | 715 | — | 8% | — |
| futbolfantasy — named, no number | 2 | — | 0% | — |

| Not graded | Calls |
|---|--:|
| within 10 points of 50%, on starts | 399 |
| within 10 points of 50%, on appearances | 1764 |

_Brier: mean squared error of the probability, 0 perfect and 0.25 a coin flip. Claims are scored as last published before the round's first kickoff. Lower Brier **on starts** earns `LINEUP_SOURCE` in ffcore/tidy.py; appearances break ties only._

_Our forecast vs. trivial baselines, n=50: ours 0.100, a flat 50% guess 0.250, a constant 70% guess 0.177 (Brier, lower is better) — beats both, adding real information._
