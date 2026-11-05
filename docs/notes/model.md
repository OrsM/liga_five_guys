# The model (2026-11-05)

What the machine models, stated once. Each line is checked against the repo or the data (the 2026-11-05 stress test:
six read-only attackers); what is not checked says so. The map of modules is `simplify.md`; this is what they model.

## The state

A **player** is free (on the game's market) or owned by a manager. He has: a forecast of points for the rest of the
season (refitted after each jornada), fieldable or not, a price, listed or not, and, if listed, an offer.

Two clocks:
- **Daily.** Price changes once a day, at the first morning snapshot (verified: every player-day, 20,498 of 20,498).
  Offers are created at the daily close (22:24 CET: 485 of 520) and expire at the next one, 24 h less ~20 s. Auctions
  close at 22:24:00.
- **Per jornada.** The forecast, fieldable and the points. The **lock** is the checkpoint.

Derived, never stored twice: the squad (owner = me), cash (the ledger's fold), the eleven (the best legal eleven of
the fieldable squad), a manager's standing (the points of his squad).

## The rules the plan lives under

- Points go to the owner, only if cash ≥ 0 at the lock (README) and with a legal eleven (one of `rules.SHAPES`; an
  empty slot scores nothing).
- A bid may total cash plus `DEBT_SHARE` (20%) of team value (`rules.bid_limit`): a **second** check beside the
  lock's, on a value that moves daily.
- The squad holds `SQUAD_MAX` = 24, **bids counted** (the app refuses more). `after()` checks the cap on won
  players only, at the end.
- The game offers only for listed players, at each close.
- A buy whose auction closes after his club's kickoff plays none of that jornada (`late_arrivals`, already in the cell).
- Clauses are paid at once; no protection after a transfer; one shield a matchday (README). Not modelled: rivals taking
  your players (exposure, U10), `buyout_until`, `shielded` (dropped by the views).

## Moves, and when they happen

A move changes a player's owner or listed flag. It has a **window** (when you can act) and an **effect time**.

| Move | Window ends | Effect (verified in the data) |
|---|---|---|
| bid | the auction closes | won: cash leaves and the player joins within one snapshot of the close |
| sale to an offer | the offer expires (the next close) | pays at once (cash +offer, squad −1 within the 12-min snapshot) |
| list | now | the first offer comes at the next close |
| field | the lock | at the lock |

Not established from the data: whether the game ever buys an unlisted player by itself, a bid's tie-breaks, whether a
lineup edit after the lock is possible, what the 100k cash bumps are, bonus income.

The plan is a set of moves; to evaluate it, apply them in order of effect time, then check the constraints at the lock.

## The objective

The aim is to finish first. The planner maximises the best eleven's **expected points** over the jornadas left, each
buy at its win chance (`decide.sql`); the chance of first is reported on those points. (The earlier "the objective is
the chance of first" was loose.)

## Offers, measured (2026-11-05, my listed players, 2026-08-25 to 11-05; NULL-money rows read as "listed, no offer")

- A listed player had an offer on **49%** of nights (534 of 1081 player-nights); **42%** of two-night windows had none on
  either night; some players sat listed 38-43 nights with offers on few. Not independent: per player, not per night.
- When there is one, offer / value is mean 0.997, sd 0.057 (min 0.87, max 1.12); mostly noise around 1.0, not player-specific.
- Next night's best offer against this night's: +0.006 of value, sd 0.073 (higher 46%, lower 40%): waiting for a
  better offer is not worth, on average, anything; a longer run's best beats its first in 77% of runs by construction (a
  maximum over time), not as a rule.
- So a later offer is **not certain**: the plan's `waiting` (an offer each night) is optimistic; per night about half.

## What the stress test found (open, ranked)

1. **A sale to an offer cannot follow the auctions** the same night (the offer expires at the close, when the bids
   settle), and **a later offer comes on about half the nights**, per player. A funding sale is therefore either a
   sale on a standing offer (sure, but before the bids settle) or on one that may not come. Decision's `proceeds` is
   `max(offer, waiting)`: both cases merged, the best case assumed. The failure of the lock (cash below zero: the
   whole matchday) is the cost nobody prices. Ruled out by the attack: a per-sale win/lose lottery with branch
   enumeration (2^k outcomes on the hot path; most paired buys win at ~0.97; it prices the wrong risk).
   Direction: each sale carries the chance it is realised by the lock (1 on a standing offer; one minus no offer on
   each night left for a listed player; 0 unlisted), from the measured offer rate; the plan's cash check flags a
   plan that is safe only through sales that may not come. A constraint, not a second objective.
2. **Unlisted players have no sale at all** until listed (then an offer at the next close, half the nights).
3. **The cap and the debt limit count pending bids**; `after()` checks the cap on won players, at the end.
4. **Squad after moves is written four times** (`fund`, `after`, the recommender, `dec_squad`) and running cash twice
   (`fund`, `after`; tied by a property test). Fixed so far: the debt limit (`rules.bid_limit`), the measure's
   step order (`in_groups`).
5. `settlement_moves` times a sale differently from `schedule` and nothing reads it.
6. `nights_left` is one aggregate for all sales; per-player offer expiry (`offers.expires_at`) must replace it.
7. No epsilon on `free < 0`.
