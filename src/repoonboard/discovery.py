"""Repository file discovery and filtering.

This module is deliberately free of any model call. Everything here is
derived from the file system and from naming conventions only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Directories that never contain source worth reading during onboarding.
EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "dist",
        "build",
        "out",
        "target",
        ".next",
        ".nuxt",
        ".cache",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "coverage",
        "htmlcov",
        "vendor",
        "third_party",
        "migrations",
        "site-packages",
        ".tox",
        ".idea",
        ".vscode",
    }
)

# Suffixes that mark a file as generated rather than authored.
GENERATED_MARKERS: tuple[str, ...] = (
    ".min.js",
    ".min.css",
    ".bundle.js",
    ".d.ts",
    "_pb2.py",
    "_pb2_grpc.py",
    ".pb.go",
    ".generated.ts",
    ".g.dart",
)

LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}

# Maximum files before the tool refuses a whole-repo run (see CLAUDE.md).
MAX_FILES = 1500


@dataclass(frozen=True)
class SourceFile:
    """A single candidate source file inside the analysed repository."""

    path: Path  # relative to the repository root
    language: str
    is_test: bool
    line_count: int

    @property
    def posix(self) -> str:
        return self.path.as_posix()


def is_generated(path: Path) -> bool:
    """True when the file looks machine-written rather than authored."""
    name = path.name
    return any(name.endswith(marker) for marker in GENERATED_MARKERS)


def is_test_file(path: Path) -> bool:
    """True when the file is a test.

    Test files are excluded from being stations but stay in the dependency
    graph: what a team tests reveals what a team considers critical.
    """
    parts = {part.lower() for part in path.parts[:-1]}
    if parts & {"test", "tests", "__tests__", "spec", "e2e", "testing"}:
        return True

    stem = path.stem.lower()
    if stem.startswith("test_") or stem.endswith("_test"):
        return True
    return any(stem.endswith(suffix) for suffix in (".test", ".spec"))


def _is_excluded(relative: Path) -> bool:
    return any(part in EXCLUDED_DIRS for part in relative.parts)


def _count_lines(absolute: Path) -> int:
    try:
        with absolute.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def discover(root: Path, subdir: str | None = None) -> list[SourceFile]:
    """Walk the repository and return every candidate source file.

    Results are sorted by path so that two runs over the same commit produce
    byte-identical output. Determinism is a requirement, not a nicety.
    """
    root = root.resolve()
    base = root / subdir if subdir else root
    if not base.is_dir():
        raise NotADirectoryError(f"Not a directory: {base}")

    found: list[SourceFile] = []
    for absolute in base.rglob("*"):
        if not absolute.is_file() or absolute.is_symlink():
            continue

        language = LANGUAGE_BY_SUFFIX.get(absolute.suffix.lower())
        if language is None:
            continue

        relative = absolute.relative_to(root)
        if _is_excluded(relative) or is_generated(relative):
            continue

        found.append(
            SourceFile(
                path=relative,
                language=language,
                is_test=is_test_file(relative),
                line_count=_count_lines(absolute),
            )
        )

    found.sort(key=lambda item: item.posix)
    return found


def dominant_languages(files: list[SourceFile]) -> dict[str, int]:
    """Count of non-test files per language, largest first."""
    counts: dict[str, int] = {}
    for item in files:
        if item.is_test:
            continue
        counts[item.language] = counts.get(item.language, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: pair[1], reverse=True))
