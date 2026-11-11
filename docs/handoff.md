# Liga Five Guys: handoff

State as of 2026-11-11, afternoon. Repo: github.com/OrsM/liga_five_guys. Main's working tree at ~/claude_projects/liga_five_guys is production (the systemd timers run from it). Site: ~/claude_projects/website, served at notes.lemonworlds.com/fantasy.

## Where we stand

Miguel is 1st: 639 points against 613, 538, 486, 485. The runner-up has a far larger team value (699M against 451M) and Miguel's cash is -39M, so the lead is held with less capital.

Main is pushed to origin. The first live run on the merged code is the 21:45 run of 2026-11-11; it re-applies the schema and drops the `dec_offer` table. Principles and the way issues are worked are in `CLAUDE.md`; the order of work is in the four GitHub milestones.

## What changed on 2026-11-11

| Change | Effect |
|---|---|
| `dec_offer` table and view removed (no reader) | board stage 26.7 s to 16.6 s on a copy of the day's data, with the next row |
| `player_facts` read once a board, as a `Fact` with named fields | same measurement |
| `finish` and `band` placeholders removed (#37, closed) | `decisions.json` otherwise identical on the same snapshot |
| Drift alert is one line (#36, closed) | six lines became one |
| `ffcore/db.py` self-test removes its scratch database | this was the source of the leaked `/tmp/tmp*` dirs |

The full suite (38 suites) passed on the merged code. The before/after board comparison used `tools/lfg-dry-run board` on a backup copy of the live database.

## The design standard

Miguel's guidance: the code complies with the Structure and Interpretation of Computer Programs approach.

- **Already met:** module frontiers in SQL (`tools/frontiers.py`: ingest, forecast, decision, board, grading; 0 crossings); `Club`, `Approach`, `Lineup`, `Built` are named types; `eleven.py` is the one judge of a team.
- **Not met:** rows cross from SQL into Python as bare tuples read by position (#39 to #41); the board both fetches and computes (#42); neither rule is checked mechanically (#43); `assemble.py` belongs to no module (#44).

## Corrections to the previous handoff

- A full run takes about 44 s, not 145 s.
- `cleanup.sh` (#35) does not exist anywhere.
- The site's fantasy-lineup commit 34fba19 is already on the website's main; only `./deploy.sh` remains (#34).
- The auction overconfidence (#33) is not noise. We bid on what rivals want: a rival wins 46% of the auctions we bid on (36 of 78) against 4% to 30% of those we do not. The curve's cells are mostly unsold players, so its chance is for the wrong population.
- The code carries about 130 docstrings despite the "no docstrings" agreement (#56).

## Open issues

**Structure (in this order)**

| # | Issue | Size |
|---|---|---|
| 39 | Typed decision inputs: one named type per `dec_*` table | main session |
| 40 | `decision.py` reads inputs by field name | small model |
| 41 | `board.py` reads inputs by field name | small model |
| 42 | Board has no SQL: selectors from the layer below | main session |
| 43 | `frontiers.py` enforces both rules | small model |
| 44 | `assemble.py`: assign or split (note first) | discuss |

**Auction and bidding**

| # | Issue | Size |
|---|---|---|
| 33 | Parent: overconfident on our own bids | |
| 49 | Formulation: top rival bid given demand | discuss, no code |
| 50 | Score the forecast on wanted players | main session |
| 51 | A look close to the close (evidence first) | discuss |
| 52 | Bid objective: millions against points | discuss, no code |
| 53 | Board must not advise lowering a standing bid unsaid (bug) | main session |
| 54 | Record our own bid changes | small model |

**Speed and storage**

| # | Issue | Size |
|---|---|---|
| 45 | Remove `sale_odds`, `seen.fetches`, `proceeds` (3.2 s, no reader) | main session |
| 46 | Grade stage: 6 s to say "grades stand" | main session |
| 47 | Board stage under 10 s | main session |
| 48 | `forecast_cell` is half the database: retention rule | discuss |

**Housekeeping**

| # | Issue | Size |
|---|---|---|
| 34 | Run `./deploy.sh` for the site, check over a VPN | Miguel's hand |
| 35 | Remove finished worktrees and branches | needs keep/drop below |
| 38 | Validate live: accept-timer latency, targets watch, if-you-lose line | open, untouched |
| 55 | Remote branches: which are merged | small model |
| 56 | Docstring rule | discuss |

## Waiting on Miguel

1. Whether the SICP reading above is the intended one, or the stricter "each layer reads only the layer directly below".
2. The word to start #39 (approved, deferred).
3. `./deploy.sh` for the site (#34).

Settled on 2026-11-11: main pushed; `/tmp` cleared; the `P_DECAY` commit and the `docs/plan-act.md` draft are dropped (the draft is kept as the local tag `archive/plan-act-draft`); nobody knows who lowered the Javi Rodriguez bid, so #53 starts by finding whether any code path can change a bid.

## How to hand work to small models

It worked on 2026-11-11 with six agents under these conditions.

- One file (or two) per agent, with scopes that do not overlap.
- Numbered steps, the exact strings to find and replace, the exact verify commands and the expected output.
- "Stop and report" as an accepted outcome.
- Each in its own worktree; `TMPDIR` under `~/.cache`, because `/tmp` is a small tmpfs.
- Read-only agents open the live database with `file:data/lfg.db?mode=ro` and `ffcore.db.functions(db)`.
- The main session reviews every diff against the design standard before merging. Two of three diffs needed a correction: positional tuple access, and a test in another file that the change broke.

## Working agreements

- Discuss before code; build when told to go. On a core mathematical problem, agree the formulation first.
- SICP standard: named data with selectors, layers, composition, one concept in one place.
- No duplication: extend the canonical code; win chance is computed only in `auction.py`.
- Tests assert general behaviour and invariants, not one fixture's wording.
- Time the board on a dry-run copy before each commit; a slowdown is a regression.
- Main is production: verify on a copy, delete scratch copies afterwards.

## First actions for whoever picks this up

1. Check the 21:45 run's log and health line: it is the first live run on the merged code.
2. Start #39, then hand #40 and #41 to small models.
3. Write the #49 formulation and bring it to Miguel before any model code.
4. Reproduce #53 on a dry-run copy.
