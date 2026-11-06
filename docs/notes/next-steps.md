# Next steps (2026-11-06) — the one list

## Where we are

Modules and contracts are in (`simplify.md`), zero frontier crossings, 39 self-test suites, live on the box and the
phone. Decision is the one place anything decision-shaped is computed; the Board and the page read and word it.

What the Decision keeps per run (all in its tables, all read as they are):

| Piece | Where | What it answers |
|---|---|---|
| order (`ord`), `cash_then` | `decision_plan` | in what order, and the cash each step leaves |
| `due`, `why` | `decision_plan` (`decision.schedule`) | by when each step must be done and what sets it: the auction closes, after the auctions, before the lock |
| blockers | `decision_blocker` (`decision.after`) | what stops you if you do nothing, and if you do the plan: debt at a jornada's lock (the moves made by it) or once all are made, no eleven, squad over the cap |
| skip | `decision_skip` | what skipping one step leaves, judged alone |
| sale assessment | `decision_sale` | offer against paid, against the usual, the chance a better one comes |

One fact each: market close = `listing_close` (ingest), the lock = `next_lock`, one blocker wording =
`board_blocker_word`, one money/time formatter on the page = `show`.

## Yours, not engineering

Cash must be ≥ 0 by Fri 2026-11-06 20:00Z (21:00 CET) or the matchday scores 0 (it was −84.4M; the plan reaches
+3.5M only through the Julián Álvarez offer, 63.4M, your call). Listing Carreras, Yeray Álvarez, Raúl García and
Dani Martínez (zero points; the game only offers for listed players) is also yours.

## The list (one list; work top to bottom; mark done here, nowhere else)

Rule for each: the page decides nothing; any new number is a column from Decision.

**Now: what wins the league**
1. **Chance of first per move (U3).** Each move's chance of first with and without it, computed in Decision
   (`flips`), read by the page beside "plan without it". This is the mix and match: you pick, you see what it does.
2. **The plan block as one compact table (U1–U3 as presentation).** One row per move: action and price, points,
   the "plan without it" column with its red blocker, bid chips, a small × for hold; facts behind a tap. Head as one
   dense line. Built on 1, so it is built once. Check with `shot` (page height, first screen).
3. **Confirm sheet (U9).** Before a bid or an accepted offer: cash after, chance of first.

**Then: make the plan more right**
4. Policy for a sale whose offer lapses at the close, then lost bids as a branch, then a cap on pending bids (`model.md`).
5. Search caps (3 buys, 10/4 shortlist) with a time budget; test first, profile before commit. Then the board price view
   (cash at each jornada start, bid against asking, points per euro, needed against optional sale).
6. Relist against wait; unlisted players' proceeds in the plan.

**Then: know whether it worked**
7. Lost bids told (U7), the cash path against the debt limit (U8), rivals ranked by the chance above you (U10),
   one ping with the next deadline (U11), board age against the close (U12), history of bids won/lost (U13).

**Housekeeping, when idle**
8. Recommender cut-offs from data; pin legacy forecast moments; sibling worktrees (`act`, `squad-cap`, `fast-plan`,
   `fast-int`, `move-cost` dirty; `position-baseline` has two parked October commits); stale agent patches in
   `~/.local/share/lfg-stale-patches/`; `late_arrivals` redundancy; grade-stage speed (~250 s).

## Done (kept short)
Per-row `detail`; offer expiry (`dec_offer`); the plan in order with `cash_then` and due; blockers; deadline strip;
one `show` formatter; the eleven as a pitch; **hold / "not this one"** end to end (page sends changes, the box owns the
set, `lfg-watch` re-plans quick); one `Move` component for buys and sales; the plan fitting the first phone screen;
`READ` list so the live plan sees locks, closes and offers.

## Yours, not engineering
Account actions are yours: bids, accepting offers, listing players (Carreras, Yeray Álvarez, Raúl García, Dani
Martínez: the game only offers for listed players). Tonight: market closes 22:24 CET, lock 21:00 CET.
