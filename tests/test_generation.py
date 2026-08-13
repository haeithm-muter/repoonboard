import json
from pathlib import Path

import pytest

from repoonboard.explanation import Provenance, StationDraft
from repoonboard.generation import (
    UnverifiableStation,
    draft_schema,
    generate_station,
)
from repoonboard.grounding import check
from repoonboard.model import (
    CachingGenerator,
    GenerationError,
    RecordedGenerator,
    tighten_schema,
)
from repoonboard.snippets import build

SOURCE = b'''"""Service layer for users."""

import os
from pathlib import Path


def load_user(user_id):
    """Fetch a user by id."""
    return {"id": user_id}


class UserCache:
    def get(self, key):
        return None
'''

KNOWN = frozenset({"app/service.py", "app/models.py"})
SIGNALS = {"fan_in_normalized": 0.2, "pagerank_reversed": 0.05, "doc_signal": 0.0}


@pytest.fixture
def snippet():
    return build(Path("app/service.py"), SOURCE, "python")


def _good_payload() -> str:
    return json.dumps(
        {
            "summary": "Defines `load_user` and a `UserCache` class.",
            "why_it_matters": "Several modules import it.",
            "questions": [
                {
                    "prompt": "What does `load_user` return?",
                    "answer_location": {
                        "path": "app/service.py",
                        "start_line": 7,
                        "end_line": 9,
                    },
                },
                {
                    "prompt": "What does `UserCache.get` return?",
                    "answer_location": {
                        "path": "app/service.py",
                        "start_line": 12,
                        "end_line": 14,
                    },
                },
            ],
        }
    )


def _ungrounded_payload() -> str:
    return json.dumps(
        {
            "summary": "Delegates to `deleteEverything` in `app/ghost.py`.",
            "why_it_matters": "It is critical.",
            "questions": [
                {
                    "prompt": "What does `deleteEverything` do?",
                    "answer_location": {
                        "path": "app/service.py",
                        "start_line": 900,
                        "end_line": 901,
                    },
                }
            ],
        }
    )


def _run(snippet, generator):
    return generate_station(
        snippet, "core", SIGNALS, {"imported by": "3 files"}, KNOWN, generator
    )


# ---------------------------------------------------------------------------
# The three paths through the ladder
# ---------------------------------------------------------------------------


def test_good_first_attempt_is_accepted(snippet):
    result = _run(snippet, RecordedGenerator([_good_payload()]))
    assert result.provenance is Provenance.MODEL
    assert result.attempts == 1
    assert result.rejections == ()


def test_rejected_then_corrected_is_marked_as_a_retry(snippet):
    generator = RecordedGenerator([_ungrounded_payload(), _good_payload()])
    result = _run(snippet, generator)
    assert result.provenance is Provenance.MODEL_RETRY
    assert result.attempts == 2
    assert len(generator.calls) == 2


def test_retry_prompt_quotes_the_rejection_reasons(snippet):
    generator = RecordedGenerator([_ungrounded_payload(), _good_payload()])
    _run(snippet, generator)

    retry_prompt = generator.calls[1][1]
    assert "deleteEverything" in retry_prompt
    assert "unknown_symbol" in retry_prompt


def test_two_failures_fall_back_to_structural_content(snippet):
    generator = RecordedGenerator([_ungrounded_payload(), _ungrounded_payload()])
    result = _run(snippet, generator)
    assert result.provenance is Provenance.STRUCTURAL
    assert result.rejections, "the reason for falling back must be retained"


def test_the_model_is_never_asked_a_third_time(snippet):
    generator = RecordedGenerator([_ungrounded_payload(), _ungrounded_payload()])
    _run(snippet, generator)
    assert len(generator.calls) == 2


def test_unreachable_model_falls_back_instead_of_raising(snippet):
    class Broken:
        def complete(self, system, user, schema):
            raise GenerationError("no network")

    result = _run(snippet, Broken())
    assert result.provenance is Provenance.STRUCTURAL


def test_no_generator_runs_the_structural_path(snippet):
    result = generate_station(snippet, "core", SIGNALS, {}, KNOWN, None)
    assert result.provenance is Provenance.STRUCTURAL
    assert result.attempts == 0


# ---------------------------------------------------------------------------
# The invariant that makes the fallback safe to ship
# ---------------------------------------------------------------------------


def test_structural_output_passes_the_gate(snippet):
    result = generate_station(snippet, "core", SIGNALS, {}, KNOWN, None)
    assert check(result.explanation, snippet, KNOWN).ok


def test_structural_output_passes_the_gate_without_a_docstring():
    source = b"import os\n\n\ndef alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"
    snippet = build(Path("app/plain.py"), source, "python")
    result = generate_station(
        snippet, "core", SIGNALS, {}, frozenset({"app/plain.py"}), None
    )
    assert check(result.explanation, snippet, frozenset({"app/plain.py"})).ok


def test_structural_output_passes_the_gate_with_no_symbols():
    source = b"import os\n\nCONFIG = {'a': 1}\nVALUE = CONFIG['a']\n"
    snippet = build(Path("app/config.py"), source, "python")
    known = frozenset({"app/config.py"})
    result = generate_station(snippet, "core", SIGNALS, {}, known, None)

    assert check(result.explanation, snippet, known).ok
    assert len(result.explanation.questions) >= 2


def test_structural_questions_never_target_import_lines():
    source = b"import os\nimport sys\n\n\ndef alpha():\n    return 1\n"
    snippet = build(Path("app/a.py"), source, "python")
    result = generate_station(snippet, "core", SIGNALS, {}, frozenset({"app/a.py"}), None)

    for question in result.explanation.questions:
        cited = set(
            range(
                question.answer_location.start_line,
                question.answer_location.end_line + 1,
            )
        )
        assert not cited <= snippet.import_lines


def test_a_pure_reexport_barrel_is_refused_rather_than_faked():
    source = b'export { a } from "./a";\nexport { b } from "./b";\n'
    snippet = build(Path("src/index.ts"), source, "typescript")

    with pytest.raises(UnverifiableStation):
        generate_station(snippet, "core", SIGNALS, {}, frozenset({"src/index.ts"}), None)


def test_structural_output_is_deterministic(snippet):
    first = generate_station(snippet, "core", SIGNALS, {}, KNOWN, None)
    second = generate_station(snippet, "core", SIGNALS, {}, KNOWN, None)
    assert first.explanation == second.explanation


# ---------------------------------------------------------------------------
# Malformed model output
# ---------------------------------------------------------------------------


def test_unparseable_json_is_treated_as_a_rejection(snippet):
    generator = RecordedGenerator(["not json at all", _good_payload()])
    result = _run(snippet, generator)
    assert result.provenance is Provenance.MODEL_RETRY


def test_json_missing_a_required_field_is_rejected(snippet):
    bad = json.dumps({"summary": "Something."})
    result = _run(snippet, RecordedGenerator([bad, bad]))
    assert result.provenance is Provenance.STRUCTURAL
    assert any(r.code == "invalid_shape" for r in result.rejections)


def test_a_fenced_payload_is_still_parsed(snippet):
    fenced = f"```json\n{_good_payload()}\n```"
    result = _run(snippet, RecordedGenerator([fenced]))
    assert result.provenance is Provenance.MODEL


# ---------------------------------------------------------------------------
# Schema and cache
# ---------------------------------------------------------------------------


def test_schema_forbids_extra_properties_everywhere():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert node["additionalProperties"] is False
                assert sorted(node["properties"]) == node["required"]
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(draft_schema())


def test_tighten_schema_does_not_mutate_its_input():
    original = StationDraft.model_json_schema()
    snapshot = json.dumps(original, sort_keys=True)
    tighten_schema(original)
    assert json.dumps(original, sort_keys=True) == snapshot


def test_cache_returns_the_recorded_answer_without_a_second_call(tmp_path):
    inner = RecordedGenerator([_good_payload()])
    cached = CachingGenerator(inner, tmp_path / "cache")

    first = cached.complete("sys", "user", {"a": 1})
    second = cached.complete("sys", "user", {"a": 1})

    assert first == second
    assert len(inner.calls) == 1, "the second call must be served from disk"


def test_cache_distinguishes_different_prompts(tmp_path):
    inner = RecordedGenerator([_good_payload(), _good_payload()])
    cached = CachingGenerator(inner, tmp_path / "cache")

    cached.complete("sys", "user one", {})
    cached.complete("sys", "user two", {})

    assert len(inner.calls) == 2


def test_recorded_generator_refuses_to_invent_a_response():
    generator = RecordedGenerator([])
    with pytest.raises(GenerationError):
        generator.complete("sys", "user", {})
