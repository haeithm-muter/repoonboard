"""Prompt construction.

The model is given two things: the graph facts that caused this file to be
selected, and the lines of the file it is allowed to talk about. It is never
told to choose or rank anything — by the time this runs, selection and
ordering already happened, and re-opening them is the one thing the
architecture forbids.
"""

from __future__ import annotations

from .grounding import MAX_QUESTIONS, MIN_QUESTIONS
from .snippets import Snippet

SYSTEM = """\
You explain source files to a developer reading an unfamiliar repository for \
the first time.

The file you are given was already selected and positioned by static analysis \
of the dependency graph. Do not comment on whether it deserves its place, \
suggest a different file, or refer to other stations in the tour.

Rules, all of which are checked automatically before your output is used:

1. Write about the lines you were shown and nothing else. Every claim must be \
visible in that text. If you cannot tell what something does from what you \
were shown, do not guess — say less instead.
2. Put every code identifier in backticks: `parse_config`, `UserCache`. \
Anything you backtick is checked against the shown lines, so do not backtick \
a name you did not see there.
3. Write between {min_questions} and {max_questions} verification questions. \
Each one must be answerable from the shown lines, and each must carry the \
line range where its answer lives.
4. Never target a question's answer at an import line. "What does this file \
import?" verifies nothing.
5. Write plain English prose. No markdown headings, no bullet lists.\
"""

_USER_TEMPLATE = """\
File: {path}
Language: {language}
Layer: {layer}
Length: {line_count} lines

Why static analysis selected this file:
{signals}

{docstring_section}\
Lines shown (the number before each `|` is the real line number in the file):

{text}

Write:
- summary: what this file does, in two or three sentences.
- why_it_matters: why a newcomer should read it, grounded in the signals above.
- questions: {min_questions} to {max_questions} questions that check whether \
the reader understood these lines, each with the file path and the line range \
holding the answer.\
"""


def _format_signals(signals: dict[str, float], extra: dict[str, str]) -> str:
    lines = [f"- {name}: {value}" for name, value in extra.items()]
    for component, value in sorted(signals.items(), key=lambda pair: -pair[1]):
        if value > 0:
            lines.append(f"- {component}: {value:.3f}")
    return "\n".join(lines) if lines else "- (no signals available)"


def build_system() -> str:
    return SYSTEM.format(min_questions=MIN_QUESTIONS, max_questions=MAX_QUESTIONS)


def build_user(
    snippet: Snippet,
    layer: str,
    signals: dict[str, float],
    extra: dict[str, str],
) -> str:
    docstring_section = ""
    if snippet.docstring:
        docstring_section = f"The file documents itself as:\n{snippet.docstring}\n\n"

    return _USER_TEMPLATE.format(
        path=snippet.path.as_posix(),
        language=snippet.language,
        layer=layer,
        line_count=snippet.line_count,
        signals=_format_signals(signals, extra),
        docstring_section=docstring_section,
        text=snippet.text,
        min_questions=MIN_QUESTIONS,
        max_questions=MAX_QUESTIONS,
    )


def build_retry(feedback: str) -> str:
    """Appended to the prompt after the gate rejects an attempt.

    The rejections are quoted verbatim rather than summarised — the model is
    better at fixing a specific stated fault than a paraphrase of one.
    """
    return (
        "\n\nYour previous attempt was rejected by an automated check for these "
        f"reasons:\n{feedback}\n\n"
        "Every one of those is a fact about the file, not a matter of opinion. "
        "Rewrite your answer so that none of them apply. If a symbol or path was "
        "rejected as unknown, do not rename it — remove the claim entirely and "
        "write about something you can actually see in the lines above."
    )
