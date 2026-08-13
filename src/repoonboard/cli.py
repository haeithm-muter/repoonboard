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
from .export import (
    Tour,
    TourStation,
    is_generated,
    to_codetour,
    to_json_payload,
    to_markdown,
    to_mermaid,
)
from .generation import UnverifiableStation, generate_station
from .git_signals import (
    changed_line_ranges,
    changed_paths,
    churn,
    commit_exists,
    head_commit,
    is_git_repository,
)
from .graph import build
from .model import DEFAULT_MODEL, AnthropicGenerator, CachingGenerator
from .snippets import build_for_file
from .staleness import State, build_report, cited_ranges
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
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite ONBOARDING.md or architecture.mmd even if this tool did not write them.",
    ),
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
    tour_stations: list[TourStation] = []
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
        assert snippet.anchor_line is not None  # guaranteed by generate_station
        tour_stations.append(
            TourStation(
                path=station_path.as_posix(),
                layer=station.layer,
                anchor_line=snippet.anchor_line,
                explanation=result.explanation,
            )
        )

    selected = {station.path for station in tour_stations}
    tour = Tour(
        repository=Path(path).resolve().name,
        commit=pinned,
        stations=tuple(tour_stations),
        edges=tuple(
            (source.as_posix(), target.as_posix())
            for source, target in repo_graph.graph.edges
            if source.as_posix() in selected and target.as_posix() in selected
        ),
        resolution_rate=repo_graph.resolution_rate,
    )

    _report(path, results, skipped)
    written = _write(Path(path), tour, force)
    for destination in written:
        console.print(f"Wrote [bold]{destination}[/bold]")


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


def _write(root: Path, tour: Tour, force: bool) -> list[Path]:
    """Write the four artefacts and return where they went.

    The `.tour` goes where CodeTour looks for it; the two readable files go to
    the repository root, because a reader who does not use VS Code should not
    have to know that `.repoonboard/` exists.

    Writing to the root means a hand-written `ONBOARDING.md` could already be
    there. Anything without this tool's marker is left alone and reported,
    rather than replaced.
    """
    internal = root / OUTPUT_DIR
    internal.mkdir(parents=True, exist_ok=True)
    tours = root / ".tours"
    tours.mkdir(parents=True, exist_ok=True)

    stations_json = internal / "stations.json"
    stations_json.write_text(to_json_payload(tour), encoding="utf-8")

    written = [stations_json]
    refused: list[Path] = []

    for destination, content in (
        (tours / "onboarding.tour", to_codetour(tour)),
        (root / "ONBOARDING.md", to_markdown(tour)),
        (root / "architecture.mmd", to_mermaid(tour)),
    ):
        if destination.exists() and not force:
            try:
                existing = destination.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                existing = ""
            if not is_generated(existing):
                refused.append(destination)
                continue

        destination.write_text(content, encoding="utf-8")
        written.append(destination)

    if refused:
        console.print(
            "\n[red]Refused to overwrite file(s) this tool did not write:[/red]"
        )
        for destination in refused:
            console.print(f"  {destination}")
        console.print(
            "[yellow]Move or rename them, or re-run with --force to replace "
            "them.[/yellow]"
        )

    return written


@app.command()
def check(
    path: Path = typer.Argument(..., help="Path to a repository holding a tour."),
    subdir: str = typer.Option(None, "--subdir", help="Restrict analysis to one subdirectory."),
) -> None:
    """Report which stations went stale since the tour was pinned.

    The comparison runs from the pinned commit to the *working tree*, not to
    HEAD, so uncommitted edits count as staleness. That is what you want when
    asking "is my tour still good right now"; in CI, where the tree is clean,
    it reduces to the pinned-commit-to-HEAD diff.

    Exits 0 when the tour is current and 1 when anything needs attention, so
    it can be run in CI.
    """
    import json

    root = Path(path)
    stations_file = root / OUTPUT_DIR / "stations.json"
    if not stations_file.is_file():
        console.print(
            f"[red]No tour found at {stations_file}.[/red]\n"
            "Run `repoonboard generate` first."
        )
        raise typer.Exit(code=1)

    try:
        payload = json.loads(stations_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]Could not read {stations_file}:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    pinned = payload.get("commit")
    if not pinned:
        console.print(
            "[red]This tour was not pinned to a commit[/red], so there is no "
            "baseline to compare against. It was generated from a directory "
            "without Git history."
        )
        raise typer.Exit(code=1)

    if not is_git_repository(root):
        console.print(f"[red]{root} is not a git repository.[/red]")
        raise typer.Exit(code=1)

    if not commit_exists(root, pinned):
        console.print(
            f"[red]The pinned commit {pinned[:12]} is not in this clone's "
            "history.[/red]\nIt may have been rebased away, force-pushed over, "
            "or this may be a shallow clone. Re-run `repoonboard generate`."
        )
        raise typer.Exit(code=1)

    stations = payload.get("stations", [])
    changes = changed_paths(root, pinned)
    ranges_for = {
        station["path"]: changed_line_ranges(root, pinned, station["path"])
        for station in stations
        if station.get("path") in changes and changes[station["path"]][0] == "M"
    }

    repo_graph = build(root, subdir)
    current = order_stations(repo_graph, select_stations(repo_graph)).stations

    report = build_report(
        pinned=pinned,
        head=head_commit(root),
        stations=[(s["path"], cited_ranges(s)) for s in stations],
        changes=changes,
        ranges_for=ranges_for,
        current_selection=_stationable(root, current, subdir),
    )

    _report_staleness(report)
    raise typer.Exit(code=0 if report.is_current else 1)


def _stationable(root: Path, selected: list[Path], subdir: str | None) -> list[str]:
    """Of the files selection would pick now, those that could be stations.

    `generate` refuses a file with no line to open at — an empty `__init__.py`,
    a pure re-export barrel. Comparing the tour against the raw selection would
    therefore report such a file as newly outranking the tour on every run,
    including the run immediately after `generate`. The comparison has to be
    like for like: what would be selected *and* could actually be explained.
    """
    languages = {item.path: item.language for item in discover(root, subdir)}

    usable: list[str] = []
    for path in selected:
        language = languages.get(path)
        if language is None:
            continue
        try:
            snippet = build_for_file(root, path, language)
        except OSError:
            continue
        if snippet.anchor_line is not None:
            usable.append(path.as_posix())
    return usable


_STATE_COLOUR = {
    State.FRESH: "green",
    State.LINES_SHIFTED: "yellow",
    State.ANSWERS_CHANGED: "red",
    State.MOVED: "yellow",
    State.DELETED: "red",
}


def _report_staleness(report) -> None:
    """Print the run's findings.

    Every sentence below is derived from the report. Nothing here asserts a
    count, an outcome, or a reassurance that was not computed from this run.
    """
    table = Table(
        title=f"Tour pinned at {report.pinned[:12]} · HEAD {report.head[:12]}",
        header_style="bold",
    )
    table.add_column("Station")
    table.add_column("State")
    table.add_column("Detail")

    for verdict in report.verdicts:
        colour = _STATE_COLOUR[verdict.state]
        table.add_row(
            verdict.path,
            f"[{colour}]{verdict.state.value}[/{colour}]",
            verdict.detail,
        )
    console.print(table)

    stale = report.stale
    total = len(report.verdicts)

    if report.pinned == report.head:
        # Comparison runs against the working tree, not against HEAD, so a
        # finding here can only have come from uncommitted edits. Saying which
        # it was beats leaving the reader to reconcile two true statements.
        if stale or report.now_outranking:
            console.print(
                "[dim]HEAD is still the pinned commit, so everything below comes "
                "from uncommitted changes in the working tree.[/dim]"
            )
        else:
            console.print("[dim]HEAD is the pinned commit.[/dim]")

    if stale:
        console.print(
            f"[yellow]{len(stale)} of {total} station(s) need attention.[/yellow]"
        )
    elif total:
        console.print(f"[green]All {total} station(s) still hold.[/green]")

    if report.now_outranking:
        console.print(
            f"\n[red]{len(report.now_outranking)} file(s) would now be selected "
            "over what is in the tour:[/red]"
        )
        for candidate in report.now_outranking:
            console.print(f"  {candidate}")
        console.print(
            "[dim]This is the case a text diff cannot see: the tour is not "
            "wrong, it is incomplete.[/dim]"
        )

    if report.no_longer_selected:
        console.print(
            f"\n[yellow]{len(report.no_longer_selected)} station(s) would no "
            "longer be selected:[/yellow]"
        )
        for candidate in report.no_longer_selected:
            console.print(f"  {candidate}")

    if report.is_current:
        console.print("\n[green]The tour is current.[/green]")
    else:
        console.print("\nRe-run [bold]repoonboard generate[/bold] to rebuild it.")


@app.command()
def version() -> None:
    """Print the installed version."""
    console.print(__version__)


if __name__ == "__main__":
    app()
