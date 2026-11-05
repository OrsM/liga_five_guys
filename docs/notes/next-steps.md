# Next steps (2026-11-05, evening)

## Where we are

Modules and contracts are in (`simplify.md`), zero frontier crossings, 39 self-test suites, live on the box and the
phone. Decision is the one place anything decision-shaped is computed; the Board and the page read and word it.

What the Decision keeps per run (all in its tables, all read as they are):

| Piece | Where | What it answers |
|---|---|---|
| order (`ord`), `cash_then` | `decision_plan` | in what order, and the cash each step leaves |
| `due`, `why` | `decision_plan` (`decision.schedule`) | by when each step must be done and what sets it: the auction closes, after the auctions, before the lock |
| blockers | `decision_blocker` (`decision.after`) | what stops you if you do nothing, and if you do the plan: debt at the lock, no eleven, squad over the cap |
| skip | `decision_skip` | what skipping one step leaves, judged alone |
| sale assessment | `decision_sale` | offer against paid, against the usual, the chance a better one comes |

One fact each: market close = `listing_close` (ingest), the lock = `next_lock`, one blocker wording =
`board_blocker_word`, one money/time formatter on the page = `show`.

## Yours, not engineering

Cash must be ≥ 0 by Fri 2026-11-06 20:00Z (21:00 CET) or the matchday scores 0 (it was −84.4M; the plan reaches
+3.5M only through the Julián Álvarez offer, 63.4M, your call). Listing Carreras, Yeray Álvarez, Raúl García and
Dani Martínez (zero points; the game only offers for listed players) is also yours.

## Open, in order

1. **Fold the per-row board fields** (`cash_then`, `skip`, `due`) into one `plan` object: each new field costs a
   column, a JSON key and a remove clause in `board_json`.
2. **Delete `settlement_moves`** (nothing reads it; `listing_close` and `schedule` cover its timing). A module in the
   design, so the user decides.
3. **Hold / not this**: a `decision_hold` input so one tap re-plans without a move (Álvarez against holding), with the
   chance of first before and after. Skip says only what is left, and judges each step alone, not summed.
4. **Offer expiry**: a sale on an offer is due before the offer expires, not only before the lock; needs an expiry fact.
5. Relist against wait (the chance a listed player gets an offer on a night); unlisted players' proceeds in the plan.
6. Recommender cut-offs from data; pin legacy forecast moments; re-read the early non-TDD tests.
7. Sibling worktrees (`act`, `squad-cap`, `fast-plan`, `fast-int`, `move-cost` dirty; `position-baseline` has two
   parked October commits); stale agent patches are in `~/.local/share/lfg-stale-patches/`.

## The website, against what you do to win

What you do: not lose by default (cash ≥ 0, a legal eleven, no bid left open); bid before the close; judge nightly
offers; field the best eleven; watch rivals; know whether it worked. The page's gaps, and what is done:

| # | Gap | State |
|---|-----|-------|
| U1 | deadline strip: next deadline with a countdown, blockers | blockers red lines and each step's due are in; countdown strip open |
| U2 | "Do this" in the order to do it, cash after each step | done (order, `cash_then`, due) |
| U3 | a move's effect on the chance of first | open: `win_after` per option, in Decision |
| U4 | no way to say "not this one" | open: item 3 above |
| U5 | facts formatted (`sd`, counts) | done: one `show` for every unit |
| U6 | the eleven as a pitch | open |
| U7 | open bids as a section, lost bids told | open |
| U8 | the cash path against the debt limit | partly: per-step cash |
| U9 | confirm sheet with cash after and chance | open |
| U10 | rivals ranked by the chance above you; exposure ranked | open |
| U11 | one ping with the next deadline | open: reads the same `due` rows |
| U12 | how old is this board against the close | open |
| U13 | history: bids won/lost, sale made against plan | open |

Rule for each: the page decides nothing; any new number is a column from Decision.
