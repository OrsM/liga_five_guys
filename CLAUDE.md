# liga_five_guys

A forecasting-and-decision system for a LaLiga Fantasy league. State and open work live in the GitHub issues (`gh issue list --state open --limit 40`; milestones give the order); there is no handoff file. Rules, model and architecture are in `docs/notes/lfg.md`.

## This tree is production

- The systemd timers run from this working tree and each run commits `report:` commits. Never leave it red or dirty.
- Code changes are made in a git worktree and merged when green. Verify on a copy: `tools/lfg-dry-run board` (scratch under `~/.cache`, never `/tmp`, never `data/`). Delete scratch copies when done.
- The live database is read only through `file:data/lfg.db?mode=ro` with `ffcore.db.functions(db)`.
- Do not touch the account (bids, accepting offers) without Miguel's explicit go.

## Principles

1. **Discuss before code.** Build when told to go. On a mathematical problem, the formulation is written and agreed first.
2. **Structure and Interpretation of Computer Programs.**
   - Data has constructors and selectors. No reading a row by position (`r[1]`, `k, _, m, *_`); use a named type.
   - Layers: ingest, forecast, decision, board, grading. A layer reads only what an earlier layer exports (`tools/frontiers.py` checks the SQL names and the Python reads of them; 0 crossings). Derived facts are Python selectors over the sweep's World (`ffcore/state.py`, one sweep over `ffcore/observations.py`); no SQL view computes one. SQLite holds raw `src_*` observations and records (`runs`, `run_measures`, `params`, `measure_log`, `grade_cell`, `auction_*`, `dec_opportunity`) only, plus the crosswalk and `parser_health`.
   - A procedure is a black box named for what it computes. Compose small general procedures.
   - A docstring states the contract (what goes in, what comes out, what must hold), nothing else. No other comments.
3. **One concept, one place.** Extend the canonical code; no second copy, no ad-hoc script with its own matching. Win chance is computed only in `ffcore/auction.py`, from the fixed table `ffcore/auction_table.py` (regenerated only by `tools/auction_table.py`, blessed in the same commit); team quality is judged only by `eleven.py`; an optimisation has one objective in one place.
4. **Simplest correct change.** Old code is not the spec; old numbers are a score to beat, not to match.
5. **Tests assert general behaviour and invariants**, not one fixture's wording. Untested changes do not go live. A change that moves a decision or a feature updates the golden files in the same commit, with the reason.
6. **Profile before commit.** Time the board on a dry-run copy; a slowdown is a regression.
7. **Judge by decisions.** A model change is for better buys, sales and elevens, not a narrower error.

## Working by issues

- One issue is one change with one owner of each file it touches. Every issue states Goal, Scope (files), Done when (a command and its expected result), Not in scope.
- Labels: `needs-discussion` means no code until Miguel has agreed the written formulation; `haiku-ready` means specified tightly enough for a small model.
- `waiting-on-miguel` means blocked on a decision or action only Miguel can take: ask, do not start. `gh issue list --label waiting-on-miguel` is the list.
- What a session learns goes on the issue it belongs to, as a dated comment with the evidence; something with no issue gets one.
- Milestones give the order. Within a milestone, lower numbers first unless "Depends on" says otherwise.
- A commit names what is now true, in the repo's style, and cites its issue. Close an issue only with the evidence its "Done when" asks for.

## Handing an issue to an agent

- The issue carries the brief: the file (one, or two) it may touch, numbered steps, the exact verify commands and the output expected. "Stop and report" is an accepted outcome.
- Scopes of agents running together do not overlap. Each has its own worktree, `TMPDIR` under `~/.cache` (`/tmp` is a small tmpfs) and its own `LFG_SCRATCH`.
- Agent worktrees start from `origin/main`: push main before spawning.
- Pin the time with `LFG_NOW` so `decisions.json` can be compared byte for byte.
- `tools/lfg-dry-run` does not copy the database: seed `$LFG_SCRATCH/data/lfg.db` first with a read-only backup of the live one (`sqlite3` `backup()` from a `mode=ro` connection), and delete it afterwards.
- On a copy run `tools/lfg-dry-run board grade health`, not `parse`: a copy reparses every snapshot (9 minutes).
- Read-only agents open the live database as above (`mode=ro`).
- Never point a tool that calls `ffcore.db.connect()` (`tools/sql.py`, `tools/q.py`, `grading.py`, `run.py`) at the live `data/`: `connect()` migrates the schema, and a migration that drops tables drops them there (2026-11-17). Use a `mode=ro` backup under `~/.cache`.
- Three levels. Small models do the issues, in swarms. A mid-size pickup agent reads their reports and diffs against the brief and the principles and sorts each issue: good, another small-model turn (it writes the corrected brief), or blocked. The main session writes the briefs, merges what is good, and takes what is blocked to Miguel.
- Before a brief is posted, the main session runs its verify command on main and checks every fact it names (a name exists, a file is where it says). A wrong brief costs a whole turn.
- At most two small-model turns on an issue; then it is blocked.
- The main session reviews every diff against the principles before merging; the pickup's verdict is advice.
- Merge under the run lock: `flock ~/.local/state/lfg/run.lock git merge --ff-only <branch>`.
- A live run may be triggered at any time to unblock a measurement (`systemctl --user start lfg.service`); no need to wait for a timer.

## Checks

```
tools/capped bash tools/selftests.sh                 # 48 suites, mypy, ruff
tools/capped ~/.local/bin/uv run --frozen python tools/frontiers.py   # 0 crossings
tools/capped env PYTHONPATH=src ~/.local/bin/uv run --frozen python tools/golden.py --selftest   # decisions.json and key features at pinned cuts
tools/lfg-rules                                      # the sale-rule table over every replay cut (~3 min, scratch copy of the live db)
tools/lfg-dry-run board                              # timing and decisions.json on a copy
```

Every heavy job (selftests, sweeps, replays, dry runs) goes through `tools/capped`, one at a time: the box has 3.8 GB and an uncapped sweep took down Remote Control. Pin `PYTHONHASHSEED=0`.
Before a merge that changes a fact: `golden.py --live` on a scratch copy of the database; a change to a golden file is `golden.py bless` with the reason in the commit.
`oracle-sql` (git tag) is the last SQL-view implementation, kept as the historic proof; it is not run any more.
Replay cuts are the app snapshot stamps (`loader.replay_cuts(obs, since)`).

Run Python with `PYTHONPATH=src ~/.local/bin/uv run --frozen python <file> --selftest`.
