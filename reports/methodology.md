# How the forecast works — and how it's doing

### The formula

Every player's **xPts/j** — expected points per jornada — is:

    xPts/j = shrunk points-per-match × fixture × P(start)

**Shrunk points-per-match** pulls an average toward the median for the position: `(points + 8×prior) / (matches + 8)`, prior = median pts/match among players in that position with 10+ matches. 8 matches of prior weight means a 3-game wonder is mostly prior and a 34-game regular is mostly himself.

It runs **twice**. Last season is shrunk toward the positional prior, and the result becomes the prior for THIS season, shrunk the same way with the same K=8. So a player who has played two jornadas is still mostly last season, and one who has played twenty is mostly this one, with no switch-over date to pick and no second constant to guess. With no matches played yet it collapses exactly to last season's number.

**Fixture** is who he plays next: teams are ranked by summed squad value (Club Elo has not been scraped yet, so the wallet is standing in for the pitch) and the rank is mapped onto ±12%, with ±4% for home advantage. It is a RANK, not a ratio — Real Madrid's squad is worth 4.6× the median one, and facing them does not cost a defender four fifths of his points. **Both numbers are guesses**, not fits: nothing has been played, so there is nothing to fit them to. They are deliberately small, and the table below grades them as soon as jornadas exist. Every logged row carries the raw Elo gap as well as the factor, so the band can be re-fitted against a continuous rating rather than the rank it was flattened into.

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

_No completed jornada in the window yet. points.py has no per-jornada rows to compare against; this section fills itself in after the first matches._

### Who to believe about the eleven

_No played interval yet, so neither source has a record. `futbolfantasy` is read because it was first, not because it won anything._
