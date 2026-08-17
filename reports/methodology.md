# How the forecast works — and how it's doing

### The formula

Every player's **xPts/j** — expected points per jornada — is:

    xPts/j = shrunk points-per-match × fixture × P(start)

**Shrunk points-per-match** pulls an average toward the median for the position: `(points + 8×prior) / (matches + 8)`, prior = median pts/match among players in that position with 10+ matches. 8 matches of prior weight means a 3-game wonder is mostly prior and a 34-game regular is mostly himself.

It runs **twice**. Last season is shrunk toward the positional prior, and the result becomes the prior for THIS season, shrunk the same way with the same K=8. So a player who has played two jornadas is still mostly last season, and one who has played twenty is mostly this one, with no switch-over date to pick and no second constant to guess. With no matches played yet it collapses exactly to last season's number.

**Fixture** is who he plays next: teams are ranked by summed squad value — Club Elo was scraped but did not cover every club in the market, and half a league ranked by Elo is not a ranking and the rank is mapped onto ±12%, with ±4% for home advantage. It is a RANK, not a ratio — Real Madrid's squad is worth 4.6× the median one, and facing them does not cost a defender four fifths of his points. **Both numbers are guesses**, not fits: nothing has been played, so there is nothing to fit them to. They are deliberately small, and the table below grades them as soon as jornadas exist. Every logged row carries the raw Elo gap as well as the factor, so the band can be re-fitted against a continuous rating rather than the rank it was flattened into.

The fixture applies to **fielding**, which is one round. It is left OUT of every buy and sell figure, and out of λ, because you own a player for months and next Saturday's draw is not a reason to sign him.

**P(start)** is futbolfantasy's probable-XI percentage, read twice daily. A player listed without a percentage gets a neutral prior; one absent from the page entirely gets a low one. Promoted-side players have no top-flight record, fall back to the positional prior, and are marked **assumed**. analiticafantasy's reading is printed beside it and is **not** blended in: neither source has been checked against a played jornada, so there is no weight to blend them by.

The **team index** is the sum over the best legal XI. It is NOT a points forecast and the report no longer prints it as one: the shrunk-points term is in points, but P(start) multiplies it by a probability and the fixture term by an unfitted guess, so the total is a ranking number whose scale means nothing. Only DIFFERENCES in it are worth reading — this swap is worth 3.4, that signing 1.6 — which is exactly what the report reports.

**λ, the exchange rate.** Every market call is priced in one unit: index points per million euros. λ is what your cash buys today, measured by walking the unowned pool best-rate-first until the money runs out (`ffcore.bid.frontier`), so it is the rate of the last purchase you could afford. Buy above it, sell below it, and the one setting is `lambda_buffer` — how much better than the going rate a purchase has to be. Because it is a RATIO, the arbitrary scale of the index cancels, which is why λ is safe on an uncalibrated forecast when a points total is not. Each run appends the rate it judged with to `data/decisions/lambda_log.csv`: if the season's realised ratios sit above the λ printed at the time, λ was too low and the buffer was covering for it.

### What it deliberately ignores (for now)

- **Sub cameos** — P(start) multiplies the whole average, so a 30% starter is modelled as 0.3 × his points, when in reality he often plays 20 minutes and scores something. Forecasts for rotation players run low.
- **Position-specific fixture sensitivity** — a clean sheet is far more opponent-driven than a striker's goal, and the fixture term treats them identically. This is the first thing to add once ±12% itself has been graded.
- **Anything but points and minutes** — no goals, assists, cards or expected-goals data is scraped, so nothing about HOW a player scores reaches the forecast.

Each of these is a candidate fix, but only after the comparison below shows which one actually costs points.

### Forecast vs actual — last 21 days

**2 player-intervals** (2026-27): predicted **5** pts total, actual **14**. Mean absolute error **4.3 pts per player-match** — read every xPts/j in this report as ± that, at least.

Only predictions logged **before** each interval are scored; hindsight is excluded by construction. Sample is your own squad, so it grows ~15 pairs a jornada.

| Forecast bucket | n | Mean forecast | Mean actual |
|---|--:|--:|--:|
| 2–3 | 2 | 2.7 | 7.0 |

**Is the fixture term earning its place?** It moves a forecast by up to ±12% and was never fitted, so this is the table that decides whether to keep it, widen it, or delete it.

| Next fixture | n | Mean forecast | Mean actual | Error |
|---|--:|--:|--:|--:|
| harder | 1 | 2.6 | 6.0 | -3.4 |
| easier | 1 | 2.8 | 8.0 | -5.2 |

_Per player-match. A positive error against an **easier** fixture together with a negative one against a **harder** fixture means the band is too wide; the reverse means too narrow; both near zero means it is roughly right. Judge nothing on a bucket with a single-digit n._

Biggest misses (forecast − actual):

- **Omar El Hilali** — forecast 2.8, actual 8 (-5.2)
- **Iñigo Vicente** — forecast 2.6, actual 6 (-3.4)

### Who to believe about the eleven

**88 confirmed starters** across 1 locked round(s), off the match pages. A substitute is a MISS here — this is the question both sources are answering. The claim scored is the last one published before the round's first kickoff, because that is when the lineup locked and later news could not have been acted on.

| Source | Calls | Mean claim | Started | Brier |
|---|--:|--:|--:|--:|
| analitica | 26 | 88% | 85% | 0.111 |
| futbolfantasy ←read | 175 | 43% | 42% | 0.035 |

_**Brier** is the mean squared error of the probability: lower is better, 0.25 is a coin flip, and it punishes confidence more than caution. A source whose mean claim sits far from its start rate is miscalibrated even if it ranks players well._

- **analitica** — 31 calls published with no number on them, 100% started

_40 claim(s) sat within 10 points of 50%: not a call either way, so not graded._

_Read the gap between the sources, not the level. The clubs playing the round's opening matches have their elevens CONFIRMED by the time it locks, and both sources copy them, so their share of the table is scored on published fact rather than on a forecast. Whichever source publishes more of those looks better than it forecasts._

The wider but blunter sample, kept because it reaches back to before the match pages were collected:

Graded on **appearances**, not starts: a 20-minute substitute counts. That flatters both sources by the same amount, so the comparison holds even though the level does not. Only claims logged before the interval opened are scored.

| Source | Calls | Mean claim | Appeared | Brier |
|---|--:|--:|--:|--:|
| analitica | 108 | 86% | 23% | 0.606 |
| futbolfantasy ←read | 424 | 41% | 9% | 0.277 |
Calls published with no number on them, which can only be graded as a hit rate:

- **analitica** — 31 named starters, 0% appeared

_98 claim(s) sat within 10 points of 50% and are not graded: that is not a call either way._

**The gate.** Once a source has a few hundred graded calls, whichever has the lower Brier **on the starts table above** earns `LINEUP_SOURCE` in ffcore/tidy.py — a one-line change, and the only thing that should ever move it. Appearances break a tie, never the other way round: they are the question nobody asked. Until then nothing is blended, because a weight fitted on one jornada is a guess wearing a decimal point.
