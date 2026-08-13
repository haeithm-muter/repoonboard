"""Classify a pinned tour against the current state of the repository.

Two kinds of staleness, and the second is the one that matters:

1. A station's own file moved, was deleted, or changed under the lines its
   questions point at. Every documentation tool notices this eventually.
2. A file has appeared, or grown, that now outranks a file in the tour. No
   documentation tool notices this, because it requires recomputing the
   ranking rather than diffing the text — and it is the case that actually
   makes an onboarding path wrong: not that a station is inaccurate, but that
   the path is now missing the thing a newcomer most needs to read.

Everything here is a pure function of data gathered elsewhere. Nothing in this
module shells out, so every verdict can be tested against synthetic input, and
every count the CLI prints is derived from these structures rather than
asserted alongside them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class State(StrEnum):
    FRESH = "fresh"
    LINES_SHIFTED = "lines shifted"
    ANSWERS_CHANGED = "answers changed"
    MOVED = "moved"
    DELETED = "deleted"

    @property
    def is_stale(self) -> bool:
        """A station needing attention before the tour is trustworthy again.

        `LINES_SHIFTED` counts: the prose is still true, but every line number
        the reader is sent to is now wrong, which in an executable tour is a
        broken link rather than a cosmetic drift.
        """
        return self is not State.FRESH


@dataclass(frozen=True)
class StationVerdict:
    path: str
    state: State
    detail: str
    new_path: str | None = None


@dataclass
class Report:
    """The complete result of one `check` run."""

    pinned: str
    head: str
    verdicts: list[StationVerdict] = field(default_factory=list)
    now_outranking: list[str] = field(default_factory=list)
    no_longer_selected: list[str] = field(default_factory=list)

    @property
    def stale(self) -> list[StationVerdict]:
        return [verdict for verdict in self.verdicts if verdict.state.is_stale]

    @property
    def is_current(self) -> bool:
        return not self.stale and not self.now_outranking


def cited_ranges(station: dict) -> list[tuple[int, int]]:
    """Every line range a station sends a reader to, from a stored tour.

    The anchor counts alongside the answer locations: a tour step that opens
    on the wrong line is broken even when all of its answers survive. Written
    to tolerate a partial record rather than raise, so a hand-edited or older
    `stations.json` degrades to fewer checks instead of a crash.
    """
    ranges: list[tuple[int, int]] = []

    anchor = station.get("anchor_line")
    if isinstance(anchor, int):
        ranges.append((anchor, anchor))

    for question in station.get("questions") or []:
        location = (question or {}).get("answer_location") or {}
        start, end = location.get("start_line"), location.get("end_line")
        if isinstance(start, int) and isinstance(end, int) and start <= end:
            ranges.append((start, end))

    return ranges


def _overlaps(ranges: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(
        hunk_start <= end and start <= hunk_end for hunk_start, hunk_end in ranges
    )


def classify_station(
    path: str,
    cited_ranges: list[tuple[int, int]],
    change: tuple[str, str | None] | None,
    changed_ranges: list[tuple[int, int]],
) -> StationVerdict:
    """Decide what happened to one station.

    `cited_ranges` are every line range the station points a reader at — its
    anchor and each answer location — expressed against the pinned commit.
    """
    if change is None:
        return StationVerdict(path, State.FRESH, "file unchanged since the tour was pinned")

    status, new_path = change

    if status == "D":
        return StationVerdict(path, State.DELETED, "file no longer exists")

    if status == "R":
        return StationVerdict(
            path,
            State.MOVED,
            f"file was renamed to {new_path}",
            new_path=new_path,
        )

    if not changed_ranges:
        return StationVerdict(path, State.FRESH, "file unchanged since the tour was pinned")

    touched = [
        (start, end) for start, end in cited_ranges if _overlaps(changed_ranges, start, end)
    ]
    if touched:
        listed = ", ".join(f"{start}-{end}" for start, end in touched)
        return StationVerdict(
            path,
            State.ANSWERS_CHANGED,
            f"the code changed under line(s) {listed}, which this station's "
            "questions send the reader to",
        )

    earliest_cited = min((start for start, _ in cited_ranges), default=None)
    if earliest_cited is not None and any(
        hunk_start < earliest_cited for hunk_start, _ in changed_ranges
    ):
        return StationVerdict(
            path,
            State.LINES_SHIFTED,
            "the file changed above the cited lines, so the recorded line "
            "numbers no longer point where they did",
        )

    return StationVerdict(
        path,
        State.FRESH,
        "the file changed, but not at or above any line this station cites",
    )


def build_report(
    pinned: str,
    head: str,
    stations: list[tuple[str, list[tuple[int, int]]]],
    changes: dict[str, tuple[str, str | None]],
    ranges_for: dict[str, list[tuple[int, int]]],
    current_selection: list[str],
) -> Report:
    """Assemble one run's verdicts.

    `current_selection` is what `plan` would choose from the repository as it
    stands now. Comparing it with the tour is what surfaces the second kind of
    staleness — a file that has become more important than one already in the
    path.
    """
    report = Report(pinned=pinned, head=head)

    for path, cited in stations:
        report.verdicts.append(
            classify_station(path, cited, changes.get(path), ranges_for.get(path, []))
        )

    toured = {verdict.path for verdict in report.verdicts}
    # A renamed station is still the same file, so its new path must not be
    # reported as a newcomer that outranks the tour.
    toured.update(v.new_path for v in report.verdicts if v.new_path)

    report.now_outranking = [path for path in current_selection if path not in toured]
    report.no_longer_selected = [
        verdict.path
        for verdict in report.verdicts
        if verdict.path not in current_selection
        and (verdict.new_path or verdict.path) not in current_selection
        and verdict.state is not State.DELETED
    ]
    return report
