import pytest

from repoonboard.evaluation import (
    Source,
    build_ground_truth,
    extract_paths,
    mean_precision,
    precision_at_k,
    top_committed,
)

KNOWN = frozenset(
    {
        "scrapy/core/engine.py",
        "scrapy/core/scheduler.py",
        "scrapy/spiders/__init__.py",
        "scrapy/utils/misc.py",
        "src/router.ts",
        "src/hono.ts",
    }
)


# ---------------------------------------------------------------------------
# Extracting references from free text
# ---------------------------------------------------------------------------


def test_a_referenced_path_is_found():
    text = "Start by reading scrapy/core/engine.py before anything else."
    assert extract_paths(text, KNOWN) == {"scrapy/core/engine.py"}


def test_a_path_that_does_not_exist_at_the_pin_is_ignored():
    assert extract_paths("see scrapy/core/deleted.py", KNOWN) == set()


def test_prose_that_merely_looks_like_a_filename_is_ignored():
    # A bare filename with no directory is far more often an example than a
    # real reference, so it must not enter the ground truth.
    assert extract_paths("engine.py handles this", KNOWN) == set()


def test_a_repository_name_prefix_is_tolerated():
    text = "look at scrapy/scrapy/core/engine.py"
    assert extract_paths(text, KNOWN) == {"scrapy/core/engine.py"}


def test_trailing_punctuation_does_not_break_a_match():
    assert extract_paths("(see src/router.ts).", KNOWN) == {"src/router.ts"}


def test_multiple_distinct_paths_are_all_found():
    text = "src/router.ts and src/hono.ts both matter"
    assert extract_paths(text, KNOWN) == {"src/router.ts", "src/hono.ts"}


@pytest.mark.parametrize("text", ["", None])
def test_empty_text_yields_nothing(text):
    assert extract_paths(text, KNOWN) == set()


# ---------------------------------------------------------------------------
# Churn
# ---------------------------------------------------------------------------


def test_top_committed_takes_the_most_changed_files():
    counts = {
        "scrapy/core/engine.py": 90,
        "scrapy/core/scheduler.py": 50,
        "scrapy/utils/misc.py": 10,
    }
    assert top_committed(counts, KNOWN, limit=2) == {
        "scrapy/core/engine.py",
        "scrapy/core/scheduler.py",
    }


def test_top_committed_ignores_files_absent_at_the_pin():
    counts = {"gone/removed.py": 999, "scrapy/utils/misc.py": 1}
    assert top_committed(counts, KNOWN, limit=10) == {"scrapy/utils/misc.py"}


def test_top_committed_breaks_ties_deterministically():
    counts = {
        "scrapy/core/engine.py": 5,
        "scrapy/core/scheduler.py": 5,
        "scrapy/utils/misc.py": 5,
    }
    first = top_committed(counts, KNOWN, limit=2)
    second = top_committed(dict(reversed(list(counts.items()))), KNOWN, limit=2)
    assert first == second


# ---------------------------------------------------------------------------
# The union
# ---------------------------------------------------------------------------


def test_the_union_combines_all_three_sources():
    truth = build_ground_truth(
        contributing_text="read scrapy/core/engine.py",
        commit_counts={"scrapy/core/scheduler.py": 40},
        issue_texts=["the bug is in scrapy/utils/misc.py"],
        known=KNOWN,
    )
    assert truth.paths == {
        "scrapy/core/engine.py",
        "scrapy/core/scheduler.py",
        "scrapy/utils/misc.py",
    }


def test_a_repository_without_beginner_issues_reports_two_sources():
    truth = build_ground_truth(
        contributing_text="read src/hono.ts",
        commit_counts={"src/router.ts": 12},
        issue_texts=[],
        known=KNOWN,
    )
    assert set(truth.contributing_sources()) == {Source.CONTRIBUTING, Source.CHURN}
    assert truth.by_source[Source.GOOD_FIRST_ISSUE] == set()


def test_overlapping_sources_do_not_double_count():
    truth = build_ground_truth(
        contributing_text="read src/router.ts",
        commit_counts={"src/router.ts": 99},
        issue_texts=["src/router.ts again"],
        known=KNOWN,
    )
    assert truth.paths == {"src/router.ts"}
    assert len(truth.contributing_sources()) == 3


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_precision_counts_hits_in_the_first_k():
    predicted = ["a.py", "scrapy/core/engine.py", "b.py"]
    score = precision_at_k(predicted, {"scrapy/core/engine.py"}, k=3)
    assert score.hits == 1
    assert score.considered == 3
    assert score.precision == pytest.approx(1 / 3)


def test_predictions_beyond_k_are_ignored():
    predicted = ["x.py"] * 6 + ["scrapy/core/engine.py"]
    score = precision_at_k(predicted, {"scrapy/core/engine.py"}, k=6)
    assert score.hits == 0


def test_a_short_tour_is_scored_on_what_it_produced():
    # Four stations scored out of four, not out of six.
    predicted = ["scrapy/core/engine.py", "a.py", "b.py", "c.py"]
    score = precision_at_k(predicted, {"scrapy/core/engine.py"}, k=6)
    assert score.considered == 4
    assert score.precision == pytest.approx(0.25)


def test_an_empty_prediction_scores_zero_without_dividing_by_zero():
    score = precision_at_k([], {"scrapy/core/engine.py"}, k=6)
    assert score.considered == 0
    assert score.precision == 0.0


def test_mean_precision_is_unweighted_across_repositories():
    scores = [
        precision_at_k(["a.py"], {"a.py"}, k=1),
        precision_at_k(["b.py"], set(), k=1),
    ]
    assert mean_precision(scores) == pytest.approx(0.5)


def test_mean_of_nothing_is_zero():
    assert mean_precision([]) == 0.0
