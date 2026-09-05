# ffcore/bid.py — design notes

Long-form rationale moved out of inline comments 2026-09-05 (comment-volume
cleanup) so the source carries a one-line pointer instead of the full
narrative. Nothing here is a duplicate source of truth — the code is still
the only place the rule is enforced; this is why it is enforced that way.

## issue-23-roundness

**Issue #23: the old reading of the ledger was inverted.** rivals.py
classified a price by whether it was a round number and concluded that an
exact one was "the app's own valuation, which means nobody competed — every
exact purchase was a player you could have had for the same money." The
repo's own ledger says otherwise: of the ten priced buys on it when that was
written, the five exact-priced ones went for +1.5%, +2.6%, +2.6%, +9.2% and
+12.7%. None was the app's valuation, so none was available at the floor.

Roundness cannot carry that inference, in either direction:

- A round price is indeed human-chosen — only 0.7% of the 610 current
  market values are divisible by 10k, so the app almost never hands you
  one. That half of the old heuristic survives, as an observation about
  how they type.
- A non-round price is NOT the app's valuation. It is a human who typed a
  non-round number, and the premium column two cells away already says
  how far above the floor they went. The proxy adds nothing the direct
  measurement doesn't say better.
- Even a purchase at exactly the floor would not prove nobody competed. A
  sealed bid is paid as bid, so matching it wins only if the tie-break
  favours you, and the tie-break rule is not documented anywhere we can
  read. Verify it in-app before treating a floor price as a missed
  bargain.

So the signal is the premium over the floor, which the ledger measures
directly:

    floor    = today's market value. The minimum legal bid IS the value.
    premium  = price / value_at_the_time - 1

    prem = premiums(deals)                  # what winning has cost so far
    adv  = suggest(value, prem, cash, ceil)  # what to bid for this one
    g    = gain(pool, candidate, xi_total)   # what he adds if you play him

`gain()` is the marginal-value primitive from docs/design.md §6.3, at the
only precision the data currently supports: the change in the XI ranking
index from owning him. It is not euros per point, and it is not a forecast.

## at-floor-count

**Nothing here asserts how often the floor wins.** An earlier version of
this module said it never had, on the strength of ten buys that all
cleared the value. Ten rows later three buys had gone at exactly the
value, and the sentence was still in the report, printed as fact.
`Premiums.at_floor` now counts them so the reports state the current
number instead. When the ledger contradicts the prose, the prose is the
bug.

`premiums()`'s `at_floor` counts deals priced at or below the value, which
reads as a bid at the floor on the buy side and as the app underpaying you
on the sell side. It is a share of PURCHASES, never a probability of
winning — every row in the ledger is a bid that won, so nothing here can
say how often a floor bid loses. Dividing at_floor by n would reintroduce
that same sampling error wearing a percent sign.

## sell-side-randomiser

**The app randomises its own price** (issue #23, second half). Selling to
the market does not pay the value: `premiums(deals, "sell")` over the
twelve priced sells in this ledger spans -9.4% to +9.8%, five below and
seven above, which is the value plus or minus a tenth and not a valuation.
Two consequences:

- A sale is a coin flip worth about a tenth of the player either way, so
  never treat the value as the money a sale will raise.
- It is the closest thing to a P(win) curve available, and it is not one.
  Whether the same randomiser also bids against you for a free agent is
  INFERRED, NOT MEASURED — every deal in the ledger is a winning bid, so
  nothing here has ever observed a bid that lost.

A handful of deals is not a distribution: everything `premiums()` returns
is a summary of purchases made in the first fortnight of a season, so
`suggest()` reports the range alongside the median and the reports print
both. Treat the band as "what this league has done so far," never as a
probability of winning.

## deals-identity-join

`deals()`'s row arrives already identified — it asks the league which
player it is (`lg.txn_key(t)`) rather than matching its display name all
over again. That second match was a second answer to a question
`replay()` had already settled, and the two could differ because only one
of them had the app's id, the counterparty and the price in front of it.

It deliberately does not join via `market.at(..., value=price)`. A
purchase PRICE is not a VALUE: it is the value plus whatever it took to
win, measured here at up to +21.6%. Handing it to a join that tests
value-agreement within 5% picked the man whose value the price
undershot — and a buy cannot go below the value at all, so those matches
were not merely weak, they were impossible. Two of the three it "rescued"
were wrong, and the app's own ownership feed said so. Who owned him is the
evidence that settles a ledger row — see `league.identify`.

## low-priced-buys-not-confirmed-impossible

`low_priced_buys()`'s rows are NOT confirmed impossible. A normal market
bid cannot undercut the value, but Miguel flagged (2026-08-20) that an
instant sale to the app pays the SELLER roughly half of value, and whether
a player picked up that way can later be BOUGHT below his normal floor is
unverified either way — so a row here can be a mis-join (that is how the
C. Romero mis-join actually surfaced, by hand, once) or a legitimate
discounted relist. Not filtered by `usable()`: a stale snapshot changes
how much to trust the number, not whether it is worth a look.
