# How the forecast works — and how it's doing

### The formula

Every player's **xPts/j** — expected points per jornada — is:

    xPts/j = shrunk points-per-match × fixture × P(start)

**Shrunk points-per-match** pulls an average toward the median for the position: `(points + 8×prior) / (matches + 8)`, prior = median pts/match among players in that position with 10+ matches. 8 matches of prior weight means a 3-game wonder is mostly prior and a 34-game regular is mostly himself.

It runs **twice**. Last season is shrunk toward the positional prior, and the result becomes the prior for THIS season, shrunk the same way with the same K=8. So a player who has played two jornadas is still mostly last season, and one who has played twenty is mostly this one, with no switch-over date to pick and no second constant to guess. With no matches played yet it collapses exactly to last season's number.

**Fixture** is who he plays next: teams are ranked by total squad value and the rank is mapped onto ±12%, with ±4% for home advantage. It is a RANK, not a ratio — Real Madrid's squad is worth 4.6× the median one, and facing them does not cost a defender four fifths of his points. **Both numbers are guesses**, not fits: nothing has been played, so there is nothing to fit them to. They are deliberately small, and the table below grades them as soon as jornadas exist.

The fixture applies to **fielding**, which is one round. It is left OUT of the buy-side figure in question 1, because you own a player for months and next Saturday's draw is not a reason to sign him.

**P(start)** is futbolfantasy's probable-XI percentage, read twice daily. A player listed without a percentage gets a neutral prior; one absent from the page entirely gets a low one. Promoted-side players have no top-flight record, fall back to the positional prior, and are marked **assumed**. analiticafantasy's reading is printed beside it and is **not** blended in: neither source has been checked against a played jornada, so there is no weight to blend them by.

The **team forecast** is the sum over the best legal XI, so ≈35 means: this eleven is expected to score about 35 points in a jornada, before variance — and single-match variance is huge.

### What it deliberately ignores (for now)

- **Sub cameos** — P(start) multiplies the whole average, so a 30% starter is modelled as 0.3 × his points, when in reality he often plays 20 minutes and scores something. Forecasts for rotation players run low.
- **Position-specific fixture sensitivity** — a clean sheet is far more opponent-driven than a striker's goal, and the fixture term treats them identically. This is the first thing to add once ±12% itself has been graded.
- **Anything but points and minutes** — no goals, assists, cards or expected-goals data is scraped, so nothing about HOW a player scores reaches the forecast.

Each of these is a candidate fix, but only after the comparison below shows which one actually costs points.

### Forecast vs actual — last 21 days

_No completed jornada in the window yet. points.py has no per-jornada rows to compare against; this section fills itself in after the first matches._
