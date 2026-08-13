import pytest

from repoonboard.staleness import (
    State,
    StationVerdict,
    build_report,
    cited_ranges,
    classify_station,
)

CITED = [(10, 10), (20, 25), (40, 44)]


# ---------------------------------------------------------------------------
# One station at a time
# ---------------------------------------------------------------------------


def test_an_untouched_file_is_fresh():
    verdict = classify_station("a.py", CITED, None, [])
    assert verdict.state is State.FRESH
    assert verdict.state.is_stale is False


def test_a_deleted_file_is_reported_as_deleted():
    assert classify_station("a.py", CITED, ("D", None), []).state is State.DELETED


def test_a_renamed_file_carries_its_new_path():
    verdict = classify_station("a.py", CITED, ("R", "b.py"), [])
    assert verdict.state is State.MOVED
    assert verdict.new_path == "b.py"
    assert "b.py" in verdict.detail


def test_a_change_inside_a_cited_range_invalidates_the_answers():
    verdict = classify_station("a.py", CITED, ("M", None), [(22, 23)])
    assert verdict.state is State.ANSWERS_CHANGED
    assert "20-25" in verdict.detail


def test_a_change_overlapping_the_edge_of_a_cited_range_counts():
    verdict = classify_station("a.py", CITED, ("M", None), [(25, 30)])
    assert verdict.state is State.ANSWERS_CHANGED


def test_an_insertion_inside_a_cited_range_counts():
    # git reports a pure insertion as a zero-width point.
    verdict = classify_station("a.py", CITED, ("M", None), [(22, 22)])
    assert verdict.state is State.ANSWERS_CHANGED


def test_a_change_above_the_cited_lines_shifts_them():
    verdict = classify_station("a.py", CITED, ("M", None), [(2, 3)])
    assert verdict.state is State.LINES_SHIFTED
    assert verdict.state.is_stale is True


def test_a_change_below_every_cited_line_leaves_the_station_fresh():
    verdict = classify_station("a.py", CITED, ("M", None), [(90, 95)])
    assert verdict.state is State.FRESH
    assert "not at or above" in verdict.detail


def test_a_modified_file_with_no_hunks_is_fresh():
    assert classify_station("a.py", CITED, ("M", None), []).state is State.FRESH


def test_a_station_citing_nothing_is_not_crashed_by_a_change():
    verdict = classify_station("a.py", [], ("M", None), [(1, 2)])
    assert verdict.state is State.FRESH


# ---------------------------------------------------------------------------
# The whole run
# ---------------------------------------------------------------------------


def _report(**overrides):
    kwargs = {
        "pinned": "aaa",
        "head": "bbb",
        "stations": [("a.py", CITED), ("b.py", CITED)],
        "changes": {},
        "ranges_for": {},
        "current_selection": ["a.py", "b.py"],
    }
    kwargs.update(overrides)
    return build_report(**kwargs)


def test_an_untouched_repository_reports_current():
    report = _report()
    assert report.is_current
    assert report.stale == []
    assert report.now_outranking == []


def test_a_new_higher_scoring_file_is_surfaced():
    report = _report(current_selection=["a.py", "b.py", "c.py"])
    assert report.now_outranking == ["c.py"]
    assert not report.is_current


def test_a_station_that_would_no_longer_be_selected_is_surfaced():
    report = _report(current_selection=["a.py"])
    assert report.no_longer_selected == ["b.py"]


def test_a_renamed_station_is_not_counted_as_a_newcomer():
    report = _report(
        changes={"a.py": ("R", "moved/a.py")},
        current_selection=["moved/a.py", "b.py"],
    )
    assert report.now_outranking == []
    assert report.verdicts[0].state is State.MOVED


def test_a_deleted_station_is_not_reported_as_merely_deselected():
    report = _report(changes={"a.py": ("D", None)}, current_selection=["b.py"])
    assert report.no_longer_selected == []
    assert report.verdicts[0].state is State.DELETED


def test_stale_counts_only_what_actually_changed():
    report = _report(
        changes={"a.py": ("M", None)},
        ranges_for={"a.py": [(22, 23)]},
    )
    assert len(report.stale) == 1
    assert report.stale[0].path == "a.py"


def test_every_station_receives_exactly_one_verdict():
    report = _report(stations=[("a.py", CITED), ("b.py", CITED), ("c.py", CITED)])
    assert len(report.verdicts) == 3
    assert [v.path for v in report.verdicts] == ["a.py", "b.py", "c.py"]


@pytest.mark.parametrize(
    "state,expected",
    [
        (State.FRESH, False),
        (State.LINES_SHIFTED, True),
        (State.ANSWERS_CHANGED, True),
        (State.MOVED, True),
        (State.DELETED, True),
    ],
)
def test_staleness_flag_per_state(state, expected):
    assert state.is_stale is expected


# ---------------------------------------------------------------------------
# Reading ranges back out of a stored tour
# ---------------------------------------------------------------------------


def test_cited_ranges_includes_the_anchor_and_every_answer():
    station = {
        "anchor_line": 7,
        "questions": [
            {"answer_location": {"start_line": 10, "end_line": 12}},
            {"answer_location": {"start_line": 20, "end_line": 20}},
        ],
    }
    assert cited_ranges(station) == [(7, 7), (10, 12), (20, 20)]


def test_cited_ranges_tolerates_a_record_with_no_anchor():
    station = {"questions": [{"answer_location": {"start_line": 3, "end_line": 4}}]}
    assert cited_ranges(station) == [(3, 4)]


def test_cited_ranges_skips_malformed_locations():
    station = {
        "anchor_line": None,
        "questions": [
            {"answer_location": {"start_line": "x", "end_line": 4}},
            {"answer_location": {}},
            {},
            {"answer_location": {"start_line": 9, "end_line": 2}},
            {"answer_location": {"start_line": 5, "end_line": 6}},
        ],
    }
    assert cited_ranges(station) == [(5, 6)]


def test_cited_ranges_of_an_empty_record_is_empty():
    assert cited_ranges({}) == []


def test_verdict_detail_is_never_empty():
    for change, ranges in (
        (None, []),
        (("D", None), []),
        (("R", "b.py"), []),
        (("M", None), [(22, 23)]),
        (("M", None), [(1, 1)]),
        (("M", None), [(90, 95)]),
    ):
        verdict = classify_station("a.py", CITED, change, ranges)
        assert isinstance(verdict, StationVerdict)
        assert verdict.detail.strip()
