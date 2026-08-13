"""Import extraction and resolution.

Two separate concerns live here on purpose:

1. `extract` walks a parsed file and pulls out raw import specifiers exactly
   as written — "app.core", "./widget", "../../lib/auth" — with no attempt
   at resolution yet.
2. `resolve` turns a raw specifier plus the file that wrote it into an actual
   path inside the repository, or gives up honestly (`None`) when it can't.

Splitting them means resolution failures are visible and countable instead of
silently dropped, which is what lets the CLI print an honest accuracy rate
instead of pretending the graph is complete.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser

_PARSERS: dict[str, Parser] = {}

# Extensions tried, in order, when a JS/TS specifier omits one.
_JS_SUFFIXES: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_JS_INDEX_NAMES: tuple[str, ...] = tuple(f"index{suffix}" for suffix in _JS_SUFFIXES)


def parser_for(language: str) -> Parser:
    """Return a cached tree-sitter parser for a supported language.

    Public because snippet extraction parses the same files for a different
    purpose and must not build a second set of parsers.
    """
    if language not in _PARSERS:
        if language == "python":
            _PARSERS[language] = Parser(Language(tspython.language()))
        elif language == "javascript":
            _PARSERS[language] = Parser(Language(tsjavascript.language()))
        elif language == "typescript":
            # .tsx and .ts share a grammar family; the TSX grammar is a
            # strict superset, so use it for both to keep one parser.
            _PARSERS[language] = Parser(Language(tstypescript.language_tsx()))
        else:
            raise ValueError(f"Unsupported language: {language}")
    return _PARSERS[language]


@dataclass(frozen=True)
class RawImport:
    """An import specifier exactly as it appeared in the source."""

    specifier: str
    relative_level: int  # Python only: number of leading dots. 0 for absolute/JS.


def extract(source_bytes: bytes, language: str) -> list[RawImport]:
    """Pull every import specifier out of a file. No resolution here."""
    parser = parser_for(language)
    tree = parser.parse(source_bytes)

    if language == "python":
        return _extract_python(tree.root_node, source_bytes)
    return _extract_js(tree.root_node, source_bytes)


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _extract_python(root: Node, source: bytes) -> list[RawImport]:
    found: list[RawImport] = []

    def visit(node: Node) -> None:
        if node.type == "import_statement":
            # import a.b, c.d as e
            for child in node.children:
                if child.type == "dotted_name":
                    found.append(RawImport(_text(child, source), 0))
                elif child.type == "aliased_import":
                    name = child.child_by_field_name("name")
                    if name is not None:
                        found.append(RawImport(_text(name, source), 0))

        elif node.type == "import_from_statement":
            module_node = node.child_by_field_name("module_name")

            if module_node is not None and module_node.type == "relative_import":
                # "from .utils import x" / "from ..pkg.sub import x" / "from . import x"
                # — dots and the dotted name after them are nested inside a
                # single relative_import node, not siblings of it.
                level = 0
                name = ""
                for child in module_node.children:
                    if child.type == "import_prefix":
                        level = _text(child, source).count(".")
                    elif child.type == "dotted_name":
                        name = _text(child, source)
                found.append(RawImport(name, level))

            elif module_node is not None:
                found.append(RawImport(_text(module_node, source), 0))

        for child in node.children:
            visit(child)

    visit(root)
    return found


_JS_IMPORT_NODE_TYPES = frozenset({"import_statement", "export_statement", "call_expression"})


def _extract_js(root: Node, source: bytes) -> list[RawImport]:
    found: list[RawImport] = []

    def visit(node: Node) -> None:
        if node.type in ("import_statement", "export_statement"):
            source_node = node.child_by_field_name("source")
            if source_node is not None:
                found.append(RawImport(_unquote(_text(source_node, source)), 0))

        elif node.type == "call_expression":
            callee = node.child_by_field_name("function")
            if callee is not None and _text(callee, source) in ("require", "import"):
                args = node.child_by_field_name("arguments")
                if args is not None and args.named_child_count > 0:
                    first = args.named_children[0]
                    if first.type == "string":
                        found.append(RawImport(_unquote(_text(first, source)), 0))

        for child in node.children:
            visit(child)

    visit(root)
    return found


def _unquote(literal: str) -> str:
    if len(literal) >= 2 and literal[0] in "'\"`" and literal[-1] == literal[0]:
        return literal[1:-1]
    return literal


# ---------------------------------------------------------------------------
# Resolution: raw specifier -> an actual file inside the repository.
# ---------------------------------------------------------------------------


def resolve_python(
    raw: RawImport, importing_file: Path, known_files: frozenset[Path]
) -> Path | None:
    """Resolve a Python import to a file already discovered in the repo.

    External packages (stdlib, third-party) resolve to None by design — an
    unresolved import isn't necessarily a bug, it may simply be outside the
    repository.
    """
    if raw.relative_level > 0:
        base = importing_file.parent
        for _ in range(raw.relative_level - 1):
            base = base.parent
        parts = raw.specifier.split(".") if raw.specifier else []
        return _first_existing_python(base, parts, known_files)

    parts = raw.specifier.split(".")
    candidate_roots = _python_roots(known_files)
    for root in candidate_roots:
        hit = _first_existing_python(root, parts, known_files)
        if hit is not None:
            return hit
    return None


def _python_roots(known_files: frozenset[Path]) -> list[Path]:
    """Directories a dotted import might be rooted at.

    Always tries the repo root, plus any top-level directory that looks like
    a src-layout package root (contains __init__.py at its own level).
    """
    roots = [Path(".")]
    top_level_dirs = {f.parts[0] for f in known_files if len(f.parts) > 1}
    for name in sorted(top_level_dirs):
        if name in ("src", "lib"):
            roots.append(Path(name))
    return roots


def _first_existing_python(
    base: Path, parts: list[str], known_files: frozenset[Path]
) -> Path | None:
    if not parts:
        candidate = (base / "__init__.py").as_posix()
        match = _match(candidate, known_files)
        return match

    module_path = base.joinpath(*parts)
    for candidate in (
        f"{module_path.as_posix()}.py",
        f"{module_path.as_posix()}/__init__.py",
    ):
        match = _match(candidate, known_files)
        if match is not None:
            return match
    return None


def _match(posix_candidate: str, known_files: frozenset[Path]) -> Path | None:
    normalised = posixpath.normpath(posix_candidate)
    for known in known_files:
        if known.as_posix() == normalised:
            return known
    return None


def resolve_js(raw: RawImport, importing_file: Path, known_files: frozenset[Path]) -> Path | None:
    """Resolve a JS/TS relative import to a file already discovered.

    Bare specifiers ("react", "@scope/pkg") are external packages and always
    resolve to None — they're outside the repository by definition, not a
    resolution failure to report.
    """
    specifier = raw.specifier
    if not (specifier.startswith("./") or specifier.startswith("../")):
        return None

    # Pure string arithmetic on repo-relative posix paths — never touches the
    # real filesystem or the process's cwd, so this stays testable and safe
    # to call against any directory structure, including one that doesn't
    # exist on disk (e.g. in tests).
    joined = posixpath.normpath(posixpath.join(importing_file.parent.as_posix(), specifier))

    for suffix in _JS_SUFFIXES:
        match = _match(f"{joined}{suffix}", known_files)
        if match is not None:
            return match

    for index_name in _JS_INDEX_NAMES:
        match = _match(posixpath.join(joined, index_name), known_files)
        if match is not None:
            return match

    return None
