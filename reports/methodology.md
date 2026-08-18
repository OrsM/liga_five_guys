# How the forecast works — and how it's doing

### The model, as configured right now

`xPts/j = shrunk points-per-match x fixture x P(start)`. What each term means, why it is shaped that way, and what it deliberately ignores is in the README — this table is only what the code is currently set to, read from the code so it cannot drift.

| Term | Setting |
|---|---|
| Shrinkage | K = 8 matches, applied twice (last season toward the positional prior, then this season toward that) |
| Fixture band | ±12%, plus 4% at home — a RANK, not a ratio |
| Team strength | **Club Elo rating**, a result-based rating with no transfer fees in it |
| P(start) read from | `futbolfantasy` |
| Fixture applies to | fielding only — never to a buy, a sale, or the line |

**Both fixture numbers are guesses, not fits.** The tables below are what will eventually settle them.

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
| analitica | 239 | 86% | 17% | 0.665 |
| futbolfantasy ←read | 836 | 41% | 7% | 0.265 |
Calls published with no number on them, which can only be graded as a hit rate:

- **analitica** — 39 named starters, 0% appeared

_214 claim(s) sat within 10 points of 50% and are not graded: that is not a call either way._

**The gate.** Once a source has a few hundred graded calls, whichever has the lower Brier **on the starts table above** earns `LINEUP_SOURCE` in ffcore/tidy.py — a one-line change, and the only thing that should ever move it. Appearances break a tie, never the other way round: they are the question nobody asked. Until then nothing is blended, because a weight fitted on one jornada is a guess wearing a decimal point.
