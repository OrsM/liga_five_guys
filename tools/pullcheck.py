"""The report job's pull (tools/lfg-run) must keep a merge among the commits it replays: a plain
--rebase flattened one on 2026-11-05 and re-resolved four files. Read-only: scratch repos in a temp dir."""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

RUN = Path(__file__).with_name("lfg-run")


def pull_args() -> list[str]:
    """The arguments of the git pull in lfg-run, as written there."""
    m = re.search(r"^\s*if ! git pull ([^2\n]*?)\s*2>", RUN.read_text(), re.M)
    assert m, "lfg-run has no git pull"
    return m.group(1).split()


def _selftest() -> None:
    env = {"GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@x", "GIT_COMMITTER_NAME": "a",
           "GIT_COMMITTER_EMAIL": "a@x", "PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}

    def git(cwd: Path, *a: str) -> str:
        return subprocess.run(["git", *a], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        git(t, "init", "-q", "-b", "main", "seed")
        (t / "seed/report").write_text("r0")
        (t / "seed/f").write_text("base")
        git(t / "seed", "add", ".")
        git(t / "seed", "commit", "-qm", "base")
        git(t, "clone", "-q", "--bare", "seed", "remote.git")
        git(t, "clone", "-q", "remote.git", "work")
        git(t, "clone", "-q", "remote.git", "other")
        w, o = t / "work", t / "other"
        (o / "report").write_text("r1")                       # a report the box pushed from elsewhere
        git(o, "commit", "-qam", "report: other")
        git(o, "push", "-q")
        git(w, "checkout", "-q", "-b", "feat")                 # unpushed work: a real merge, then this run's report
        (w / "a").write_text("a")
        (w / "f").write_text("b")
        git(w, "add", ".")
        git(w, "commit", "-qm", "feat")
        git(w, "checkout", "-q", "main")
        (w / "c").write_text("c")
        git(w, "add", "c")
        git(w, "commit", "-qm", "main side")
        git(w, "merge", "-q", "--no-ff", "feat", "-m", "merge feat")
        (w / "report").write_text("r2")
        git(w, "commit", "-qam", "report: run")
        git(w, "pull", *pull_args(), "-q")
        merges = git(w, "log", "--merges", "--format=%s").split("\n")
        assert "merge feat" in merges, "the pull flattened an unpushed merge: %s" % pull_args()
        assert (w / "report").read_text() == "r2", "this run's report wins"
    print("pullcheck self-test OK")


if __name__ == "__main__":
    _selftest()
