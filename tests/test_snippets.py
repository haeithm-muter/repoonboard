from pathlib import Path

import pytest

from repoonboard.snippets import (
    Region,
    build,
    extract_symbols,
    import_line_numbers,
    module_docstring,
)

PY_SOURCE = b'''"""Module docstring.

Second paragraph.
"""

import os
from pathlib import Path

CONSTANT = 3


def alpha(x):
    return x + 1


class Beta:
    def method(self):
        return 2
'''

TS_SOURCE = b"""/**
 * Leading block comment.
 */
import { thing } from "./thing";
export { other } from "./other";

export function alpha(x: number): number {
  return x + 1;
}

export class Beta {
  method(): number {
    return 2;
  }
}

interface Gamma {
  field: string;
}
"""


# ---------------------------------------------------------------------------
# Docstrings
# ---------------------------------------------------------------------------


def test_python_module_docstring_is_extracted():
    assert module_docstring(PY_SOURCE, "python").startswith("Module docstring.")


def test_typescript_leading_block_comment_is_extracted():
    assert module_docstring(TS_SOURCE, "typescript") == "Leading block comment."


def test_missing_docstring_returns_none():
    assert module_docstring(b"x = 1\n", "python") is None


def test_first_statement_string_only_counts_at_module_level():
    assert module_docstring(b"x = 1\n'not a docstring'\n", "python") is None


# ---------------------------------------------------------------------------
# Import lines — the set a station must never be targeted at
# ---------------------------------------------------------------------------


def test_python_import_lines_are_reported_one_based():
    assert import_line_numbers(PY_SOURCE, "python") == frozenset({6, 7})


def test_typescript_import_and_reexport_lines_are_reported():
    assert import_line_numbers(TS_SOURCE, "typescript") == frozenset({4, 5})


def test_require_call_counts_as_an_import_line():
    source = b"const x = require('./x');\nconst y = 2;\n"
    assert import_line_numbers(source, "javascript") == frozenset({1})


def test_multiline_import_marks_every_line_it_spans():
    source = b"from pathlib import (\n    Path,\n    PurePath,\n)\nx = 1\n"
    assert import_line_numbers(source, "python") == frozenset({1, 2, 3, 4})


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------


def test_python_top_level_symbols_are_found_in_source_order():
    symbols = extract_symbols(PY_SOURCE, "python")
    assert [(s.name, s.kind) for s in symbols] == [("alpha", "function"), ("Beta", "class")]


def test_nested_definitions_are_not_reported():
    # `method` sits inside Beta; v1 is file-level, not function-level.
    assert "method" not in {s.name for s in extract_symbols(PY_SOURCE, "python")}


def test_symbol_line_span_matches_the_source():
    alpha = next(s for s in extract_symbols(PY_SOURCE, "python") if s.name == "alpha")
    assert (alpha.start_line, alpha.end_line) == (12, 13)


def test_decorated_definition_is_unwrapped():
    source = b"import typer\n\n@app.command()\ndef run():\n    return 1\n"
    assert [s.name for s in extract_symbols(source, "python")] == ["run"]


def test_exported_typescript_declarations_are_found():
    names = [s.name for s in extract_symbols(TS_SOURCE, "typescript")]
    assert names == ["alpha", "Beta", "Gamma"]


@pytest.mark.parametrize("language", ["python", "typescript", "javascript"])
def test_empty_source_yields_no_symbols(language: str):
    assert extract_symbols(b"", language) == ()


# ---------------------------------------------------------------------------
# Snippet assembly
# ---------------------------------------------------------------------------


def test_small_file_is_shown_whole():
    snippet = build(Path("a.py"), PY_SOURCE, "python")
    assert snippet.regions == (Region(1, snippet.line_count),)
    assert snippet.covers(1) and snippet.covers(snippet.line_count)


def test_rendered_text_carries_real_line_numbers():
    snippet = build(Path("a.py"), PY_SOURCE, "python")
    assert "    6 | import os" in snippet.text


def test_covers_range_rejects_lines_beyond_the_file():
    snippet = build(Path("a.py"), PY_SOURCE, "python")
    assert snippet.covers_range(1, snippet.line_count) is True
    assert snippet.covers_range(1, snippet.line_count + 5) is False


def test_covers_range_rejects_an_inverted_range():
    snippet = build(Path("a.py"), PY_SOURCE, "python")
    assert snippet.covers_range(10, 4) is False


def test_large_file_is_reduced_but_keeps_the_header():
    body = b"".join(b"def f%d():\n    return %d\n\n" % (i, i) for i in range(200))
    source = b'"""Doc."""\n\nimport os\n\n' + body
    snippet = build(Path("big.py"), source, "python", budget=60)

    assert snippet.line_count > 60
    assert snippet.covers(1), "header must survive reduction"
    shown = sum(r.end_line - r.start_line + 1 for r in snippet.regions)
    assert shown < snippet.line_count, "a file over budget must actually be reduced"


def test_elision_is_marked_explicitly():
    body = b"".join(b"def f%d():\n    return %d\n\n" % (i, i) for i in range(200))
    source = b'"""Doc."""\n\nimport os\n\n' + body
    snippet = build(Path("big.py"), source, "python", budget=60)
    assert "omitted ..." in snippet.text


def test_reduced_snippet_never_claims_to_cover_an_omitted_line():
    body = b"".join(b"def f%d():\n    return %d\n\n" % (i, i) for i in range(200))
    source = b'"""Doc."""\n\nimport os\n\n' + body
    snippet = build(Path("big.py"), source, "python", budget=60)

    covered = {line for line in range(1, snippet.line_count + 1) if snippet.covers(line)}
    from_regions = {
        line for r in snippet.regions for line in range(r.start_line, r.end_line + 1)
    }
    assert covered == from_regions


def test_snippet_is_deterministic():
    first = build(Path("a.py"), PY_SOURCE, "python")
    second = build(Path("a.py"), PY_SOURCE, "python")
    assert first == second


def test_empty_file_produces_an_empty_snippet():
    snippet = build(Path("empty.py"), b"", "python")
    assert snippet.regions == ()
    assert snippet.text == ""
    assert snippet.covers(1) is False
