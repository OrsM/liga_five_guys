# ffcore/market.py — design notes

Long-form rationale moved out of inline comments 2026-09-05 (comment-volume
cleanup) so the source carries a one-line pointer instead of the full
narrative.

## spend-now-or-later

The question this module exists for is "spend now or spend later." Every
move the simulation ranks is scored against doing nothing for the rest of
the season, which is not the alternative on offer: the alternative is doing
something better in a few days, with the balance intact, against a market
that deals a fresh dozen players every cycle. Waiting therefore scored
exactly zero and any move with a positive number beat it BY CONSTRUCTION —
which is how a report comes to recommend spending everything you have on
the first thing that clears a low bar.

## value-weighted-pool

The market is not a random draw, and assuming it was would have been worse
than not modelling it at all. Measured across every cycle on record, the
players actually offered are about five and a half times more valuable
than the unowned pool they come from — median 9.58M against 1.72M — while
the POSITION mix is close to proportional. So `Offers` samples weighted by
value, with the exponent fitted (`Offers.fit()`) to reproduce the observed
quantiles rather than chosen: uniform would offer you journeymen and
flatter waiting, proportional would offer you Raphinha every cycle and
flatter it far more.

What it cannot do: it has one league's worth of cycles behind it, it
assumes the pool and the values stand still, and it knows nothing about a
rival buying the man you were waiting for. Every one of those makes
waiting look better than it is, which is the opposite of the bias it was
built to correct — so the report prints the band and the sample size
beside the number, always.

## chance-with-replacement-approximation

`chance()`'s `per_cycle * w_i / total` is each player's exact inclusion
probability only if a cycle's `per_cycle` offers were drawn WITH
replacement; the app deals distinct players per cycle, so the true
marginal is slightly lower (drawing the same weight twice is impossible).
Flagged in a swarm review (2026-09-01): at `per_cycle` ~12 against a pool
of ~570-600, the drawn fraction is small enough (~2%) that the error is
negligible here — this file caveats every other approximation it makes, so
this one gets the same note rather than reading as an oversight.

## unowned-is-not-available

`chance()`/`median_wait()`: unowned is not available, and the difference is
most of a season. The app deals about a dozen players out of five hundred,
and you cannot ask for one — so a man you want who is not on the market
today is not something you can go and buy, however unowned he is. On the
day this was written that was Ruben Garcia, sold in error and then
described as "buyable for less than you sold him for," which was simply
untrue.

## draw-np-vectorization

`_draw_np()`'s weighted sampling without replacement is vectorised by the
exponential race (Efraimidis-Spirakis): give every player a clock that
ticks at his own weight, Exp(1)/w, and deal the n that ring first. That is
not an approximation of `_draw_idx()`'s loop — it is the same distribution,
which is the only reason it is allowed to replace it. The loop it replaces
cost 10 of the 14 seconds the report took: 25,200 cycles, each one copying
a 570-entry list twice and then calling `random.choices` twelve times over
the whole of it. Same answer, drawn in one array operation instead of
302,400 scalar ones. The self-test's vectorised-vs-scalar comparison is
what actually checks "it is the same distribution" is true, not assumed.
