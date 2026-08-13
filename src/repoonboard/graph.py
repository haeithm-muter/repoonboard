"""Dependency graph and importance scoring.

Nothing here calls a model. Everything is derived from the import graph and
Git signals, per the architectural law in CLAUDE.md.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx

from .discovery import SourceFile, discover
from .git_signals import churn as git_churn
from .git_signals import is_git_repository
from .imports import extract, resolve_js, resolve_python

_WEIGHTS_PATH = Path(__file__).parent / "weights.toml"


@dataclass(frozen=True)
class Weights:
    pagerank_reversed: float
    fan_in_normalized: float
    entry_proximity: float
    churn_normalized: float
    test_coverage: float
    doc_signal: float
    entry_threshold: int
    entry_manifest: int
    entry_conventional_name: int
    entry_main_guard: int
    entry_zero_in_high_out: int
    entry_dockerfile: int
    entry_readme: int


def load_weights(path: Path = _WEIGHTS_PATH) -> Weights:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    importance = data["importance"]
    entry = data["entry_points"]
    return Weights(
        pagerank_reversed=importance["pagerank_reversed"],
        fan_in_normalized=importance["fan_in_normalized"],
        entry_proximity=importance["entry_proximity"],
        churn_normalized=importance["churn_normalized"],
        test_coverage=importance["test_coverage"],
        doc_signal=importance["doc_signal"],
        entry_threshold=entry["threshold"],
        entry_manifest=entry["named_in_manifest"],
        entry_conventional_name=entry["conventional_filename"],
        entry_main_guard=entry["main_guard_or_app_init"],
        entry_zero_in_high_out=entry["zero_in_degree_high_out"],
        entry_dockerfile=entry["dockerfile_or_makefile"],
        entry_readme=entry["named_in_readme_codeblock"],
    )


@dataclass
class RepoGraph:
    root: Path
    files: list[SourceFile]
    graph: nx.DiGraph  # edges A -> B mean "A imports B"
    resolved_edges: int
    unresolved_edges: int
    entry_points: set[Path]
    scores: dict[Path, float] = field(default_factory=dict)
    score_breakdown: dict[Path, dict[str, float]] = field(default_factory=dict)

    @property
    def resolution_rate(self) -> float:
        total = self.resolved_edges + self.unresolved_edges
        return self.resolved_edges / total if total else 1.0


_CONVENTIONAL_ENTRY_NAMES = frozenset(
    {"main.py", "app.py", "index.ts", "index.js", "server.ts", "server.js", "cli.py"}
)


def build(root: Path, subdir: str | None = None, weights: Weights | None = None) -> RepoGraph:
    """Discover files, extract imports, resolve them, and score every node."""
    weights = weights or load_weights()
    files = discover(root, subdir)
    known = frozenset(item.path for item in files)

    graph = nx.DiGraph()
    for item in files:
        graph.add_node(item.path, is_test=item.is_test, language=item.language)

    resolved = 0
    unresolved = 0
    for item in files:
        absolute = root / item.path
        try:
            source_bytes = absolute.read_bytes()
        except OSError:
            continue

        for raw in extract(source_bytes, item.language):
            target = (
                resolve_python(raw, item.path, known)
                if item.language == "python"
                else resolve_js(raw, item.path, known)
            )
            if target is not None and target != item.path:
                graph.add_edge(item.path, target)
                resolved += 1
            elif _looks_internal(raw, item.language):
                unresolved += 1
            # else: external package — not counted as a resolution failure

    entry_points = _detect_entry_points(files, graph, weights)
    churn = git_churn(root) if is_git_repository(root) else {}

    repo_graph = RepoGraph(
        root=root,
        files=files,
        graph=graph,
        resolved_edges=resolved,
        unresolved_edges=unresolved,
        entry_points=entry_points,
    )
    _score(repo_graph, weights, churn)
    return repo_graph


def _looks_internal(raw, language: str) -> bool:
    """True when a specifier that failed to resolve still looks in-repo.

    Used only to report an honest resolution rate — external packages should
    not count against it, but a relative import that failed to match a real
    file should.
    """
    if language == "python":
        return raw.relative_level > 0
    return raw.specifier.startswith("./") or raw.specifier.startswith("../")


def _detect_entry_points(
    files: list[SourceFile], graph: nx.DiGraph, weights: Weights
) -> set[Path]:
    entries: set[Path] = set()
    for item in files:
        if item.is_test:
            continue
        score = 0
        if item.path.name in _CONVENTIONAL_ENTRY_NAMES:
            score += weights.entry_conventional_name

        in_degree = graph.in_degree(item.path) if item.path in graph else 0
        out_degree = graph.out_degree(item.path) if item.path in graph else 0
        if in_degree == 0 and out_degree >= 3:
            score += weights.entry_zero_in_high_out

        if score >= weights.entry_threshold:
            entries.add(item.path)

    if not entries:
        # Fall back to whatever has the highest out-degree with no in-degree,
        # so a tour can still be built even when naming conventions weren't
        # followed. Test files are never eligible, even here — a library
        # with no conventional entry point (e.g. an importable package with
        # no CLI) still shouldn't hand onboarding to its test suite.
        non_test_paths = {item.path for item in files if not item.is_test}
        candidates = [
            (path, graph.out_degree(path))
            for path in graph.nodes
            if graph.in_degree(path) == 0 and path in non_test_paths
        ]
        if candidates:
            candidates.sort(key=lambda pair: pair[1], reverse=True)
            entries.add(candidates[0][0])

    return entries


def _score(repo_graph: RepoGraph, weights: Weights, churn: dict[str, int]) -> None:
    graph = repo_graph.graph
    nodes = list(graph.nodes)
    if not nodes:
        return

    # Reversed PageRank: importance flows from being imported, not from
    # importing — so PageRank runs on the graph with edges flipped.
    reversed_graph = graph.reverse(copy=True)
    try:
        pagerank = nx.pagerank(reversed_graph)
    except nx.PowerIterationFailedConvergence:
        pagerank = dict.fromkeys(nodes, 0.0)

    max_fan_in = max((graph.in_degree(n) for n in nodes), default=0) or 1
    max_churn = max(churn.values(), default=0) or 1

    distances = _distances_from_entries(graph, repo_graph.entry_points)
    max_distance = max((d for d in distances.values() if d != float("inf")), default=0)

    test_refs = _test_reference_counts(graph)
    max_test_refs = max(test_refs.values(), default=0) or 1

    for node in nodes:
        fan_in = graph.in_degree(node)
        distance = distances.get(node, float("inf"))
        proximity = 1.0 / (1.0 + distance) if distance != float("inf") else 0.0

        components = {
            "pagerank_reversed": weights.pagerank_reversed * pagerank.get(node, 0.0),
            "fan_in_normalized": weights.fan_in_normalized * (fan_in / max_fan_in),
            "entry_proximity": weights.entry_proximity * proximity,
            "churn_normalized": weights.churn_normalized
            * (churn.get(node.as_posix(), 0) / max_churn),
            "test_coverage": weights.test_coverage * (test_refs.get(node, 0) / max_test_refs),
            "doc_signal": 0.0,  # wired up once README parsing lands
        }
        repo_graph.score_breakdown[node] = components
        repo_graph.scores[node] = sum(components.values())


def _distances_from_entries(graph: nx.DiGraph, entry_points: set[Path]) -> dict[Path, float]:
    if not entry_points:
        return {}
    distances: dict[Path, float] = {}
    for entry in entry_points:
        if entry not in graph:
            continue
        lengths = nx.single_source_shortest_path_length(graph, entry)
        for node, dist in lengths.items():
            if dist < distances.get(node, float("inf")):
                distances[node] = dist
    return distances


def _test_reference_counts(graph: nx.DiGraph) -> dict[Path, int]:
    counts: dict[Path, int] = {}
    for node, data in graph.nodes(data=True):
        if not data.get("is_test"):
            continue
        for target in graph.successors(node):
            counts[target] = counts.get(target, 0) + 1
    return counts
