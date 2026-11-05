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

## Offers, measured (2026-11-05, my listed players, 2026-08-25 to 11-05)

- **Every listing leads to an offer**, one at each close while he is listed (the game's rule). A listing made after
  a close gets its first offer at the next. (A first reading of the data said a listed player had an offer on only
  49% of nights; that counted 3,425 empty polls, rows with no money and no offer id, taken before the night's offer
  existed, as nights without one. Wrong; retracted.)
- When there is an offer, offer / value is mean 0.997, sd 0.057 (min 0.87, max 1.12), mostly noise around 1.0 and not
  player-specific (offers with money only, so unaffected).
- Next night's best offer against this night's: +0.006 of value, sd 0.073 (higher 46%, lower 40%): waiting for a
  better offer is worth nothing on average; its risk is the amount, not whether one comes.

## What the stress test found (open, ranked)

1. **A sale to an offer cannot follow the auctions** the same night: the offer expires at the close, when the bids
   settle. A funding sale is either on tonight's standing offer (a known amount, before the bids settle: a player gone
   for nobody if the bid is lost) or on the next close's offer (certain to come, the amount about value, sd 0.057, the
   cash check at the lock then rests on that ratio). Decision's `proceeds` is `max(offer, waiting)`, which merges the
   two. Ruled out by the attack: a per-sale win/lose lottery with branch enumeration (2^k outcomes on the hot path; most
   paired buys win at ~0.97). Open: whether the plan should price the lock's risk from the offer ratio's spread (the
   amount needed against the distribution), a constraint on the cash check and not a second objective.
2. **Unlisted players have no sale at all** until listed (then an offer at the next close: list before the last close
   before the lock, or there is none).
3. **The cap and the debt limit count pending bids**; `after()` checks the cap on won players, at the end.
4. **Squad after moves is written four times** (`fund`, `after`, the recommender, `dec_squad`) and running cash twice
   (`fund`, `after`; tied by a property test). Fixed so far: the debt limit (`rules.bid_limit`), the measure's
   step order (`in_groups`).
5. `settlement_moves` times a sale differently from `schedule` and nothing reads it.
6. `nights_left` is one aggregate for all sales; per-player offer expiry (`offers.expires_at`) must replace it.
7. No epsilon on `free < 0`.
