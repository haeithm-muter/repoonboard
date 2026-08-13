"""Command line interface.

Commands map onto the milestones. Anything not yet built exits with a clear
message rather than pretending to work.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .discovery import MAX_FILES, discover, dominant_languages
from .explanation import Provenance
from .generation import UnverifiableStation, generate_station
from .git_signals import churn, head_commit, is_git_repository
from .graph import build
from .model import DEFAULT_MODEL, AnthropicGenerator, CachingGenerator
from .snippets import build_for_file
from .stations import order_stations, select_stations

OUTPUT_DIR = ".repoonboard"

app = typer.Typer(
    name="repoonboard",
    help="Generate a CodeTour learning path ordered by a repository's real structure.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _load(root: Path, subdir: str | None):
    if not root.exists():
        console.print(f"[red]Path does not exist:[/red] {root}")
        raise typer.Exit(code=1)

    files = discover(root, subdir)
    if not files:
        console.print("[red]No Python or TypeScript/JavaScript source files found.[/red]")
        raise typer.Exit(code=1)

    if len(files) > MAX_FILES and subdir is None:
        console.print(
            f"[yellow]This repository has {len(files)} source files "
            f"(limit {MAX_FILES}).[/yellow]\n"
            "Re-run against a single package with --subdir to keep the tour meaningful."
        )
        raise typer.Exit(code=2)

    return files


@app.command()
def analyze(
    path: Path = typer.Argument(..., help="Path to a local repository."),
    subdir: str = typer.Option(None, "--subdir", help="Restrict analysis to one subdirectory."),
    limit: int = typer.Option(25, "--limit", help="Rows to display."),
    months: int = typer.Option(12, "--months", help="Churn window in months."),
) -> None:
    """Inventory the repository: files kept, files filtered, churn per file."""
    files = _load(path, subdir)

    commits: dict[str, int] = {}
    pinned = "not a git repository"
    if is_git_repository(path):
        commits = churn(path, months)
        pinned = head_commit(path)[:12]

    sources = [item for item in files if not item.is_test]
    tests = [item for item in files if item.is_test]

    table = Table(title=f"{path.name} @ {pinned}", header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("File")
    table.add_column("Lang")
    table.add_column("Lines", justify="right")
    table.add_column(f"Commits/{months}mo", justify="right")

    ranked = sorted(sources, key=lambda item: commits.get(item.posix, 0), reverse=True)
    for index, item in enumerate(ranked[:limit], start=1):
        table.add_row(
            str(index),
            item.posix,
            item.language,
            str(item.line_count),
            str(commits.get(item.posix, 0)),
        )

    console.print(table)
    console.print(
        f"[bold]{len(sources)}[/bold] source files kept, "
        f"[bold]{len(tests)}[/bold] test files held in the graph but excluded as stations."
    )
    console.print(f"Languages: {dominant_languages(files)}")
    if not commits:
        console.print("[yellow]No git history available — churn signal is unavailable.[/yellow]")


@app.command()
def plan(
    path: Path = typer.Argument(..., help="Path to a local repository."),
    subdir: str = typer.Option(None, "--subdir", help="Restrict analysis to one subdirectory."),
    explain: bool = typer.Option(False, "--explain", help="Show each score component."),
) -> None:
    """Select and order the stations. No model call is made here, by design."""
    files = _load(path, subdir)  # validates existence and the file-count limit
    repo_graph = build(path, subdir)

    if repo_graph.unresolved_edges + repo_graph.resolved_edges > 0:
        console.print(
            f"Import resolution: [bold]{repo_graph.resolution_rate:.0%}[/bold] "
            f"({repo_graph.resolved_edges} resolved, {repo_graph.unresolved_edges} unresolved)"
        )

    if not repo_graph.entry_points:
        console.print(
            "[yellow]No entry point detected — falling back to the highest out-degree "
            "file with no incoming imports.[/yellow]"
        )

    stations = select_stations(repo_graph)
    result = order_stations(repo_graph, stations)
    by_path = {s.path: s for s in stations}

    table = Table(title=f"{path.name} — learning path", header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("File")
    table.add_column("Layer")
    table.add_column("Score", justify="right")

    for index, station_path in enumerate(result.stations, start=1):
        station = by_path[station_path]
        table.add_row(str(index), station.path.as_posix(), station.layer, f"{station.score:.3f}")

    console.print(table)

    if result.broken_edges:
        console.print(
            f"[dim]{len(result.broken_edges)} cyclic edge(s) broken during ordering: "
            + ", ".join(f"{a.as_posix()}→{b.as_posix()}" for a, b in result.broken_edges[:5])
            + "[/dim]"
        )

    if explain:
        console.print("\n[bold]Score breakdown[/bold]")
        for station_path in result.stations:
            console.print(f"\n[bold]{station_path.as_posix()}[/bold]")
            for component, value in repo_graph.score_breakdown[station_path].items():
                console.print(f"  {component:<20} {value:.4f}")


@app.command()
def generate(
    path: Path = typer.Argument(..., help="Path to a local repository."),
    subdir: str = typer.Option(None, "--subdir", help="Restrict analysis to one subdirectory."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Skip the model entirely and use structural explanations only.",
    ),
    model: str = typer.Option(DEFAULT_MODEL, "--model", help="Model used for explanations."),
) -> None:
    """Write grounded explanations and verification questions for each station.

    Selection and ordering are already fixed by `plan`; this command only adds
    prose and questions, and every piece of both must pass the grounding gate
    before it is written out.
    """
    files = _load(path, subdir)
    repo_graph = build(path, subdir)

    stations = select_stations(repo_graph)
    ordering = order_stations(repo_graph, stations)
    by_path = {station.path: station for station in stations}
    known_paths = frozenset(item.posix for item in files)
    languages = {item.path: item.language for item in files}

    pinned = head_commit(path) if is_git_repository(path) else None
    if pinned is None:
        console.print(
            "[yellow]Not a git repository — the tour cannot be pinned to a commit, "
            "so `check` will not be able to tell when it goes stale.[/yellow]"
        )

    generator = None
    if not dry_run:
        generator = CachingGenerator(
            AnthropicGenerator(model=model), Path(path) / OUTPUT_DIR / "cache"
        )

    results = []
    skipped: list[tuple[str, str]] = []

    for station_path in ordering.stations:
        station = by_path[station_path]
        snippet = build_for_file(Path(path), station_path, languages[station_path])
        fan_in = repo_graph.graph.in_degree(station_path)

        try:
            result = generate_station(
                snippet=snippet,
                layer=station.layer,
                signals=repo_graph.score_breakdown.get(station_path, {}),
                extra={"imported by": f"{fan_in} file(s) in this repository"},
                known_paths=known_paths,
                generator=generator,
            )
        except UnverifiableStation as exc:
            skipped.append((station_path.as_posix(), str(exc)))
            continue

        results.append(result)

    _report(path, results, skipped)
    _write(Path(path), pinned, results)


def _report(path: Path, results: list, skipped: list[tuple[str, str]]) -> None:
    table = Table(title=f"{path.resolve().name} — generated stations", header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("File")
    table.add_column("Source")
    table.add_column("Q", justify="right")

    colours = {
        Provenance.MODEL: "green",
        Provenance.MODEL_RETRY: "yellow",
        Provenance.STRUCTURAL: "red",
    }
    for index, result in enumerate(results, start=1):
        provenance = result.provenance
        table.add_row(
            str(index),
            result.explanation.path,
            f"[{colours[provenance]}]{provenance.value}[/{colours[provenance]}]",
            str(len(result.explanation.questions)),
        )
    console.print(table)

    # attempts == 0 means the model was never asked (--dry-run), which is not
    # a fallback and must not be reported as one.
    fell_back = [
        r for r in results if r.provenance is Provenance.STRUCTURAL and r.attempts > 0
    ]
    if fell_back:
        console.print(
            f"[yellow]{len(fell_back)} station(s) fell back to structural text because "
            "the model's output failed the grounding gate twice:[/yellow]"
        )
        for result in fell_back:
            reasons = ", ".join(sorted({r.code for r in result.rejections})) or "model unavailable"
            console.print(f"  [dim]{result.explanation.path}: {reasons}[/dim]")
    elif results and all(r.attempts == 0 for r in results):
        console.print(
            "[dim]--dry-run: no model was called. Every station carries structural "
            "text describing what the code contains, not what it is for.[/dim]"
        )

    for station_path, reason in skipped:
        console.print(f"[red]Skipped {station_path}[/red]: {reason}")


def _write(root: Path, pinned: str | None, results: list) -> None:
    import json

    directory = root / OUTPUT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "stations.json"

    payload = {
        "commit": pinned,
        "stations": [
            result.explanation.model_dump(mode="json") for result in results
        ],
    }
    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    console.print(f"Wrote [bold]{destination}[/bold]")


@app.command()
def check(path: Path = typer.Argument(..., help="Path to a repository holding a tour.")) -> None:
    """Report which stations went stale since the tour was pinned."""
    console.print("[yellow]Not implemented yet — milestone 5.[/yellow]")
    raise typer.Exit(code=3)


@app.command()
def version() -> None:
    """Print the installed version."""
    console.print(__version__)


if __name__ == "__main__":
    app()
