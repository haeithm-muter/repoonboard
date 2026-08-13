"""Deterministic context extraction for a station.

The model is shown exactly one thing about a file: the object built here.
Nothing else about the repository reaches it. That is what makes the grounding
gate meaningful — "this symbol was never shown to you" is a decidable
statement only because the shown region is recorded precisely.

Two properties matter more than richness:

1. **Line numbers are real.** Every region carries the line range it occupied
   in the original file, so a question's answer location can be checked
   against the file rather than trusted.
2. **The result is a pure function of the file bytes.** No clock, no
   filesystem walk, no set iteration order. The same commit yields the same
   snippet, because the tour is supposed to be reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Node

from .imports import parser_for

# Lines of source shown to the model before regions are elided. Chosen so a
# typical module arrives whole; larger files fall back to selected regions.
DEFAULT_BUDGET = 160

_PY_SYMBOL_TYPES = {
    "function_definition": "function",
    "class_definition": "class",
}

_JS_SYMBOL_TYPES = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
}


@dataclass(frozen=True)
class Symbol:
    """A named top-level definition and the lines it spans."""

    name: str
    kind: str
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive


@dataclass(frozen=True)
class Region:
    """A contiguous run of lines from the original file. 1-based, inclusive."""

    start_line: int
    end_line: int

    def contains(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line


@dataclass(frozen=True)
class Snippet:
    """Everything the model is allowed to see about one file."""

    path: Path
    language: str
    line_count: int
    docstring: str | None
    symbols: tuple[Symbol, ...]
    import_lines: frozenset[int]
    regions: tuple[Region, ...]
    text: str

    def covers(self, line: int) -> bool:
        """True when a line was actually shown to the model."""
        return any(region.contains(line) for region in self.regions)

    def covers_range(self, start_line: int, end_line: int) -> bool:
        """True when every line in the range was shown to the model."""
        if start_line > end_line:
            return False
        return all(self.covers(line) for line in range(start_line, end_line + 1))

    def symbol_names(self) -> frozenset[str]:
        return frozenset(symbol.name for symbol in self.symbols)


def _text_of(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _named_child_text(node: Node, field: str, source: bytes) -> str | None:
    child = node.child_by_field_name(field)
    return _text_of(child, source) if child is not None else None


def import_line_numbers(source_bytes: bytes, language: str) -> frozenset[int]:
    """Every 1-based line occupied by an import statement.

    A station must never be targeted at an import line (CLAUDE.md), and a
    question whose answer is "the file imports X" verifies nothing. Both rules
    are enforced against this set.
    """
    tree = parser_for(language).parse(source_bytes)
    lines: set[int] = set()

    def visit(node: Node) -> None:
        is_import = node.type in ("import_statement", "import_from_statement")
        # `export ... from "./x"` re-exports are import edges too.
        if node.type == "export_statement" and node.child_by_field_name("source") is not None:
            is_import = True
        if node.type == "call_expression":
            callee = node.child_by_field_name("function")
            if callee is not None and _text_of(callee, source_bytes) in ("require", "import"):
                is_import = True

        if is_import:
            for row in range(node.start_point[0], node.end_point[0] + 1):
                lines.add(row + 1)

        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return frozenset(lines)


def module_docstring(source_bytes: bytes, language: str) -> str | None:
    """The module docstring for Python, or the leading block comment for JS/TS.

    Returned so an explanation can lean on what the authors already said about
    the file rather than paraphrasing the code back at the reader.
    """
    tree = parser_for(language).parse(source_bytes)
    root = tree.root_node
    if root.named_child_count == 0:
        return None

    first = root.named_children[0]

    if language == "python":
        if first.type == "expression_statement" and first.named_child_count > 0:
            literal = first.named_children[0]
            if literal.type == "string":
                return _strip_python_quotes(_text_of(literal, source_bytes))
        return None

    if first.type == "comment":
        return _strip_block_comment(_text_of(first, source_bytes))
    return None


def _strip_python_quotes(literal: str) -> str:
    for quote in ('"""', "'''"):
        if literal.startswith(quote) and literal.endswith(quote) and len(literal) >= 6:
            return literal[3:-3].strip()
    for quote in ('"', "'"):
        if literal.startswith(quote) and literal.endswith(quote) and len(literal) >= 2:
            return literal[1:-1].strip()
    return literal.strip()


def _strip_block_comment(comment: str) -> str:
    body = comment
    if body.startswith("/*"):
        body = body[2:]
        body = body.removeprefix("*")
    body = body.removesuffix("*/")

    cleaned = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("*"):
            stripped = stripped[1:].strip()
        cleaned.append(stripped)
    return "\n".join(cleaned).strip()


def extract_symbols(source_bytes: bytes, language: str) -> tuple[Symbol, ...]:
    """Top-level named definitions, in source order.

    Deliberately file-level and shallow: v1 does not do function-level
    analysis, so nested definitions are not reported. Sorting is by position,
    never by name, so the order matches how the file reads.
    """
    tree = parser_for(language).parse(source_bytes)
    table = _PY_SYMBOL_TYPES if language == "python" else _JS_SYMBOL_TYPES
    found: list[Symbol] = []

    def record(node: Node, kind: str) -> None:
        name = _named_child_text(node, "name", source_bytes)
        if name:
            found.append(
                Symbol(
                    name=name,
                    kind=kind,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                )
            )

    def visit(node: Node, depth: int) -> None:
        for child in node.children:
            kind = table.get(child.type)
            if kind is not None:
                record(child, kind)
                continue  # do not descend: nested defs are out of scope for v1

            # Unwrap the containers that only decorate or re-export a
            # definition, so `export class Foo` and `@app.command def bar`
            # are found at their real nesting level rather than missed.
            if child.type in ("decorated_definition", "export_statement", "lexical_declaration") or depth == 0 and child.type in ("expression_statement", "program", "module"):
                visit(child, depth)

    visit(tree.root_node, 0)
    found.sort(key=lambda symbol: (symbol.start_line, symbol.name))
    return tuple(found)


def _merge(regions: list[Region]) -> tuple[Region, ...]:
    """Sort and coalesce overlapping or adjacent regions."""
    if not regions:
        return ()
    ordered = sorted(regions, key=lambda region: (region.start_line, region.end_line))
    merged = [ordered[0]]
    for region in ordered[1:]:
        last = merged[-1]
        if region.start_line <= last.end_line + 1:
            merged[-1] = Region(last.start_line, max(last.end_line, region.end_line))
        else:
            merged.append(region)
    return tuple(merged)


def _render(source_lines: list[str], regions: tuple[Region, ...]) -> str:
    """Render regions as numbered lines, marking every elision explicitly.

    Line numbers are part of the payload, not decoration: the model is asked
    to cite them, and the gate checks what it cites.
    """
    out: list[str] = []
    previous_end = 0
    for region in regions:
        if previous_end and region.start_line > previous_end + 1:
            out.append(f"... lines {previous_end + 1}-{region.start_line - 1} omitted ...")
        for line_number in range(region.start_line, region.end_line + 1):
            out.append(f"{line_number:>5} | {source_lines[line_number - 1]}")
        previous_end = region.end_line
    if previous_end and previous_end < len(source_lines):
        out.append(f"... lines {previous_end + 1}-{len(source_lines)} omitted ...")
    return "\n".join(out)


def build(
    path: Path, source_bytes: bytes, language: str, budget: int = DEFAULT_BUDGET
) -> Snippet:
    """Build the snippet for one file.

    Files within budget are shown whole. Larger files are reduced to the
    regions that carry meaning — the header, and as many top-level definitions
    as fit, largest-first so the substantial ones survive — then re-sorted into
    reading order.
    """
    source_lines = source_bytes.decode("utf-8", errors="replace").splitlines()
    line_count = len(source_lines)
    symbols = extract_symbols(source_bytes, language)
    imports = import_line_numbers(source_bytes, language)
    docstring = module_docstring(source_bytes, language)

    if line_count == 0:
        return Snippet(path, language, 0, docstring, symbols, imports, (), "")

    if line_count <= budget:
        regions = (Region(1, line_count),)
        return Snippet(
            path=path,
            language=language,
            line_count=line_count,
            docstring=docstring,
            symbols=symbols,
            import_lines=imports,
            regions=regions,
            text=_render(source_lines, regions),
        )

    # Always keep the header: the docstring and the imports establish what the
    # file is and what it depends on.
    header_end = min(max(imports, default=0) or 1, budget // 4, line_count)
    chosen: list[Region] = [Region(1, max(header_end, 1))]
    used = header_end

    for symbol in sorted(symbols, key=lambda s: s.start_line - s.end_line):
        span = symbol.end_line - symbol.start_line + 1
        if used + span > budget:
            continue
        chosen.append(Region(symbol.start_line, symbol.end_line))
        used += span

    regions = _merge(chosen)
    return Snippet(
        path=path,
        language=language,
        line_count=line_count,
        docstring=docstring,
        symbols=symbols,
        import_lines=imports,
        regions=regions,
        text=_render(source_lines, regions),
    )


def build_for_file(root: Path, relative: Path, language: str, budget: int = DEFAULT_BUDGET) -> Snippet:
    """Read a file from disk and build its snippet."""
    return build(relative, (root / relative).read_bytes(), language, budget)
