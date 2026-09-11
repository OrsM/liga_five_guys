# sim.py — design notes

Condensed 2026-09-05 out of long inline comments in `src/sim.py`, so the
history of *why* a rule exists lives in one homogenized place instead of
sprawling across the file. Code carries a short pointer back here; this
file carries the dates, the quotes, and the rejected alternatives. Nothing
factual was dropped in the move — only reworded for length.

## header() — cash line shows two numbers, not one

`u.cash` (what you can actually spend) is the headline; the app's raw
balance can differ when a bid of yours is still pending. Found 2026-08-29:
the app's balance screen read +3.30M while this line read -2.64M, with
nothing on the page explaining the 5.94M gap (three real pending bids, not
an error). Fix: show both numbers the phone already has (balance and
pending), not a new one to reconcile by hand.

## ladder_rows() — "vs X" band notes

A held player's band (from `best_swap_for()`) answers "sell him, buy the
best his own money reaches," so the row names which player that is.
`action.buy != k` excludes a candidate's own band (his Action buys HIM,
funded by dead weight — "vs himself" is nonsense). The note is suppressed
on `in`/`out` groups (`xi_change()`'s free lineup diff, no transfer
happening) — found 2026-09-01, a "vs X" note on a bench/start swap read as
"sell him for X" when nothing was being sold.

**The same target can be a real, separately-funded BUY row.** `best_swap_for()`
asks "what THIS man's own money reaches" — a different question from
`candidates()`/`rank()`'s one winning funder per target — so the two can
legitimately name different funders for the same target. Found 2026-09-01
(swarm review): Ali Houary's row named Antonio Blanco as "vs X" while the
real, ranked Antonio Blanco BUY row was actually funded by Moi Gomez. Fixed
by naming the real funder inline once (an asterisk + a closing-paragraph
explanation), rather than leaving the coincidence for the reader to spot.
The asterisk form (not the full "reaches him via X instead" sentence) was
chosen because the long form kept breaking the table's width on a phone
(Miguel raised this twice, 2026-09-01).

## ladder_rows() — CHASE is a separate group, not a re-sort

See `chase_keys()`. Empty whenever `trailing()` is silent (BUY group stays
byte-identical). Emitted ahead of BUY, in ceiling order (`chase[k]`, 1 then
2) rather than `_move_rank_key()` order — widest band first is the point of
the section.

## ladder_rows() — BUY / RAID split

Free agents, then a real raid — two headings, nothing dropped. Before this
(Miguel, 2026-09-01: "I want to know if any free players are worth buying
honestly"), every BUY row sorted together on value-for-money, which has no
reason to prefer a free pickup over a clause raid that pays for its own
premium in points — a real report could (and did) show nothing but
rival-owned targets at the top, with no way to tell "no good free options
today" from "free options exist but a bigger raid outranked them."

A rival's own player is not one thing. A **clause** cannot be refused — pay
it and he is yours, the same certainty as a free pickup. A **listed**
target is the owner's own choice to sell, and this league's own measured
history (decide.py, re-derived and proven 2026-08-31 by replaying ownership
forward over the real ledger rather than reading it off ledger columns) is
that 0 of 119 real deals have ever been manager-to-manager — not rare, the
entire sample. Originally a third heading here (grouping listed separately
from RAID rather than mixing it into RAID, since a rival selling to Miguel
is not a live option — "no way they're selling willingly to me," found
2026-09-01 against a real report where 3 of the RAID rows turned out to be
listed, not clause). LISTED as a section was later removed entirely
(2026-09-05, `bf8bf94`): Miguel asked three separate times for rival-owned,
non-clause players to simply not appear, and the honest end state (asked
directly, since two prior guesses had already missed) was drop, not
relabel. `decide.candidates()` now filters a listed target out before it
ever becomes an `Action`, so it's absent from `rows`/`won` here, not
demoted to a heading — RAID (clause) is the only rival-owned row that can
appear.

Implementation: one `_move_rank_key()`-sorted list (`ranked`, built from
`won`), filtered by `route_kind()` for each heading — never separately
ranked lists, so no section can disagree with another about relative
order.

## ladder_rows() — PASS is free agents only

`rest` (every acquirable player, not just `candidates()`'s screened pool)
feeds SAVE and PASS independently of the BUY/RAID list above — a second,
parallel candidate pool `route_kind()`'s ownership fix could bypass
entirely. Found 2026-09-05, the SAME day the BUY/RAID work above shipped:
Miguel, within the hour, "I see a lot of competitor players in pass... I
really don't know how to explain what I want" — a rival-owned player who
"cleared the bar" was landing in PASS with a price, indistinguishable from
a genuine free-agent miss; worse, a clause-raidable rival who simply didn't
rank high enough for RAID also fell through here, rendered identically,
with no clause marker at all. Two candidate pools that looked like one
(`rows`/`won` vs `rest`) is exactly the duplication `route_kind()` existed
to close, one call site later. Fixed bluntly, Miguel's own end state:
PASS is free-agent-only, full stop, clause or not — no rival-owned row
anywhere in PASS, regardless of how close it came to clearing the bar.

## ladder() — the race, in the Where column

`race_cell()` renders `contest` (from `race()`), it does not recompute it —
one answer, two renderings (the markdown cell and the phone's `contest`
field), never two computations. Shown inline in Where ("he is BurtonGM89's,
and SusoGattuso can pay his clause in 3 days") rather than as its own
column, which would be an eighth column on a phone, empty on most rows.

## ladder() — BUY section, "none clear the bar today"

Free agents get their own heading (see the BUY/RAID/LISTED split note
above) so "is there anything worth buying that isn't a raid" gets a direct
answer. An explicit "none clear the bar today" line (rather than silence)
matters once RAID/LISTED exist as separate sections — silence used to mean
only "no buy candidates at all"; now it could also mean "candidates exist,
all rival-owned," and the reader should be told which.

## ladder() — column guide moved out, not inlined

Used to carry the full column-by-column explanation here, in markdown
nothing publishes (`digest.py`'s own docstring: `.runtime/parts/` fragments
are build artifacts, "nothing reads them, nothing publishes them"), while
`website/src/pages/Fantasy.jsx` carried its own, separately-worded rewrite
of the same facts — two copies, already drifted, for a document the reader
couldn't even reach from here. Moved into `methodology.py`'s
`column_guide_lines()` on 2026-09-01 (Miguel: "do we need all that long long
text? shouldn't it go somewhere else?") — the one place both the appendix
(METHOD.md) and the linked site page point at instead of each carrying
their own.

## wait_routes() — approx_gain() is a deliberate cheap stand-in

Not `ffcore.bid.gain()` — that re-picks a whole best XI per candidate via
`pick_xi()`, real but too expensive to run per candidate per Monte Carlo
trial here. Named apart from the real one (`approx_gain` vs `gain`) so the
two are never mistaken for each other.

Uses `u.market_exp`, not `forecaster.expected()` — `expected()` only scores
the ~89 players who could be in a squad; this question is about the other
~500. A player it was never given reads as 0.0, indistinguishable from
worthless — that once scored Lamine Yamal at nothing.

## caveats() — a clause race's Season/€ price prices winning, deliberately

Not a gap left unpriced — a genuine "N-player preemption game" (real-options
economics: several agents racing an indivisible, irreversible payoff —
patent races, first-mover investment races are the standard examples).
That literature's own answer to "how much does losing cost" is why this
isn't folded into a fabricated win-probability discount: the loser's
capital is NOT destroyed, only the option on this one target is — the cash
still buys whatever is next-best. Losing the race doesn't cost the gap
between Season and zero, it costs the difference between this row and the
NEXT row on the same table, which is already ranked and priced. Found
2026-09-01 (swarm review); the literature check is the same day — Miguel
asked whether anyone else modelling this kind of race had already worked
out how to price it. That literature's equilibrium result: a genuinely
contested race trends toward roughly even odds at the point either side
actually pulls the trigger — a reason to read "can pay today" as real
50/50-territory risk, not sharpenable further without real bidding data.

## VALUE_TOLERANCE (0.90) and MOVES_VALUE_FLOOR (0.25)

`VALUE_TOLERANCE`: how much of the best available move's season gain a
materially cheaper alternative may give up and still be the one
recommended, in `_best()`'s own value-for-money refinement (the single
headline pick only). Real judgment, not measured — the cash spent this
week does not come back this season (`rank()`'s own net-cost
accounting), so a move keeping 90%+ of the best gain for meaningfully
less money leaves next week's options open in a way the last 10%
doesn't buy back. Not 1.0 (biggest gain wins outright regardless of
cost) and not much lower (a move worth noticeably less of the season is
a worse move, full stop, whatever it costs). Still live.

`MOVES_VALUE_FLOOR`: RETIRED 2026-09-06, deleted along with
`_moves_floor()` — see `_move_rank_key()`'s own note above. It was the
floor half of "bar, then ratio" for the WHOLE ladder (not the single
headline pick `VALUE_TOLERANCE` guards): what counted as worth ranking
by efficiency at all, guarding against the fractional-knapsack trap (a
near-zero gain at an even smaller cost dividing out to a spuriously
enormous ratio). Real, judged, never measured against data in its own
right — and when `backtest.replay_ladder_percentile()` finally checked
the WHOLE tiered scheme it was part of against 200+ real historical
ladders, that scheme came out net NEGATIVE where a flat `pts_lo` sort
(no floor, no tiers at all) was net POSITIVE. Kept here for anyone
tracing an old `MOVES_VALUE_FLOOR` reference back to why it existed and
why it's gone, not because the mechanism survived.

## _move_rank_key() — d_win alone no longer wins tier 0 outright

`d_win` (the season's win-probability swing) is computed straight off the
simulated squads, BEFORE `rank()`'s own `lam*burn()` premium charge — the
same charge `d_pos` already carries (`d_pos = gross - charge`). A clause
costing well over its market value could lead the whole table on
win-probability alone even when paying that premium left `d_pos` negative
— the model's own honest "net loss once the premium is paid" answer,
silently overruled by an axis that never saw the premium. Found 2026-08-31
(Miguel: "why would having a clause, which means you pay above market
rates, be a good thing?"). Fixed by also requiring `d_pos > 0` for tier 0 —
this does not invent a win-probability-to-money exchange rate (this repo's
own principled objection to charging `d_win` directly, still correct), it
only refuses to let a move lead purely on `d_win` once its own priced
`d_pos` says the premium already cost more than the pitch gained.
`trailing()`/`chase_keys()` is the one place an average-case loss is
deliberately accepted for a title shot — gated on actually trailing and
labelled "worse on average," not the ordinary unlabelled tier every other
ranked buy passes through.

## CHASE_PICKS (2) — the Frontier Economics citation

Not a tuning knob, not this repo's own measurement — the cited finding it
implements. Frontier Economics' fantasy-football analysis of a team
trailing the leader in a small, winner-take-most league found P(win) is
maximised by fielding ONE OR TWO high-variance, high-ceiling "maverick"
picks, with returns diminishing then REVERSING past two — the same shape
as a short stack seeking variance in tournament poker (gambler's ruin/ICM),
where "expected points" is not the objective the payout structure actually
rewards. Two is a ceiling, not a target: `chase_keys()` returns fewer
whenever fewer qualify. Variance isn't free — every chase pick is bought
with expected points, so ranking everything by ceiling would be a strictly
worse table for a level-or-ahead manager, and a worse table even for a
trailing one past two picks — why the whole mode is gated on `trailing()`
rather than being a column beside `value`.

## market_model() — real_cycles/real_routes attached, not returned separately

`off.real_cycles`/`off.real_routes` are the raw per-cycle sets, attached to
the fitted `Offers` sampler rather than returned as a second value —
`Offers.cycles` is already a count (`len(cycles)`), so this can't collide
with it, and every caller that only wants the fitted sampler is unaffected.
`wait_routes()` reads it (via `getattr`, the same optional-capability
pattern `forecaster.rate_draw`/`start_draw` use) to grade today's real best
against real single-cycle history instead of a resampled hypothetical —
see `real_cycle_bests()`.

## render() — no sentences above the table

`verdict()` and `market_percentile()` (and the overdraft paragraph and the
three-route panel before them) were each added to explain a contradiction
rather than remove one, and each became another thing on the page that
could disagree with the table under it — `market_percentile()`'s own
headline ("an unusually good week") once claimed exactly that while every
BUY row failed the real bar below it. Both retired for the same reason.
What's left is the position, the formation, and the ranked table itself —
all of it data, none of it a separate claim about it.

## payload() — one wait_routes() call, read by everything

Used to call `wait_routes()` fresh for "wait," again for "verdict" (later
retired), and again for the "market_pct" headline (later deleted, see the
`market_percentile`/`verdict` retirement note above) — five resamples of
the same real market history per report, total, for one fact that cannot
differ between them. Found 2026-09-01, alongside the same duplication in
`render()`. Now one call, read everywhere.

## _best() — pts_lo, not mean d_pos (2026-09-06)

Miguel asked, in a money-management discussion, whether points should be
discounted so nearer jornadas count more (buying is for the whole
remaining season, but the far end of it is genuinely less certain). A
literal time-discount would bias a risk-neutral expected-value objective
for no principled reason — a point is a point wherever it falls. The real
version of his intuition: since a squad decision is revisable (you can
trade again next week), a candidate whose gain leans on distant,
`DRIFT_FRAC`-widened jornadas is a weaker case FOR ACTING TODAY than one
with an identical mean but a solid near-term floor, because the distant
part can be captured later with better information. `_best()`'s own
Monte Carlo already produces that floor for free — `pts_lo`, the 10th
percentile of the move's paired trial distribution — so `_best()` was
changed to pick `max(pool, key=lambda r: r["pts_lo"])` instead of
`max(pool, key=lambda r: r["d_pos"])`. No new discount constant; the same
uncertainty machinery (`rate_rel`, `club_rel`, `DRIFT_FRAC`) just read at
a different quantile. Validated against real history before being wired
in, not on theory alone: `backtest.replay_percentile_rank()` replayed
200+ real historical `reports/decisions.json` commits with this exact
substitution and found it would have beaten mean-ranking on real
outcomes (+64 vs +31 real points, 14/32 vs 9/24 net-positive calls).
Everything else in `_best()` — the eligibility gate, reliable-routes-
first, the `VALUE_TOLERANCE` cheaper-alternative refinement — is
unchanged; this was one targeted substitution, not a rebuild.

## _move_rank_key() — pts_lo, not mean d_pos or value (2026-09-06)

After `_best()`'s switch above, Miguel: "the whole ladder should follow
same logic why wouldn't it?" — a fair challenge to leaving
`_move_rank_key()` (every BUY/RAID row across both the markdown ladder
and the phone's JSON) on the old d_win/d_pos/value tiering. Checked
before wiring it in, not assumed: `backtest.replay_ladder_percentile()`
compared each real historical day's real top-3 (what the tiered scheme
actually produced) against a flat `pts_lo` sort over the same real
candidates. The tiered scheme was net NEGATIVE on real history (-94
points, 98 episodes); the flat sort was net POSITIVE (+90 points, 118
episodes) — a bigger, clearer margin than the single-pick test.
`_move_rank_key(r, u)` now returns `(reliable, -pts_lo)`. RETIRED
entirely, on that evidence: `MOVES_VALUE_FLOOR`, `_moves_floor()`, and
the "a d_win-driven move leads outright" tier (see this file's earlier,
now-superseded note on why that tier existed — `VALUE_TOLERANCE`, the
90% cheaper-alternative-tolerance constant, stays: it's `_best()`'s own
separate refinement above, untouched). A simpler single-metric rule
beating the accumulated special-casing lines up with this session's
earlier "the book" simplification pass — the complexity wasn't earning
its keep here either.

## payload() — moves sorted by the same rule as the ladder

`ladder_rows()`'s BUY group and `payload()`'s `moves` both call
`_move_rank_key()` (see its own note above) — one shared key, so the
markdown table a reader scrolls top to bottom and the phone's JSON
cannot rank the same candidates in two different orders. A chase pick is
flagged on its existing row (`chase_keys()`), not re-sorted to the top —
the ranking is unchanged whether or not chase mode fires.

## payload() — several fields exist only because the phone used to drift from the markdown

Multiple `moves` fields (`contest` — soonest-first, full list, same
`race()` the markdown's Where cell draws; `left`/`answer` — what you're on
afterwards and what the victim does about it) were shown in the markdown
ladder but missing from the phone's JSON — added so the two cannot show
different information about the same row.

`expected_finish`/`p_win` are rounded (2dp/3dp) rather than raw floats —
checked 2026-08-31 (Miguel asked directly whether `FINAL_TRIALS` itself was
false precision; see decide.py for the fuller answer). These are LEVELS
(not the paired differences the ranking runs on) so they carry real Monte
Carlo noise, and a 17-digit raw float (e.g. 0.22233333333333335) claimed a
precision the sample doesn't have. 2dp matches the markdown's own "%.2f"
(`header()`); 3dp on p_win is finer than the markdown's "%.0f%%" but
nowhere near the 17-digit noise it replaces. `trailing`'s three
probabilities are rounded here (not inside `trailing()` itself) so
`trailing()`'s own return value stays exact for its 0.5 trigger comparison.

`cash_locked` and `trailing` both exist so the phone can explain a number
rather than leave it a mystery: `cash_locked` says WHY `cash` is short of
the raw balance (a pending bid, summed via `decide.pending_sent`);
`trailing` (None when you're winning — the state the whole chase mode is
defined against) captions the CHASE rows with who the model says is
winning, the same reason the markdown heading carries it — an
average-case-loss recommendation with no stated reason is worse than none.

## wait_routes() — beats_now graded against real cycles, not the resampled band

`band` (`offers.best_over()`) is a maximum over many independent draws from
the whole pool — it beats one real day's actual listing almost by
construction, which is why this line once read "under the 1st percentile"
on 15 real days running regardless of whether the market was actually
weak. `real_cycle_bests()` asks the fair question instead: how does
today's real best compare to the OTHER real cycles this repo has actually
observed. `real_cycles`/`real_routes` (getattr, same optional-capability
pattern as `forecaster.rate_draw`/`start_draw`) carry that; an `offers`
without them (an old test fixture, or a caller not wired to
`market_model()`) falls back to `band` exactly as before.

**A listed cycle is not evidence of a good week to wait.** This pooled
history used to count a rival's own contested listing exactly like a real
free-agent offer — decide.py's finding (9b25510: 108 real transactions in
this league, zero manager-to-manager) is that a listed cycle is not a real
opportunity at all, so a week whose only offer was three rivals listing
their stars (who will almost certainly never sell) scored as "a great week
to wait" off zero real opportunities. Fixed: `group_of` returns `None` for
a listed player, dropping him from the cycle's own best (same
silence-not-a-guessed-zero rule `real_cycle_bests()` already applies) — a
cycle whose only offer was listed now contributes nothing to `hist`, as if
the week had no market. `by_route`'s own "listed" bucket is untouched —
grading a listed offer against listed history is still the right question,
just not the headline one.

**market_now_best is restricted to the same routes hist represents.**
`hist`/`band` are real free-agent market history (listed dropped, above).
`now_best` (a few lines up) is the best of the WHOLE acquirable board,
clause raids included — which `api_market.csv` (what `hist`/`band` are
fitted from) never observes. Comparing that mixed "best of anything"
against a free-agent-only history meant a single big clause opportunity —
nothing to do with whether the free/listed market was actually good —
could alone push "market Nth percentile" to an extreme. Found 2026-08-31
(Miguel questioning a "93rd percentile · unusually good week" reading that
was really just today's best clause target, Nahuel Tenaglia, being large).
Fixed by restricting today's side to the same routes `hist` represents.

**"best"/"lo"/"hi" use real history first, the resampled band as fallback**
— same fix as beats_now, one level up. Used to read straight off
`best_over()`'s resampled band; a live check the day this was fixed found
its median at 7.37 against real history's own 4.15, with the band's
minimum (4.33) already above the real median — a player never actually
offered (the "Not for sale" table already knows who) still gets drawn into
the resample by value alone, skewing every number built from it.

## wait_routes() — by_route grades free-pickup vs contested-bid separately

"How good is today's real free-pickup market" and "how good is today's
contested-bid market" are different questions with different risk — the
exact thing conflating them under one "market" label used to hide (see
`decide.Universe.route`'s own docstring). Graded separately, not blended.

## wait_routes() — "Not for sale" is not the same as "not worth having"

The best players in the free pool are simply not on offer, and you cannot
ask for one — so the report names them with how long the app would take to
deal them, rather than leaving "114 would improve your eleven" to read as a
shopping list. That misreading once cost a real sale: Ruben Garcia was
described as buyable when he was merely unowned.

## wait_routes() — "Act today" uses rows' real d_pts when given

`rows`, when given, is `decide.rank()`'s own scored candidates — the same
ones the BUY table ranks. Before this (found 2026-09-01, swarm review),
"Act today" always used `season(now_best)`, a cheap linear stand-in (best
single-jornada margin × jornadas left, no re-picked XI, no simulation) —
sharing its "Season pts" column header with the BUY table's real simulated
figure two sections above while being a genuinely cruder number: a live
case read "Act today +244" off the same player the BUY table correctly
priced, simulated, at +74. `rows` only ever holds moves already affordable
today (`rank()`'s own screen: `cost <= cash + proceeds`) — exactly what
"Act today" means — so the best of their real `d_pts` (and its band) IS the
number the BUY table's top row already carries, not a second estimate.
`rows=None` (an old caller, or the self-test) keeps the cheap estimate —
nothing real to fall back to without it.

## ladder_rows() — pts_lo ranking re-audited, 2026-09-11

A swarm audit of this repo's forecast/backtest validation (Miguel: "3 good
proven tests, not a bunch of iffy ones") found `backtest._grade_episodes()`
scored every episode open-ended, from its own day to "now" — an arm firing
more episodes, or firing earlier in the replay window, racked up more total
points independent of call quality, and a superseded episode kept scoring
to "now" too (two episodes for the same rank slot silently double-counting
the same real jornadas). This was the actual mechanism behind both
`replay_ladder_percentile()`'s original "-94 vs +90" reading (2026-09-06,
cited when `pts_lo` replaced the tiered value-floor/win-probability scheme)
and `replay_percentile_rank()`'s "+64 vs +31" reading (same day, cited when
`_best()`'s single headline pick switched to `pts_lo`) — not a clean result
either time.

Fixed: `_grade_episodes()` now scores a FIXED forward window
(`HORIZON_DAYS=10.0`, chosen so most of the real commit history clears it at
today's still-short season) instead of open-ended-to-now, and
`backtest.compare_arms()` runs a real bootstrap significance test
(`stats.bootstrap_gap()`) on the two arms' per-episode nets rather than a
bare `total_net` comparison. Re-run on real history (2026-09-11): pts_lo vs
mean-rank is NOT significantly different (19 vs 16 graded episodes, 90% CI
on the gap straddles zero); pts_lo vs the retired tiered scheme is ALSO not
significantly different (78 vs 72 graded episodes) — and the "current"
(shipped) arm's own real net is now +178 (positive), not the -94 once
reported.

**This does not mean pts_lo was a bad choice** — it means the backtest
evidence FOR it was itself broken, and at today's n the honest backtest
answer is "no significant difference either way," not "confirmed" or
"refuted." `pts_lo` stays the live ranking on the structural argument
`ladder_rows()`'s own docstring gives (shares d_win's Monte Carlo draws, a
real quantile, no invented constant) — a different, independent kind of
support than a backtest win, and one this fix didn't touch. Re-run
`backtest.py --selftest` again once more jornadas have locked (n grows a
handful of episodes per jornada) before treating either reading — "no
difference" or a future "beats" — as settled.
