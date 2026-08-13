"""Git-derived signals.

Churn is the signal competing tools ignore: a file changed forty times in a
year is load-bearing whatever the import graph says about it.
"""

from __future__ import annotations

import re
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


def commit_exists(root: Path, sha: str) -> bool:
    """True when the pinned commit is actually reachable in this clone.

    A tour can outlive the history it was pinned to — a rebase, a force-push,
    or a shallow clone all remove the commit. Saying so is better than
    silently diffing against something else.
    """
    try:
        _run_git(root, "cat-file", "-e", f"{sha}^{{commit}}")
    except NotAGitRepository:
        return False
    return True


def changed_paths(root: Path, base: str) -> dict[str, tuple[str, str | None]]:
    """What happened to every file between `base` and the working tree.

    Returns a mapping of the path *as it was at `base`* to a (status, new path)
    pair, where status is one of "M", "A", "D" or "R". Keying on the old path
    is what lets a station recorded against the pinned commit be looked up at
    all — after a rename, its own path no longer exists.
    """
    output = _run_git(root, "diff", "--name-status", "-M", base)

    changes: dict[str, tuple[str, str | None]] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        code = fields[0]
        if code.startswith("R") and len(fields) >= 3:
            changes[fields[1]] = ("R", fields[2])
        elif len(fields) >= 2:
            changes[fields[1]] = (code[0], None)
    return changes


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def changed_line_ranges(root: Path, base: str, path: str) -> list[tuple[int, int]]:
    """Line ranges of `path` that changed since `base`, in `base` coordinates.

    Ranges are expressed against the *old* file because that is the coordinate
    system a pinned tour's answer locations live in. A pure insertion has no
    old lines, so it is reported as the zero-width point it follows — text
    appearing in the middle of a cited range does change what a reader finds
    there, even though no old line was touched.
    """
    output = _run_git(root, "diff", "-U0", base, "--", path)

    ranges: list[tuple[int, int]] = []
    for line in output.splitlines():
        match = _HUNK.match(line)
        if match is None:
            continue
        start = int(match.group(1))
        count = 1 if match.group(2) is None else int(match.group(2))
        if count == 0:
            ranges.append((start, start))  # insertion after old line `start`
        else:
            ranges.append((start, start + count - 1))
    return ranges


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
