from pathlib import Path

import pytest

from repoonboard.explanation import AnswerLocation, Question, StationExplanation
from repoonboard.grounding import check
from repoonboard.snippets import build

SOURCE = b'''"""Service layer."""

import os
from pathlib import Path


def load_user(user_id):
    """Fetch a user."""
    return {"id": user_id}


class UserCache:
    def get(self, key):
        return None
'''

KNOWN = frozenset({"app/service.py", "app/models.py"})


@pytest.fixture
def snippet():
    return build(Path("app/service.py"), SOURCE, "python")


def _explanation(**overrides) -> StationExplanation:
    payload = {
        "path": "app/service.py",
        "summary": "Defines `load_user` and the `UserCache` class.",
        "why_it_matters": "It is imported widely across the repository.",
        "questions": [
            Question(
                prompt="What does `load_user` return?",
                answer_location=AnswerLocation(path="app/service.py", start_line=7, end_line=9),
            ),
            Question(
                prompt="What does `UserCache.get` return when the key is absent?",
                answer_location=AnswerLocation(path="app/service.py", start_line=12, end_line=14),
            ),
        ],
    }
    payload.update(overrides)
    return StationExplanation(**payload)


# ---------------------------------------------------------------------------
# The passing case
# ---------------------------------------------------------------------------


def test_well_grounded_content_passes(snippet):
    result = check(_explanation(), snippet, KNOWN)
    assert result.ok, result.as_feedback()


def test_three_questions_are_allowed(snippet):
    extra = _explanation().questions + [
        Question(
            prompt="What does the module docstring say this file is?",
            answer_location=AnswerLocation(path="app/service.py", start_line=1, end_line=1),
        )
    ]
    assert check(_explanation(questions=extra), snippet, KNOWN).ok


# ---------------------------------------------------------------------------
# One test per rejection reason
# ---------------------------------------------------------------------------


def _codes(result) -> set[str]:
    return {rejection.code for rejection in result.rejections}


def test_invented_symbol_is_rejected(snippet):
    bad = _explanation(summary="Delegates to `deleteEverything` for cleanup.")
    assert "unknown_symbol" in _codes(check(bad, snippet, KNOWN))


def test_invented_path_is_rejected(snippet):
    bad = _explanation(why_it_matters="It is called from `app/nonexistent.py`.")
    assert "unknown_path" in _codes(check(bad, snippet, KNOWN))


def test_real_sibling_path_is_accepted(snippet):
    ok = _explanation(why_it_matters="It is imported by `app/models.py`.")
    assert check(ok, snippet, KNOWN).ok


def test_too_few_questions_is_rejected(snippet):
    bad = _explanation(questions=_explanation().questions[:1])
    assert "question_count" in _codes(check(bad, snippet, KNOWN))


def test_too_many_questions_is_rejected(snippet):
    bad = _explanation(questions=_explanation().questions * 2)
    assert "question_count" in _codes(check(bad, snippet, KNOWN))


def test_answer_beyond_end_of_file_is_rejected(snippet):
    bad = _explanation(
        questions=[
            Question(
                prompt="What does `load_user` return?",
                answer_location=AnswerLocation(
                    path="app/service.py", start_line=500, end_line=900
                ),
            ),
            _explanation().questions[1],
        ]
    )
    assert "answer_out_of_bounds" in _codes(check(bad, snippet, KNOWN))


def test_inverted_answer_range_is_rejected(snippet):
    bad = _explanation(
        questions=[
            Question(
                prompt="What does `load_user` return?",
                answer_location=AnswerLocation(path="app/service.py", start_line=9, end_line=7),
            ),
            _explanation().questions[1],
        ]
    )
    assert "answer_range_inverted" in _codes(check(bad, snippet, KNOWN))


def test_answer_pointing_only_at_imports_is_rejected(snippet):
    bad = _explanation(
        questions=[
            Question(
                prompt="What is imported here?",
                answer_location=AnswerLocation(path="app/service.py", start_line=3, end_line=3),
            ),
            _explanation().questions[1],
        ]
    )
    assert "answer_is_import" in _codes(check(bad, snippet, KNOWN))


def test_answer_in_another_file_is_rejected(snippet):
    bad = _explanation(
        questions=[
            Question(
                prompt="What does the model define?",
                answer_location=AnswerLocation(path="app/models.py", start_line=1, end_line=2),
            ),
            _explanation().questions[1],
        ]
    )
    assert "answer_outside_station" in _codes(check(bad, snippet, KNOWN))


def test_content_labelled_for_a_different_station_is_rejected(snippet):
    bad = _explanation(path="app/models.py")
    assert "wrong_station" in _codes(check(bad, snippet, KNOWN))


def test_answer_in_an_omitted_region_is_rejected():
    body = b"".join(b"def f%d():\n    return %d\n\n" % (i, i) for i in range(200))
    source = b'"""Doc."""\n\nimport os\n\n' + body
    reduced = build(Path("big.py"), source, "python", budget=60)

    omitted = next(
        line
        for line in range(1, reduced.line_count + 1)
        if not reduced.covers(line)
    )
    bad = StationExplanation(
        path="big.py",
        summary="A large module.",
        why_it_matters="Widely imported.",
        questions=[
            Question(
                prompt="What happens here?",
                answer_location=AnswerLocation(
                    path="big.py", start_line=omitted, end_line=omitted
                ),
            ),
            Question(
                prompt="What does the header declare?",
                answer_location=AnswerLocation(path="big.py", start_line=1, end_line=1),
            ),
        ],
    )
    assert "answer_not_shown" in _codes(check(bad, reduced, frozenset({"big.py"})))


# ---------------------------------------------------------------------------
# Behaviour of the checker itself
# ---------------------------------------------------------------------------


def test_every_rejection_is_reported_not_just_the_first(snippet):
    bad = _explanation(
        summary="Calls `ghostFunction`.",
        why_it_matters="Lives in `app/imaginary.py`.",
        questions=_explanation().questions[:1],
    )
    assert _codes(check(bad, snippet, KNOWN)) == {
        "unknown_symbol",
        "unknown_path",
        "question_count",
    }


def test_feedback_lists_each_rejection_for_the_retry_prompt(snippet):
    bad = _explanation(summary="Calls `ghostFunction`.")
    feedback = check(bad, snippet, KNOWN).as_feedback()
    assert "unknown_symbol" in feedback and "ghostFunction" in feedback


def test_language_words_in_backticks_are_not_treated_as_claims(snippet):
    ok = _explanation(why_it_matters="Returns `None` when absent, in `python`.")
    assert check(ok, snippet, KNOWN).ok


def test_function_call_parentheses_are_stripped_before_checking(snippet):
    ok = _explanation(summary="Calls `load_user()` on each request.")
    assert check(ok, snippet, KNOWN).ok
