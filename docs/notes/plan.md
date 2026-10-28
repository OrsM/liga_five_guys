# The plan as one problem: slots, the price of money, bids (2026-10-28)

Design, not yet code. Miguel: "I need to fill the legal 11 and maximize my
usage of money and bids towards the results that I want ... there's a core
component there that needs to be figured out."

## What the board does now, and why it keeps breaking

Each move is scored alone against doing nothing (`Universe.rank`), passed or
failed by a bar (`verdict`: 70% chance of helping), then moves are added one
at a time (`fill`) with the sales that pay for them found apart
(`raised`, `cheapest_cover`), and money is turned into points at a fixed
`MONEY_RATE`. The holes found on 2026-10-27/28 all come from that shape:

- sales costed one by one and summed, when two forwards sold together cost
  more than each alone (Niño + Vicente: -49 summed, -69 together);
- a squad with no keeper and no forward that could buy neither, since no
  one buy makes a side (fixed by a patch, `fits`);
- the debt step re-ranking every other sale as if its own sales were made;
- a buy shown as worth it alone and unpaid for (`consider`), followed, 48M
  into debt;
- `MONEY_RATE` and `CONFIDENCE` that no backtest can pin down (8 jornadas:
  451, 390, 367 and 445 points at rates 3, 5, 8 and 12).

They are one missing piece, in three parts.

## 1. A player is worth what he adds to the eleven

Each jornada j the eleven is the best legal one from the squad S: one
keeper, 3-5 defenders, 3-5 midfielders, 1-3 forwards (`rules.SHAPES`). That
is a small assignment problem, f_j(S) = the most expected points any legal
eleven from S scores. The squad is worth F(S) = sum over the jornadas to
come of f_j(S).

A player's worth is marginal: F(S + p) - F(S), his points over whoever would
fill his slot (value over replacement: Budimir's 168 season points cost 40
sold, Vicente taking his slot). Values interact: a second keeper adds almost
nothing, a keeper to a squad without one a whole slot; a keeper and a
forward can add nothing apart and much together. So moves are valued as a
set against the slots, never one at a time and summed.

## 2. Money has a price, and it comes out of the problem

The one money rule: cash at the lock at least zero (below it the jornada
scores nothing); bids may take you to 20% of team value into debt meanwhile.
Every decision trades points for money, at the rate the budget sets:

    maximize   F(S) + V(cash left)
    subject to cash - buys + sales >= 0 at the lock

With the budget priced by a multiplier lambda (Lagrangian relaxation),
the problem splits into: maximize F(S) - lambda * (money spent). lambda is
the shadow price of the budget, in points per million: what the next
million would buy at the margin. It is not set; it is the lambda at which
the best squad just fits the budget (found by bisection on lambda, or read
off the program's dual). It moves daily: high in debt (every million must
be found by a sale), low with cash and nothing worth buying.

Every line of the board then reads the same way:

- a buy is worth it if its points over replacement beat lambda x its cost;
- a sale is worth it if the points it loses are under lambda x what it raises;
- keeping cash is worth it if nothing beats lambda.

`MONEY_RATE`, the 70% bar, the forced-sales step, `cheapest_cover` and the
consider list are stand-ins for lambda, and go. What stays a judgement is
V: what money and squad value left at the end of the horizon are worth
(the horizon is the jornadas forecast; a million kept buys points later).
That is one number, lambda_end, and the backtest's question.

## 3. A bid is a lottery priced at lambda

A bid b on player p wins with chance P_p(b) (`bid_odds`, from the past
auctions of players nearest in trend). Its worth is
P_p(b) x (value over replacement - lambda x b): the best bid is below what
he is worth by an amount the odds set (bid shading, first-price sealed-bid
auction), not "asking plus a bit".

Several bids are a portfolio: whichever win, the budget must hold at the
lock. So the money rule becomes a rule on every outcome: cash after all bids
that could win, less the sales that will be made, at least zero (or, as a
chance constraint, at least zero but for a set small chance, offers on
sales drawn from the game's offer spread). Bids that cannot all be paid if
all win must come with the sales that would pay for them, or not be made
(the 2026-10-27 debt: Niño and Carreras both won, unfunded).

## Solving it

One integer program per run, the exact form of 1 to 3 (HiGHS, open
source; prototype `milp` branch solves today's board in seconds):

- variables: keep[p] for each of yours, take[p, b] for each buy at each bid
  level the odds offer, xi[p, j] for jornada j's eleven, shape[s, j] its
  formation;
- constraints: the eleven only from the squad, one formation each jornada;
  the money rule on every bid outcome; a player in one move at most;
- objective: sum over j of the elevens' expected points, bids weighted by
  their chance of winning, plus lambda_end x (cash and squad value left).

lambda is the budget constraint's price at the optimum (dual of the
relaxation, or the bisection above), shown in the headline. The program
replaces candidates, rank, verdict and plan (`decide.FUNNEL`); forecasts,
prices, offers and auction odds stay as inputs.

On 2026-10-28 the prototype found, on the board's own measure, sell Vicente,
buy Nsongo, Sucic and Recio: +33.6 season points against the board's +25.0
(buy Nsongo), its chance of helping 72%, over the board's own bar.

## The result wanted: first, not points

Points are the means; the aim is to finish first. With players independent,
a squad's season variance is a sum over its players, so linear in the
selection like its mean. Maximizing the chance of first (Hunter, Vielma and
Zaman, "Picking winners in daily fantasy sports using integer programming",
arXiv 1604.01455) is then a sweep: solve for mean + kappa x variance at a few
kappa, compute each plan's chance of first (`Standings.position`), keep the
best. Behind the leader the best plan takes more variance; ahead, less.
This replaces the 70% bar, which only avoids losing ground.

## Paths: the nights before the lock

Offers come nightly, auctions close nightly, the lock comes after N nights.
The same program over the nights (decisions per night, cash carried from one
to the next) chooses sequences: sell tonight and buy tomorrow, bid now and
sell after the auctions if the bid wins. Today's board decides one night
and holds no plan for the next.

## What it does not fix

The forecasts. Eight jornadas of backtest cannot separate close settings;
lambda_end and the variance weight are the only constants left, and the
backtest (grading.py money, bids held between the timers' runs) judges
them, evidence growing a jornada a week.

## Order

1. The program for one night, lambda shown, on a branch; golden boards and
   the backtest against the current plan. Done 2026-10-28 (decide.program):
   backtest, jornadas 2-9, 352 points and 456M of squad value against the
   old plan step's 325 and 336M (never moving: 395).
2. Bids as levels with their odds; the money rule on every outcome. Done
   (decide.options): a bid holds his slot the share of the time it wins,
   the budget holds if every bid wins, the squad if every one loses.
3. The chance of first by the variance sweep. Done (decide.KAPPAS).
   Backtest of 2 and 3 together, jornadas 2-9: 414 points, no jornada
   scoring nothing, 374M of squad value: the first plan to beat never
   moving (395); the one-night program alone made 352.
4. The nights before the lock: by re-solving, not yet by one program. The
   board is solved again at every run (10:00, the 21:45 flip, after the
   close), each time from what the night before left: a rolling horizon,
   the usual answer to planning under uncertainty that resolves as it goes
   (model predictive control). Planning the nights in one program needs the
   outcomes of tonight's auctions as scenarios (a tree, every win or loss
   of every bid), and tomorrow's market is not known tonight: the gain over
   re-solving is the sequences re-solving cannot see, such as a sale held
   for tomorrow's offer to pay for a bid tonight. The waiting values
   (sim_waiting: what each later night's offer is worth) already price
   that one. To revisit if the backtest shows money left on the table
   between runs.

Sources: Ramezani & Dinh, FPL team selection as a mixed-integer program
(arXiv 2505.02170); Beal, Norman & Ramchurn, daily fantasy NFL by integer
programming (IJCSS 2020); Hunter, Vielma & Zaman (arXiv 1604.01455); a
chance-constrained model for football transfer decisions (arXiv 1911.04689).
