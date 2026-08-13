"""Ground truth assembly and scoring.

The point of this module is that the ground truth is not the author's opinion
of what matters in a repository. It is the union of three sources that exist
independently of this project:

1. files a maintainer named in CONTRIBUTING.md,
2. the ten files the team commits to most,
3. files referenced from issues labelled "good first issue".

Each source is weak on its own and wrong in a different direction — a
CONTRIBUTING file may list only build scripts, churn favours whatever is
being actively rewritten, and beginner issues cluster on the periphery. The
union is used precisely because no single one of them is trustworthy.

Everything here is pure. Fetching the raw material — cloning, reading git
history, calling the GitHub API — happens in `eval/`, so the scoring can be
tested without a network.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from enum import StrEnum


class Source(StrEnum):
    DOCUMENTATION = "documentation"
    CHURN = "churn"
    GOOD_FIRST_ISSUE = "good_first_issue"


# A source naming more than this share of a repository has no discriminating
# power, however correct each individual entry is. Measured case: scrapy
# documents nearly every module in its Sphinx tree, so extracting module
# references yields 48.6% of the repository — against which picking six files
# at random scores about 0.49. Such a source is recorded and reported, but
# kept out of the union, because a ground truth that names half the code
# cannot tell a good selection from a lucky one.
MAX_SOURCE_SHARE = 0.25


# A path-like token: at least one directory separator, ending in a source
# extension. Deliberately strict — a bare "utils.py" mentioned in prose is far
# more likely to be an example than a real reference, and a false entry in the
# ground truth silently inflates every score computed against it.
_PATH_TOKEN = re.compile(r"[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+\.(?:py|ts|tsx|js|jsx|mjs|cjs)")


@dataclass
class GroundTruth:
    """The union, plus which source each path came from."""

    paths: set[str] = field(default_factory=set)
    by_source: dict[Source, set[str]] = field(default_factory=dict)
    rejected: dict[Source, float] = field(default_factory=dict)

    def add(self, source: Source, found: set[str], repository_size: int = 0) -> None:
        """Record a source's contribution, unless it is too broad to mean anything.

        `repository_size` of 0 disables the selectivity guard, which is what
        callers scoring a fixed hand-written truth want.
        """
        found = set(found)
        if repository_size and len(found) / repository_size > MAX_SOURCE_SHARE:
            self.rejected[source] = len(found) / repository_size
            self.by_source[source] = set()
            return
        self.by_source[source] = found
        self.paths |= found

    def contributing_sources(self) -> list[Source]:
        """Sources that actually yielded at least one path.

        Reported rather than assumed: a repository with no `good first issue`
        label produces a two-source union, and a result computed against it
        should say so.
        """
        return [source for source, found in self.by_source.items() if found]


def extract_paths(text: str, known: frozenset[str]) -> set[str]:
    """Repository paths referenced in free text.

    Only paths that exist in the pinned tree survive. Anything else is prose,
    a path from another project, or a file that has since been deleted — none
    of which belong in a ground truth used to score a specific commit.
    """
    found: set[str] = set()
    for raw in _PATH_TOKEN.findall(text or ""):
        candidate = posixpath.normpath(raw.strip().strip(".,;:)("))
        if candidate in known:
            found.add(candidate)
            continue
        # Tolerate a leading "./" or a repo-name prefix ("scrapy/scrapy/x.py").
        parts = candidate.split("/")
        for start in range(1, len(parts)):
            suffix = "/".join(parts[start:])
            if suffix in known:
                found.add(suffix)
                break
    return found


def top_committed(commit_counts: dict[str, int], known: frozenset[str], limit: int = 10) -> set[str]:
    """The `limit` most-committed files that are still source files at the pin.

    Ties are broken by path so the result does not depend on dict ordering;
    an evaluation that changed between runs would be worthless.
    """
    eligible = [(path, count) for path, count in commit_counts.items() if path in known]
    eligible.sort(key=lambda pair: (-pair[1], pair[0]))
    return {path for path, _ in eligible[:limit]}


_DOTTED = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*){1,6})\b")


def extract_dotted(text: str, known: frozenset[str]) -> set[str]:
    """Resolve dotted module references — `scrapy.core.engine` — to files.

    Python documentation names modules, not paths, so the slash-requiring path
    extractor cannot see them at all. Only references that resolve to a real
    file at the pin are kept, and the caller is expected to apply the
    selectivity guard: a project that documents every module exhaustively
    produces a reference set too broad to score against.
    """
    found: set[str] = set()
    for match in _DOTTED.findall(text or ""):
        parts = match.split(".")
        for start in range(len(parts) - 1):
            stem = "/".join(parts[start:])
            for candidate in (f"{stem}.py", f"{stem}/__init__.py", f"src/{stem}.py",
                              f"src/{stem}/__init__.py"):
                if candidate in known:
                    found.add(candidate)
                    break
    return found


def build_ground_truth(
    documentation_text: str,
    commit_counts: dict[str, int],
    issue_texts: list[str],
    known: frozenset[str],
    churn_limit: int = 10,
) -> GroundTruth:
    """Assemble the union from the three sources.

    `documentation_text` replaces the CONTRIBUTING.md source of the original
    design, which was measured to yield nothing on any of the four evaluation
    repositories: contributing guides describe process, not architecture.
    """
    truth = GroundTruth()
    size = len(known)

    documented = extract_paths(documentation_text, known)
    documented |= extract_dotted(documentation_text, known)
    truth.add(Source.DOCUMENTATION, documented, repository_size=size)

    truth.add(Source.CHURN, top_committed(commit_counts, known, churn_limit))
    truth.add(
        Source.GOOD_FIRST_ISSUE,
        extract_paths("\n".join(issue_texts), known),
        repository_size=size,
    )
    return truth


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Score:
    variant: str
    repository: str
    hits: int
    considered: int
    ground_truth_size: int

    @property
    def precision(self) -> float:
        return self.hits / self.considered if self.considered else 0.0


def precision_at_k(
    predicted: list[str], truth: set[str], k: int = 6, variant: str = "", repository: str = ""
) -> Score:
    """How many of the first k predicted files are in the ground truth.

    `considered` is `min(k, len(predicted))`, not k. A tour that produced four
    stations is scored on four; dividing by six instead would punish it for
    stations it never claimed to have, and quietly mix two different failures
    into one number.
    """
    head = predicted[:k]
    return Score(
        variant=variant,
        repository=repository,
        hits=sum(1 for path in head if path in truth),
        considered=len(head),
        ground_truth_size=len(truth),
    )


def mean_precision(scores: list[Score]) -> float:
    """Unweighted mean across repositories.

    Unweighted on purpose: each repository is one architecture, and weighting
    by ground-truth size would let the largest repository decide the headline.
    """
    if not scores:
        return 0.0
    return sum(score.precision for score in scores) / len(scores)
