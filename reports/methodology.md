# How the forecast works — and how it's doing

### The formula

Every player's **xPts/j** — expected points per jornada — is:

    xPts/j = shrunk points-per-match × P(start)

**Shrunk points-per-match** pulls a player's last-season average toward the median for his position: `(points + 8×prior) / (matches + 8)`, prior = median pts/match among players in that position with 10+ matches. 8 matches of prior weight means a 3-game wonder is mostly prior and a 34-game regular is mostly himself.

**P(start)** is futbolfantasy's probable-XI percentage, read twice daily. A player listed without a percentage gets a neutral prior; one absent from the page entirely gets a low one. Promoted-side players have no top-flight record, fall back to the positional prior, and are marked **assumed**.

The **team forecast** is the sum over the best legal XI, so ≈35 means: this eleven is expected to score about 35 points in a jornada, before variance — and single-match variance is huge.

### What it deliberately ignores (for now)

- **Fixtures** — no opponent-strength or home/away adjustment.
- **Sub cameos** — P(start) multiplies the whole average, so a 30% starter is modelled as 0.3 × his points, when in reality he often plays 20 minutes and scores something. Forecasts for rotation players run low.
- **This season** — the baseline is last season until the live points blend is turned on deliberately; a two-jornada sample should not drive an XI.

Each of these is a candidate fix, but only after the comparison below shows which one actually costs points.

### Forecast vs actual — last 21 days

_No completed jornada in the window yet. points.py has no per-jornada rows to compare against; this section fills itself in after the first matches._
