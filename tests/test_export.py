import json
from pathlib import Path

import pytest

from repoonboard.explanation import (
    AnswerLocation,
    Provenance,
    Question,
    StationExplanation,
)
from repoonboard.export import (
    CODETOUR_SCHEMA,
    Tour,
    TourStation,
    is_generated,
    to_codetour,
    to_markdown,
    to_mermaid,
)
from repoonboard.snippets import build


def _explanation(path: str, provenance: Provenance = Provenance.MODEL) -> StationExplanation:
    return StationExplanation(
        path=path,
        summary=f"Summary for {path}.",
        why_it_matters="It is imported widely.",
        questions=[
            Question(
                prompt="What does `load_user` return?",
                answer_location=AnswerLocation(path=path, start_line=7, end_line=9),
            ),
            Question(
                prompt="What is defined on the first line?",
                answer_location=AnswerLocation(path=path, start_line=1, end_line=1),
            ),
        ],
        provenance=provenance,
    )


@pytest.fixture
def tour() -> Tour:
    return Tour(
        repository="demo",
        commit="abc123def456",
        stations=(
            TourStation("main.py", "entry", 12, _explanation("main.py")),
            TourStation(
                "app/service.py",
                "core",
                20,
                _explanation("app/service.py", Provenance.STRUCTURAL),
            ),
        ),
        edges=(("main.py", "app/service.py"), ("main.py", "not/a/station.py")),
    )


# ---------------------------------------------------------------------------
# .tour
# ---------------------------------------------------------------------------


def test_codetour_is_valid_json_with_the_expected_shape(tour):
    document = json.loads(to_codetour(tour))
    assert document["$schema"] == CODETOUR_SCHEMA
    assert document["title"] == "Onboarding: demo"
    assert len(document["steps"]) == 2


def test_codetour_pins_the_commit_as_ref(tour):
    assert json.loads(to_codetour(tour))["ref"] == "abc123def456"


def test_codetour_omits_ref_when_there_is_no_commit(tour):
    document = json.loads(to_codetour(Tour("demo", None, tour.stations)))
    assert "ref" not in document


def test_codetour_steps_carry_file_and_anchor_line(tour):
    steps = json.loads(to_codetour(tour))["steps"]
    assert steps[0]["file"] == "main.py"
    assert steps[0]["line"] == 12
    assert steps[1]["line"] == 20


def test_codetour_steps_are_in_tour_order(tour):
    steps = json.loads(to_codetour(tour))["steps"]
    assert [step["file"] for step in steps] == ["main.py", "app/service.py"]


def test_codetour_description_carries_every_question_and_its_answer(tour):
    description = json.loads(to_codetour(tour))["steps"][0]["description"]
    assert "What does `load_user` return?" in description
    assert "lines 7-9" in description
    assert "line 1" in description


def test_codetour_marks_a_structural_station_as_modelless(tour):
    steps = json.loads(to_codetour(tour))["steps"]
    assert "without a language model" in steps[1]["description"]
    assert "without a language model" not in steps[0]["description"]


# ---------------------------------------------------------------------------
# ONBOARDING.md
# ---------------------------------------------------------------------------


def test_markdown_lists_every_station_in_order(tour):
    text = to_markdown(tour)
    assert text.index("## 1. `main.py`") < text.index("## 2. `app/service.py`")


def test_markdown_records_the_commit(tour):
    assert "abc123def456" in to_markdown(tour)


def test_markdown_says_plainly_when_there_is_no_commit(tour):
    text = to_markdown(Tour("demo", None, tour.stations))
    assert "not\npinned to a commit" in text or "not pinned to a commit" in text.replace("\n", " ")


def test_markdown_links_answers_with_line_fragments(tour):
    assert "(main.py#L7-L9)" in to_markdown(tour)


def test_markdown_uses_a_single_line_fragment_for_a_one_line_answer(tour):
    assert "(main.py#L1)" in to_markdown(tour)


def test_markdown_does_not_claim_a_model_wrote_an_all_structural_tour(tour):
    structural = Tour(
        "demo",
        "abc",
        tuple(
            TourStation(
                s.path,
                s.layer,
                s.anchor_line,
                _explanation(s.path, Provenance.STRUCTURAL),
            )
            for s in tour.stations
        ),
    )
    text = to_markdown(structural)
    assert "No language model was involved" in text
    assert "A language model wrote" not in text


def test_markdown_counts_the_mixed_case_honestly(tour):
    # The fixture is one model station and one structural station.
    assert "wrote 1 of 2 explanations" in to_markdown(tour)


def test_markdown_claims_full_model_authorship_only_when_true(tour):
    all_model = Tour(
        "demo",
        "abc",
        tuple(
            TourStation(s.path, s.layer, s.anchor_line, _explanation(s.path, Provenance.MODEL))
            for s in tour.stations
        ),
    )
    text = to_markdown(all_model)
    assert "A language model wrote the explanations" in text
    assert "fall back to structural" not in text


def test_markdown_states_provenance_per_station(tour):
    text = to_markdown(tour)
    assert "explained by model, verified" in text
    assert "structural — no model involved" in text


# ---------------------------------------------------------------------------
# architecture.mmd
# ---------------------------------------------------------------------------


def test_mermaid_groups_stations_by_layer(tour):
    diagram = to_mermaid(tour)
    assert "subgraph entry" in diagram
    assert "subgraph core" in diagram


def test_mermaid_draws_only_edges_between_selected_stations(tour):
    diagram = to_mermaid(tour)
    assert "s0 --> s1" in diagram
    assert "not/a/station.py" not in diagram


def test_mermaid_says_so_when_there_are_no_edges(tour):
    diagram = to_mermaid(Tour("demo", "abc", tour.stations, edges=()))
    assert "No direct import edges" in diagram


def test_mermaid_labels_every_station(tour):
    diagram = to_mermaid(tour)
    assert '"main.py"' in diagram
    assert '"app/service.py"' in diagram


# ---------------------------------------------------------------------------
# The marker that protects hand-written files from being overwritten
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("renderer", [to_codetour, to_markdown, to_mermaid])
def test_every_rendering_carries_the_generated_marker(tour, renderer):
    assert is_generated(renderer(tour))


def test_a_hand_written_file_is_not_mistaken_for_generated_output():
    assert not is_generated("# Onboarding\n\nWe wrote this ourselves.\n")


def test_the_marker_survives_a_json_round_trip(tour):
    document = json.loads(to_codetour(tour))
    assert is_generated(document["description"])


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("renderer", [to_codetour, to_markdown, to_mermaid])
def test_rendering_is_deterministic(tour, renderer):
    assert renderer(tour) == renderer(tour)


# ---------------------------------------------------------------------------
# The anchor line the exporters depend on
# ---------------------------------------------------------------------------


def test_anchor_line_is_the_first_definition_not_an_import():
    source = b'"""Doc."""\n\nimport os\nfrom pathlib import Path\n\n\ndef alpha():\n    return 1\n'
    snippet = build(Path("a.py"), source, "python")
    assert snippet.anchor_line == 7
    assert snippet.anchor_line not in snippet.import_lines


def test_anchor_line_falls_back_past_imports_when_there_are_no_definitions():
    source = b"import os\n\nCONFIG = {'a': 1}\n"
    snippet = build(Path("a.py"), source, "python")
    assert snippet.anchor_line == 3


def test_anchor_line_is_never_blank():
    source = b"import os\n\n\n\nVALUE = 1\n"
    snippet = build(Path("a.py"), source, "python")
    assert snippet.anchor_line == 5


def test_anchor_line_is_none_for_a_pure_import_file():
    source = b'export { a } from "./a";\n'
    snippet = build(Path("index.ts"), source, "typescript")
    assert snippet.anchor_line is None
