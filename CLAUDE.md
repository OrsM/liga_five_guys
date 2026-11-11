# liga_five_guys

A forecasting-and-decision system for a LaLiga Fantasy league. Start with `docs/handoff.md` (state, open issues) and `docs/notes/lfg.md` (rules, model, architecture).

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
- Milestones give the order. Within a milestone, lower numbers first unless "Depends on" says otherwise.
- A commit names what is now true, in the repo's style, and cites its issue. Close an issue only with the evidence its "Done when" asks for.

## Checks

```
bash tools/selftests.sh                              # 38 suites, mypy, ruff
~/.local/bin/uv run --frozen python tools/frontiers.py   # 0 crossings
tools/lfg-dry-run board                              # timing and decisions.json on a copy
```

Run Python with `PYTHONPATH=src ~/.local/bin/uv run --frozen python <file> --selftest`.
