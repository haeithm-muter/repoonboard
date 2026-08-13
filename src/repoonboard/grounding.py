"""The grounding gate.

Nothing generated reaches a tour without passing through here. The gate answers
one question — *is every checkable claim in this content actually true of the
repository?* — and it answers it structurally, never by asking a model whether
it told the truth.

What the gate can decide, it decides:

- a path named must exist in the repository
- a code identifier named must appear in the text the model was shown
- an answer location must lie inside the file, inside the shown region, and
  not on an import line
- there must be two or three questions

What the gate cannot decide it does not pretend to. Prose that is fluent,
grounded in real symbols, and wrong about *behaviour* will pass. The gate
bounds hallucination to the vocabulary of the file; it does not verify
semantics. That limit is recorded in NOTES.md rather than papered over.

The convention that makes identifier checking decidable: every code identifier
in generated prose must be written in backticks. An unbackticked identifier is
invisible to the gate, so the prompt requires backticks and the gate treats
every backticked token as a claim to be checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .discovery import LANGUAGE_BY_SUFFIX
from .explanation import StationExplanation
from .snippets import Snippet

MIN_QUESTIONS = 2
MAX_QUESTIONS = 3

_BACKTICKED = re.compile(r"`([^`\n]+)`")

# Tokens that are prose rather than a claim about this repository, even when a
# model puts them in backticks.
_ALLOWED_BARE_TOKENS = frozenset(
    {
        "true",
        "false",
        "none",
        "null",
        "undefined",
        "python",
        "typescript",
        "javascript",
    }
)


@dataclass(frozen=True)
class Rejection:
    """One decidable claim that turned out to be false."""

    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@dataclass(frozen=True)
class GateResult:
    rejections: tuple[Rejection, ...]

    @property
    def ok(self) -> bool:
        return not self.rejections

    def as_feedback(self) -> str:
        """Rejection list phrased for a retry prompt."""
        return "\n".join(f"- {rejection}" for rejection in self.rejections)


def _looks_like_path(token: str) -> bool:
    if "/" in token or "\\" in token:
        return True
    return Path(token).suffix.lower() in LANGUAGE_BY_SUFFIX


def _normalise_identifier(token: str) -> str:
    """Strip the decoration a model adds around a name it is citing."""
    cleaned = token.strip()
    cleaned = cleaned.removesuffix("()")
    return cleaned.strip().strip(".,;:")


def check(
    explanation: StationExplanation,
    snippet: Snippet,
    known_paths: frozenset[str],
) -> GateResult:
    """Verify one station's generated content against the repository."""
    rejections: list[Rejection] = []

    rejections.extend(_check_station_path(explanation, snippet, known_paths))
    rejections.extend(_check_claims(explanation, snippet, known_paths))
    rejections.extend(_check_questions(explanation, snippet))

    return GateResult(tuple(rejections))


def _check_station_path(
    explanation: StationExplanation, snippet: Snippet, known_paths: frozenset[str]
) -> list[Rejection]:
    if explanation.path != snippet.path.as_posix():
        return [
            Rejection(
                "wrong_station",
                f"content is labelled {explanation.path!r} but was generated for "
                f"{snippet.path.as_posix()!r}",
            )
        ]
    if explanation.path not in known_paths:
        return [Rejection("unknown_path", f"{explanation.path!r} is not a file in this repository")]
    return []


def _check_claims(
    explanation: StationExplanation, snippet: Snippet, known_paths: frozenset[str]
) -> list[Rejection]:
    """Every backticked token must be a real path or appear in the shown text."""
    rejections: list[Rejection] = []
    prose = f"{explanation.summary}\n{explanation.why_it_matters}\n" + "\n".join(
        question.prompt for question in explanation.questions
    )

    for raw in _BACKTICKED.findall(prose):
        token = _normalise_identifier(raw)
        if not token or token.lower() in _ALLOWED_BARE_TOKENS:
            continue

        if _looks_like_path(token):
            if token.replace("\\", "/") not in known_paths:
                rejections.append(
                    Rejection("unknown_path", f"{token!r} is not a file in this repository")
                )
            continue

        missing = _missing_segments(token, snippet.text)
        if missing:
            rejections.append(
                Rejection(
                    "unknown_symbol",
                    f"{token!r} does not appear in the shown lines of "
                    f"{snippet.path.as_posix()}",
                )
            )

    return rejections


def _missing_segments(token: str, shown: str) -> list[str]:
    """Segments of a token that never appear in the shown text.

    Member access is checked part by part. A model writing `UserCache.get` is
    citing two real names that the source never places side by side, so a
    literal search would reject correct content and drive pointless retries.
    Checking segments still bounds the model to the file's own vocabulary:
    `UserCache.deleteEverything` is rejected on its second segment.
    """
    segments = [part for part in token.split(".") if part]
    if not segments:
        return []
    return [segment for segment in segments if segment not in shown]


def _check_questions(explanation: StationExplanation, snippet: Snippet) -> list[Rejection]:
    rejections: list[Rejection] = []
    count = len(explanation.questions)
    if not MIN_QUESTIONS <= count <= MAX_QUESTIONS:
        rejections.append(
            Rejection(
                "question_count",
                f"expected {MIN_QUESTIONS}-{MAX_QUESTIONS} questions, got {count}",
            )
        )

    for index, question in enumerate(explanation.questions, start=1):
        location = question.answer_location
        label = f"question {index}"

        if location.path != snippet.path.as_posix():
            rejections.append(
                Rejection(
                    "answer_outside_station",
                    f"{label} answers into {location.path!r}, but only "
                    f"{snippet.path.as_posix()!r} was shown",
                )
            )
            continue

        if location.end_line < location.start_line:
            rejections.append(
                Rejection(
                    "answer_range_inverted",
                    f"{label} has start_line {location.start_line} after end_line "
                    f"{location.end_line}",
                )
            )
            continue

        if location.end_line > snippet.line_count:
            rejections.append(
                Rejection(
                    "answer_out_of_bounds",
                    f"{label} cites line {location.end_line} but the file has "
                    f"{snippet.line_count} lines",
                )
            )
            continue

        if not snippet.covers_range(location.start_line, location.end_line):
            rejections.append(
                Rejection(
                    "answer_not_shown",
                    f"{label} cites lines {location.start_line}-{location.end_line}, "
                    "which were not among the lines shown",
                )
            )
            continue

        cited = set(range(location.start_line, location.end_line + 1))
        if cited <= snippet.import_lines:
            rejections.append(
                Rejection(
                    "answer_is_import",
                    f"{label} points only at import lines "
                    f"{location.start_line}-{location.end_line}",
                )
            )

    return rejections
