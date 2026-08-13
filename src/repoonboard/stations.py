"""Layer classification and station selection.

Picking the highest-scoring files directly is wrong: it returns six files
from the same layer. This module enforces layer diversity, then orders the
result by execution flow rather than by importance score — which is the
actual difference between "a list of important files" and "a reading path".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from .graph import RepoGraph

LAYER_ORDER: tuple[str, ...] = ("entry", "routing", "core", "data", "utils")

_ROUTING_MARKERS = frozenset(
    {"routes", "route", "api", "controllers", "controller", "handlers", "handler", "views", "endpoints"}
)
_DATA_MARKERS = frozenset(
    {"models", "model", "db", "database", "repository", "repositories", "schema", "storage", "dao"}
)
_UTILS_MARKERS = frozenset(
    {"utils", "util", "helpers", "helper", "lib", "libs", "common", "shared", "misc"}
)


def classify_layer(path: Path, is_entry: bool) -> str:
    """Assign one of the five fixed layers to a file.

    Entry status overrides folder naming — a file already known to be an
    entry point is the "entry" layer regardless of what directory it sits in.
    """
    if is_entry:
        return "entry"

    parts = {part.lower() for part in path.parts[:-1]}
    if parts & _ROUTING_MARKERS:
        return "routing"
    if parts & _DATA_MARKERS:
        return "data"
    if parts & _UTILS_MARKERS:
        return "utils"
    return "core"


@dataclass(frozen=True)
class Station:
    path: Path
    layer: str
    score: float


def select_stations(
    repo_graph: RepoGraph, min_count: int = 5, max_count: int = 8
) -> list[Station]:
    """Choose stations with layer diversity and a folder-similarity cap.

    Selection order: highest score first within each layer, guaranteeing at
    least one station per layer that actually has files, then filling the
    remainder by score across all layers. Two stations from the same folder
    are only both kept if they belong to different layers.
    """
    candidates = [
        Station(item.path, classify_layer(item.path, item.path in repo_graph.entry_points), repo_graph.scores.get(item.path, 0.0))
        for item in repo_graph.files
        if not item.language or True
    ]
    candidates = [
        c
        for c in candidates
        if not _is_test_path(c.path, repo_graph)
    ]
    candidates.sort(key=lambda c: c.score, reverse=True)

    by_layer: dict[str, list[Station]] = {layer: [] for layer in LAYER_ORDER}
    for candidate in candidates:
        by_layer[candidate.layer].append(candidate)

    selected: list[Station] = []
    used_folders: dict[Path, set[str]] = {}

    def _try_add(station: Station) -> bool:
        folder = station.path.parent
        if station.layer in used_folders.get(folder, set()):
            return False  # same folder, same layer already represented
        selected.append(station)
        used_folders.setdefault(folder, set()).add(station.layer)
        return True

    # Pass 1: guarantee at least one station per non-empty layer.
    for layer in LAYER_ORDER:
        for candidate in by_layer[layer]:
            if _try_add(candidate):
                break

    # Pass 2: fill up to max_count by score, across all layers.
    remaining = sorted(
        (c for c in candidates if c not in selected), key=lambda c: c.score, reverse=True
    )
    for candidate in remaining:
        if len(selected) >= max_count:
            break
        _try_add(candidate)

    # If layer diversity alone left us short of the minimum, top up ignoring
    # the folder cap rather than shipping an under-sized tour.
    if len(selected) < min_count:
        for candidate in remaining:
            if candidate in selected:
                continue
            selected.append(candidate)
            if len(selected) >= min_count:
                break

    return selected[:max_count]


def _is_test_path(path: Path, repo_graph: RepoGraph) -> bool:
    data = repo_graph.graph.nodes.get(path, {})
    return bool(data.get("is_test"))


@dataclass
class OrderingResult:
    stations: list[Path]
    broken_edges: list[tuple[Path, Path]]


def order_stations(repo_graph: RepoGraph, stations: list[Station]) -> OrderingResult:
    """Sequence stations to follow execution flow, not raw importance.

    Walks the graph depth-first starting from entry points, ranked by score
    so the strongest entry is explored first. A back edge encountered during
    the walk is exactly a cycle being broken — it is recorded rather than
    followed again.
    """
    station_paths = {s.path for s in stations}
    graph = repo_graph.graph

    ordered_entries = sorted(
        (e for e in repo_graph.entry_points if e in graph),
        key=lambda p: repo_graph.scores.get(p, 0.0),
        reverse=True,
    )
    if not ordered_entries:
        # No detected entry point — fall back to the highest-scoring station.
        ordered_entries = [max(stations, key=lambda s: s.score).path] if stations else []

    visited: set[Path] = set()
    visit_order: list[Path] = []
    broken_edges: list[tuple[Path, Path]] = []
    in_stack: set[Path] = set()

    def dfs(node: Path) -> None:
        if node in visited:
            return
        visited.add(node)
        in_stack.add(node)
        visit_order.append(node)

        neighbours = sorted(
            graph.successors(node), key=lambda n: repo_graph.scores.get(n, 0.0), reverse=True
        )
        for neighbour in neighbours:
            if neighbour in in_stack:
                broken_edges.append((node, neighbour))  # cycle — break it here
                continue
            dfs(neighbour)

        in_stack.discard(node)

    for entry in ordered_entries:
        dfs(entry)

    # Anything reachable only from other stations we haven't started from yet.
    for station in stations:
        if station.path not in visited:
            dfs(station.path)

    filtered = [node for node in visit_order if node in station_paths]

    # Guard against a station DFS never reached (disconnected from every
    # entry point and every other station) — append by score so nothing
    # silently disappears from the tour.
    missing = [s.path for s in stations if s.path not in filtered]
    missing.sort(key=lambda p: repo_graph.scores.get(p, 0.0), reverse=True)
    filtered.extend(missing)

    return OrderingResult(stations=filtered, broken_edges=broken_edges)
