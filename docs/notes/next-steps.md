# Next steps (2026-11-05)

Where we are: the modules and their contracts are in (`simplify.md`), zero frontier crossings, 39 self-test
suites. The only thing not yet seen is a scheduled live run on the new code (next 10:03 CET).

## A. Engineering, in order

| # | Step | Why | Test first |
|---|------|-----|------------|
| 1 | Watch the live run: `decision_sale` rows, board sale lines, no errors | first end-to-end run of the new Decision on real data | none: it is the check |
| 2 | **Done.** `tools/lfg-run` pulled with a plain `--rebase`, which flattens a merge among the unpushed commits and re-resolves its conflicts (reflog 06:41, `pull --rebase --autostash -X theirs`). Now `--rebase=merges`; `tools/pullcheck.py` builds scratch repos, runs the exact line from `lfg-run`, and fails if the merge is flattened (red on the old line, green on the new) | main could silently change under any later integration | `pullcheck.py` |
| 3 | **Replaced** by the blockers check (done): nothing live reads `settlement_moves` (only the replay was meant to), so its partial-win overshoot is not worth fixing now. `decision.blockers` runs at the plan's end on two futures, nothing done and the plan done, and lists debt at the lock, no eleven, squad over the cap (`decision_blocker`, `board_json.blockers`, the page's red lines). A placed bid the plan does not make is withdrawn by the board, so the plan future counts only its own buys | the alarm is what you act on | `_selftest_blockers`, the board test, `sim` record test |
| 4 | Listed vs relist odds: from the season's offers, the chance a listed player gets an offer on a night; the board then compares keeping him listed against waiting | the sale lines compare only the offer with waiting | seeded offers, expected chance |
| 5 | Unlisted players sell at their value in the plan; the game only offers for listed ones. Use the listed/unlisted ratios and add a `list` move | the plan overstates what an unlisted player brings | seeded squad |
| 6 | Recommender order and cut-offs from data (worth per euro is structural today) | you asked for no magic numbers | seeded season, expected order |
| 7 | Pin legacy forecast moments (`pinned`) | a rerun at the same cut can drop an old run's cells | migrate test |
| 8 | Re-read the earlier non-TDD tests (ingest, `expected_gap`) | weak tests | n/a |

Not engineering, and yours: cash must be ≥ 0 by Fri 2026-11-06 20:00Z (21:00 CET) or the matchday scores 0
(cash was −84.4M); the Julián Álvarez offer (63.4M) against holding; listing Carreras, Yeray Álvarez,
Raúl García and Dani Martínez (zero points; the game only offers for listed players).

## B. The website: what a good one does for winning

Reviewed from `website/src/pages/Fantasy.jsx` (the page), `server/index.js` (`/api/fantasy-*`) and the board
JSON (`reports/decisions.json`). The page is one scroll: head (chance to win, lock, cash, can bid, finish),
"Do this", the Buys and Sales tables, standings, links to the box. Buttons ask `window.confirm`, the box acts
and the page polls for the answer.

### What you do to win, and what the page must answer

1. **Not lose by default.** Cash ≥ 0 at the lock, a full legal eleven, no bid left open. Everything else is
   second to this: a 0 for a matchday costs more than any buy gains.
2. **Bid before the market closes (22:24 CET).** Who, at what price, funded by what.
3. **Judge the nightly offers (22–23 UTC, ~24 h life).** Take, wait, list, or relist.
4. **Field the best eleven before the lock.**
5. **Watch rivals and your own exposure** (clauses, who is above you, how likely).
6. **Know whether it worked** (did the bid win, what did the sale make, did the chance move).

### Findings against the page today

| # | Gap | Effect |
|---|-----|--------|
| U1 | No deadline strip. The lock is in the head, but not the auction close, the time left, or what blocks you (debt, open bids, a hole in the eleven, zero-point players unlisted) | the one rule that zeroes a matchday is a line of small text |
| U2 | "Do this" is a flat list; its order (sales that fund a bid come first, a swap's sale with its buy) is not shown, nor the cash after each step | you can do a step out of order and end in debt |
| U3 | A move shows points, not what it does to the chance of first | the head says 9% → 7%, the rows say +4.9 pts; you cannot tell which move moves the 9% |
| U4 | **No way to say "not this one"**: Álvarez 63.4M vs hold is your call and the plan cannot take it; there is no hold/veto/pin and no recompute | the plan keeps proposing what you have already decided against |
| U5 | The sale facts the board now sends are cut: `sd` has no entry in the page's `FACT` map, so it prints "offer vs the usual (sd) 1.46366639490068"; the first three facts show, the rest sit under "more" | the made/better-offer lines are the ones that answer "are we making money" |
| U6 | The eleven is a row ("Field them"), not a picture: no formation, bench, minutes risk, who is injured or doubtful | you cannot check it at a glance before the lock |
| U7 | Open bids are not a section: price, chance to win, when they close, withdraw. A lost bid is not told | "no bid left open" is a rule you cannot see |
| U8 | Cash is a number and "after the plan"; not the path (now → after buys → after sales → at the lock) against the debt limit | the debt rule is invisible until it fails |
| U9 | Confirmation is `window.confirm("Bid 5.1M on Moi Gomez?")`: no cash after, no chance, no undo | the cost of a mis-tap is a real bid |
| U10 | Rivals are a folded table; exposure ("At risk") lists clauses without ranking, cost to protect or who can afford to raise them | the threat is listed, not prioritised |
| U11 | The ping is one long line of verbs | a phone notification should say the next deadline and the one thing to do |
| U12 | Freshness: "Updated 06:35" but not "this is N hours old, the market closes in M" | you may act on a board older than the offers |
| U13 | No history: what the last plan did, bids won/lost, sale made against the plan's number | you cannot learn whether to trust the numbers |

### What it should be (phone first, one thumb)

Top to bottom, one screen each.

1. **Now.** The next deadline with a countdown, then the blockers, each a red/green line that opens the fix:
   cash at the lock, eleven complete, bids open, unlisted zero-point players. All green says "nothing blocks you".
   Below it the chance of first (today → after the plan) with the season band.
2. **Do this, in order.** A numbered list in the order to do it. Each step: the move, its price and chance, its
   points and its change in the chance of first, and the cash after it. A swap's sale is shown with its buy. Tapping
   opens a sheet with the reasons (the facts, formatted) and the buttons. Confirmation is a sheet that repeats
   price, chance and cash after, with an undo window where the app allows one.
3. **Hold / not this.** On every row, "keep him" and "I'll decide": the board is recomputed without that move,
   with the chance of first before and after beside it, so Álvarez 63.4M against holding is one tap and one number.
4. **Offers.** One card per offered player: the offer, what you paid, what it makes, the offer in standard
   deviations from the usual, the chance a better one comes, and the verdict (take / wait / list). Relist
   against wait appears when step A4 exists.
5. **Bids.** Open bids with the chance to win, the close time and withdraw; yesterday's wins and losses.
6. **Eleven.** The pitch: formation, bench, expected points, minutes risk, one button to field the board's eleven.
7. **League.** Rivals ordered by the chance they finish above you, with cash and the gap to first; exposure
   ranked by the clause and by who can pay it.
8. **Since last time.** What the previous plan did, in points and money.

Notifications: one ping for the next deadline ("Auctions close in 1h: 2 bids to place, cash −84M"), another
only when a blocker appears. Not the list of verbs.

### Where each piece belongs (the contracts above hold)

| Piece | Owner | Needs |
|-------|-------|-------|
| Blockers, deadlines | Board (`board_rows`) | `next_lock`, `listing_ends`, cash, the plan; no new Decision output |
| Step order, cash after each step | Board, from `decision_plan` and `settlement_moves` | cash per step is a derived column |
| Change in chance of first per move | **Decision** (one optimiser; the board only reads `decision_option`) | a `win_after` per option |
| Hold / not this | Decision (a constraint on the next run); the page sends it, `act.py` records it | a `decision_hold` input table, read by `recommend`/`plan_rows`, tested seeded |
| Sale card verdict | Decision (`decision_sale`, `assess_sale`) | already there; relist chance is A4 |
| Formatting (`sd`, chance, money) | Page (`FACT`) | one entry per unit the board sends; test in `fantasyBoard.test.js` |
| Bids, history | Board, from the ledger and `settlement_moves` | open-bid view, last run's outcome |

Order of work: U5 (one `FACT` entry and its test) → U1 and U2 (the board already has the data) → U3 and U4
(Decision work, test first) → the rest. The page decides nothing; any new number comes from a board column.
