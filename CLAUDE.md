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
   - Layers: ingest, forecast, decision, board, grading. A layer reads only what an earlier layer exports (`tools/frontiers.py` checks it; 0 crossings).
   - A procedure is a black box named for what it computes. Compose small general procedures.
3. **One concept, one place.** Extend the canonical code; no second copy, no ad-hoc script with its own matching. Win chance is computed only in `ffcore/auction.py`; team quality is judged only by `eleven.py`; an optimisation has one objective in one place.
4. **Simplest correct change.** Old code is not the spec; old numbers are a score to beat, not to match.
5. **Tests assert general behaviour and invariants**, not one fixture's wording. Untested changes do not go live.
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
- Pin the time with `LFG_NOW` so `decisions.json` can be compared byte for byte; a fresh pin is needed for the board to rebuild `seen`.
- On a copy run `tools/lfg-dry-run board grade health`, not `parse`: a copy reparses every snapshot (9 minutes).
- Read-only agents open the live database as above (`mode=ro`).
- The main session reviews every agent diff against the principles before merging.
- Merge under the run lock: `flock ~/.local/state/lfg/run.lock git merge --ff-only <branch>`.
- A live run may be triggered at any time to unblock a measurement (`systemctl --user start lfg.service`); no need to wait for a timer.

## Checks

```
bash tools/selftests.sh                              # 38 suites, mypy, ruff
~/.local/bin/uv run --frozen python tools/frontiers.py   # 0 crossings
tools/lfg-dry-run board                              # timing and decisions.json on a copy
```

Run Python with `PYTHONPATH=src ~/.local/bin/uv run --frozen python <file> --selftest`.
