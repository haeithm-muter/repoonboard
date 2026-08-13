"""Probe candidate replacements for the dead CONTRIBUTING.md source.

Measures, for each pinned repository, how many real source paths each
candidate yields — using the same extractor and the same `known` filter the
scoring harness uses, so the numbers are directly comparable to the ones that
condemned CONTRIBUTING.md.

A candidate is only worth adopting if it (a) yields paths on most of the four
repositories and (b) is independent of churn, since the independent column is
the number the README asks the reader to believe.

    python eval/probe_sources.py
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from repoonboard.discovery import discover
from repoonboard.evaluation import extract_paths

REPOS = ROOT / "eval" / "repos.toml"
CACHE = ROOT / ".eval-cache"

DOC_SUFFIXES = (".md", ".rst", ".mdx", ".txt")

# A dotted module reference such as `scrapy.core.engine`, which is how Python
# documentation names files. The path extractor requires a slash and therefore
# cannot see these at all.
_DOTTED = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*){1,6})\b")


def dotted_paths(text: str, known: frozenset[str]) -> set[str]:
    """Resolve dotted module references to real files."""
    found: set[str] = set()
    for match in _DOTTED.findall(text or ""):
        parts = match.split(".")
        for start in range(len(parts) - 1):
            stem = "/".join(parts[start:])
            for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
                if candidate in known:
                    found.add(candidate)
                    break
            for prefix in ("src/",):
                for candidate in (f"{prefix}{stem}.py", f"{prefix}{stem}/__init__.py"):
                    if candidate in known:
                        found.add(candidate)
                        break
    return found


def read_many(paths: list[Path]) -> str:
    chunks = []
    for path in paths:
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)


def docs_tree(root: Path, exclude: set[str]) -> list[Path]:
    docs = root / "docs"
    if not docs.is_dir():
        return []
    return [
        path
        for path in sorted(docs.rglob("*"))
        if path.is_file()
        and path.suffix.lower() in DOC_SUFFIXES
        and path.relative_to(root).as_posix() not in exclude
    ]


def find_first(root: Path, names: list[str]) -> Path | None:
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def main() -> int:
    config = tomllib.loads(REPOS.read_text(encoding="utf-8"))
    report = []

    for entry in config["repo"]:
        name = entry["name"]
        root = CACHE / name
        if not root.is_dir():
            print(f"{name}: not cloned; run eval/fetch.py first")
            return 1

        known = frozenset(item.posix for item in discover(root) if not item.is_test)
        exclude = {entry.get("contributing_path", "")}

        docs_files = docs_tree(root, exclude)
        readme = find_first(root, ["README.md", "README.rst", "readme.md"])
        codeowners = find_first(
            root, [".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"]
        )
        architecture = find_first(
            root,
            [
                "ARCHITECTURE.md",
                "docs/architecture.md",
                "docs/ARCHITECTURE.md",
                "docs/architecture.rst",
                "docs/design.md",
                "docs/internals.rst",
                "docs/topics/architecture.rst",
            ],
        )

        # Widest honest candidate: every prose file anywhere in the tree.
        # kysely keeps its documentation in site/ and hono keeps its in a
        # separate repository, so looking only in docs/ understates both.
        all_prose = [
            path
            for path in sorted(root.rglob("*"))
            if path.is_file()
            and path.suffix.lower() in DOC_SUFFIXES
            and ".git" not in path.parts
            and "node_modules" not in path.parts
            and path.relative_to(root).as_posix() not in exclude
        ]

        candidates: dict[str, tuple[str, bool]] = {
            "all prose (paths)": (read_many(all_prose), False),
            "all prose (dotted modules)": (read_many(all_prose), True),
            "docs tree (paths)": (read_many(docs_files), False),
            "README (paths)": (read_many([readme]) if readme else "", False),
            "CODEOWNERS (paths)": (read_many([codeowners]) if codeowners else "", False),
            "ARCHITECTURE doc (paths)": (
                read_many([architecture]) if architecture else "",
                False,
            ),
            "docs tree (dotted modules)": (read_many(docs_files), True),
            "README (dotted modules)": (read_many([readme]) if readme else "", True),
        }

        row = {
            "name": name,
            "language": entry["language"],
            "docs_files": len(docs_files),
            "prose_files": len(all_prose),
            "has_readme": readme is not None,
            "has_codeowners": codeowners.relative_to(root).as_posix() if codeowners else None,
            "has_architecture_doc": (
                architecture.relative_to(root).as_posix() if architecture else None
            ),
            "yields": {},
        }

        for label, (text, use_dotted) in candidates.items():
            found = dotted_paths(text, known) if use_dotted else extract_paths(text, known)
            row["yields"][label] = sorted(found)

        report.append(row)

        print(f"\n{name} ({entry['language']}, {len(known)} source files)")
        print(f"  docs/ files: {row['docs_files']}   README: {row['has_readme']}")
        print(f"  CODEOWNERS: {row['has_codeowners']}")
        print(f"  architecture doc: {row['has_architecture_doc']}")
        for label in candidates:
            print(f"    {label:28} -> {len(row['yields'][label]):3} paths")

    output = ROOT / "eval" / "source_probe.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print("\n\nsummary: repositories yielding at least one path per candidate")
    labels = list(report[0]["yields"]) if report else []
    for label in labels:
        hits = [row["name"] for row in report if row["yields"][label]]
        print(f"  {label:28} {len(hits)}/4  {hits}")
    print(f"\nwrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
