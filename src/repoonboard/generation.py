"""Generation pipeline: model, gate, retry, fallback.

The ladder has three rungs and always terminates on solid ground:

1. ask the model;
2. if the gate rejects, ask once more with the rejections quoted back;
3. if it rejects again, build the explanation structurally — from the graph
   signals and the file's own symbol table, with no model involved.

The third rung is what makes the promise "no unverified content is ever
emitted" keepable. A pipeline whose only failure mode is "give up" would
either ship ungrounded text or ship a hole in the tour; this ships neither.
The structural output is passed through the same gate as everything else,
and that invariant is tested.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .explanation import (
    AnswerLocation,
    Provenance,
    Question,
    StationDraft,
    StationExplanation,
)
from .grounding import MAX_QUESTIONS, MIN_QUESTIONS, GateResult, Rejection, check
from .model import GenerationError, Generator, tighten_schema
from .prompts import build_retry, build_system, build_user
from .snippets import Snippet


class UnverifiableStation(RuntimeError):
    """The file offers nothing a question could be anchored to.

    Raised only when every line shown is an import — a pure re-export barrel.
    Such a file cannot honestly be a station, and saying so is better than
    emitting a station whose questions verify nothing.
    """


@dataclass
class StationResult:
    """One station's content, plus how it was arrived at."""

    explanation: StationExplanation
    attempts: int
    rejections: tuple[Rejection, ...]

    @property
    def provenance(self) -> Provenance:
        return self.explanation.provenance


def draft_schema() -> dict[str, Any]:
    return tighten_schema(StationDraft.model_json_schema())


def _parse(raw: str) -> tuple[StationDraft | None, Rejection | None]:
    """Parse a model response, reporting failure as a rejection rather than raising."""
    text = raw.strip()
    if text.startswith("```"):
        # Defensive: structured outputs should never fence, but a fenced
        # payload is a formatting slip rather than a grounding failure, and
        # rejecting it outright would waste an attempt.
        text = text.split("\n", 1)[-1] if "\n" in text else text
        text = text.removesuffix("```")
        text = text.strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, Rejection("invalid_json", f"response was not valid JSON: {exc}")

    try:
        return StationDraft.model_validate(payload), None
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "(root)"
        return None, Rejection("invalid_shape", f"{location}: {first['msg']}")


def generate_station(
    snippet: Snippet,
    layer: str,
    signals: dict[str, float],
    extra: dict[str, str],
    known_paths: frozenset[str],
    generator: Generator | None = None,
) -> StationResult:
    """Produce grounded content for one station.

    A `generator` of None runs the structural path directly, which is what
    `--dry-run` uses to exercise the whole pipeline with no network and no
    model.
    """
    path = snippet.path.as_posix()
    if generator is None:
        return _structural_result(snippet, layer, signals, known_paths, attempts=0)

    system = build_system()
    base_user = build_user(snippet, layer, signals, extra)
    schema = draft_schema()

    last: GateResult | None = None
    user = base_user

    for attempt in (1, 2):
        try:
            raw = generator.complete(system, user, schema)
        except GenerationError:
            # The model is unreachable or refused. Structural content is a
            # better answer than no station at all.
            break

        draft, parse_failure = _parse(raw)
        if parse_failure is not None:
            last = GateResult((parse_failure,))
        else:
            assert draft is not None
            candidate = StationExplanation.from_draft(
                draft,
                path=path,
                provenance=Provenance.MODEL if attempt == 1 else Provenance.MODEL_RETRY,
            )
            result = check(candidate, snippet, known_paths)
            if result.ok:
                return StationResult(candidate, attempts=attempt, rejections=())
            last = result

        if attempt == 1:
            user = base_user + build_retry(last.as_feedback())

    rejections = last.rejections if last is not None else ()
    return _structural_result(snippet, layer, signals, known_paths, attempts=2, rejections=rejections)


# ---------------------------------------------------------------------------
# The structural path — no model involved
# ---------------------------------------------------------------------------


def _shown_symbols(snippet: Snippet) -> list:
    """Symbols the reader can actually see, largest first.

    A symbol whose body was elided cannot anchor a question, and a symbol
    whose name never appears in the shown text would be rejected by the gate
    as unknown — so both are filtered out here rather than discovered later.
    """
    return sorted(
        (
            symbol
            for symbol in snippet.symbols
            if snippet.covers_range(symbol.start_line, symbol.end_line)
            and symbol.name in snippet.text
            and not set(range(symbol.start_line, symbol.end_line + 1)) <= snippet.import_lines
        ),
        key=lambda symbol: symbol.end_line - symbol.start_line,
        reverse=True,
    )


def _non_import_ranges(snippet: Snippet) -> list[tuple[int, int]]:
    """Contiguous runs of shown lines that are not imports."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for line in range(1, snippet.line_count + 1):
        usable = snippet.covers(line) and line not in snippet.import_lines
        if usable and start is None:
            start = line
        elif not usable and start is not None:
            runs.append((start, line - 1))
            start = None
    if start is not None:
        runs.append((start, snippet.line_count))
    return runs


def _candidate_ranges(snippet: Snippet) -> list[tuple[int, int]]:
    """Answerable line ranges, split until there are enough to ask about.

    A file with no definitions may present a single unbroken run of lines,
    which yields one question where the gate demands two. Splitting the widest
    run keeps every range real — these are still lines the reader was shown —
    without inventing anything.
    """
    candidates = _non_import_ranges(snippet)
    while len(candidates) < MIN_QUESTIONS:
        widest = max(
            range(len(candidates)),
            key=lambda index: candidates[index][1] - candidates[index][0],
            default=None,
        )
        if widest is None:
            break
        start, end = candidates[widest]
        if end == start:
            break  # a single line cannot be split further
        middle = start + (end - start) // 2
        candidates[widest : widest + 1] = [(start, middle), (middle + 1, end)]
    return candidates


def _structural_questions(snippet: Snippet) -> list[Question]:
    path = snippet.path.as_posix()
    questions: list[Question] = []
    used: set[tuple[int, int]] = set()

    def add(prompt: str, start: int, end: int) -> None:
        questions.append(
            Question(
                prompt=prompt,
                answer_location=AnswerLocation(path=path, start_line=start, end_line=end),
            )
        )
        used.add((start, end))

    for symbol in _shown_symbols(snippet):
        if len(questions) >= MAX_QUESTIONS:
            break
        add(
            f"What does the {symbol.kind} `{symbol.name}` do, and what does it return?",
            symbol.start_line,
            symbol.end_line,
        )

    # A file with no top-level definitions (a script, a settings module) still
    # needs questions, so fall back to the lines themselves.
    for start, end in _candidate_ranges(snippet):
        if len(questions) >= MIN_QUESTIONS:
            break
        if (start, end) in used:
            continue
        add(f"What happens in lines {start} to {end} of this file?", start, end)

    # Degenerate case: one usable line, and the gate still wants two questions.
    if 0 < len(questions) < MIN_QUESTIONS:
        location = questions[0].answer_location
        add(
            f"Which names does this file define in lines {location.start_line} to "
            f"{location.end_line}?",
            location.start_line,
            location.end_line,
        )

    if not questions:
        raise UnverifiableStation(
            f"{path} has no lines that can carry a verification question: every "
            "line shown is an import. A file that only re-exports cannot be a "
            "station, because there is nothing in it to verify."
        )

    return questions[:MAX_QUESTIONS]


def _structural_summary(snippet: Snippet) -> str:
    if snippet.docstring:
        first = snippet.docstring.strip().splitlines()[0].strip()
        if first:
            return f"The file documents itself as: {first}"

    shown = _shown_symbols(snippet)
    if shown:
        names = ", ".join(f"`{symbol.name}`" for symbol in shown[:4])
        return (
            f"A {snippet.language} file of {snippet.line_count} lines defining {names}."
        )
    return f"A {snippet.language} file of {snippet.line_count} lines."


def _structural_why(layer: str, signals: dict[str, float]) -> str:
    ranked = [name for name, value in sorted(signals.items(), key=lambda p: -p[1]) if value > 0]
    reason = ranked[0].replace("_", " ") if ranked else "its position in the import graph"
    return (
        f"Static analysis placed this file in the {layer} layer, and it scored "
        f"highest on {reason}. This explanation was generated without a model, "
        "so it describes structure rather than intent."
    )


def _structural_result(
    snippet: Snippet,
    layer: str,
    signals: dict[str, float],
    known_paths: frozenset[str],
    attempts: int,
    rejections: tuple[Rejection, ...] = (),
) -> StationResult:
    explanation = StationExplanation(
        path=snippet.path.as_posix(),
        summary=_structural_summary(snippet),
        why_it_matters=_structural_why(layer, signals),
        questions=_structural_questions(snippet),
        provenance=Provenance.STRUCTURAL,
    )
    return StationResult(explanation, attempts=attempts, rejections=rejections)
