"""Git-derived signals.

Churn is the signal competing tools ignore: a file changed forty times in a
year is load-bearing whatever the import graph says about it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class NotAGitRepository(RuntimeError):
    """Raised when churn is requested for a directory without git history."""


def _run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        raise NotAGitRepository(result.stderr.strip() or "git command failed")
    return result.stdout


def head_commit(root: Path) -> str:
    """Full hash of HEAD. Every generated tour is pinned to this."""
    return _run_git(root, "rev-parse", "HEAD").strip()


def churn(root: Path, months: int = 12) -> dict[str, int]:
    """Commits touching each file within the given window.

    Returns a mapping of posix relative path to commit count. Files never
    touched in the window are simply absent.
    """
    output = _run_git(
        root,
        "log",
        f"--since={months}.months.ago",
        "--name-only",
        "--pretty=format:",
        "--no-merges",
    )

    counts: dict[str, int] = {}
    for line in output.splitlines():
        name = line.strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def is_git_repository(root: Path) -> bool:
    try:
        _run_git(root, "rev-parse", "--git-dir")
    except (NotAGitRepository, FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return True
