"""Score the pinned repositories and print the results table.

    python eval/fetch.py     # once, to build eval/ground_truth.json
    python eval/run.py       # as often as you like

Two variants are computed here:

- **Full scoring** — what `plan` actually does: weighted importance, layer
  diversity, folder cap, topological ordering.
- **PageRank only** — the ablation. Rank every non-test file by reversed
  PageRank and take the top six, with none of the diversity machinery. If the
  full pipeline cannot beat this, the diversity machinery is not earning its
  place and the thesis of the project is in trouble.

A third variant, **direct model ordering**, is the baseline the README claims
to improve on. It is deliberately *not* computed here: it requires a live
model call, and this project has never run one. Reporting a number for it
would mean inventing the very comparison the tool exists to avoid, so it is
reported as not run, with the reason.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from repoonboard.evaluation import Score, mean_precision, precision_at_k
from repoonboard.graph import build
from repoonboard.stations import order_stations, select_stations

GROUND_TRUTH = ROOT / "eval" / "ground_truth.json"
RESULTS = ROOT / "eval" / "results.json"

FULL = "Full scoring"
PAGERANK = "PageRank only"
MODEL = "Direct model ordering"


def full_scoring(repo_graph) -> list[str]:
    stations = select_stations(repo_graph)
    ordered = order_stations(repo_graph, stations)
    return [path.as_posix() for path in ordered.stations]


def pagerank_only(repo_graph) -> list[str]:
    """Rank by reversed PageRank alone — no layers, no folder cap, no ordering."""
    ranked = []
    for path, components in repo_graph.score_breakdown.items():
        if repo_graph.graph.nodes.get(path, {}).get("is_test"):
            continue
        ranked.append((components.get("pagerank_reversed", 0.0), path.as_posix()))
    # Sort by score, then path, so ties do not depend on dict ordering.
    ranked.sort(key=lambda pair: (-pair[0], pair[1]))
    return [path for _, path in ranked]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", default=str(ROOT / ".eval-cache"))
    parser.add_argument("--k", type=int, default=6)
    args = parser.parse_args()

    if not GROUND_TRUTH.is_file():
        print(f"No ground truth at {GROUND_TRUTH}. Run eval/fetch.py first.")
        return 1

    entries = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    work = Path(args.work)

    scores: dict[str, list[Score]] = {FULL: [], PAGERANK: []}
    independent_scores: dict[str, list[Score]] = {FULL: [], PAGERANK: []}
    per_repo = []

    for entry in entries:
        name = entry["name"]
        target = work / name
        if not target.is_dir():
            print(f"{name}: not cloned at {target}; run eval/fetch.py")
            return 1

        truth = set(entry["ground_truth"])

        # Churn is an input to the very scorer under test (0.15 of the weight),
        # so a ground truth built from churn partly measures the tool against
        # its own input. The independent subset — everything the union got from
        # a source the scorer never sees — is the honest comparison, and is
        # reported alongside rather than instead of the union.
        by_source = entry.get("by_source", {})
        independent = set()
        for source, paths in by_source.items():
            if source != "churn":
                independent |= set(paths)

        print(f"\n{name} ({entry['language']}, {entry['source_files']} source files)")
        print(f"  ground truth: {len(truth)} files from {', '.join(entry['contributing_sources'])}")
        print(f"  independent of churn: {len(independent)} files")

        repo_graph = build(target)

        row = {
            "name": name,
            "ground_truth_size": len(truth),
            "independent_size": len(independent),
            "variants": {},
        }
        for variant, predict in ((FULL, full_scoring), (PAGERANK, pagerank_only)):
            predicted = predict(repo_graph)
            score = precision_at_k(predicted, truth, k=args.k, variant=variant, repository=name)
            scores[variant].append(score)

            entry_row = {
                "hits": score.hits,
                "considered": score.considered,
                "precision": round(score.precision, 4),
                "predicted": predicted[: args.k],
            }

            if independent:
                independent_score = precision_at_k(
                    predicted, independent, k=args.k, variant=variant, repository=name
                )
                independent_scores[variant].append(independent_score)
                entry_row["independent_hits"] = independent_score.hits
                entry_row["independent_precision"] = round(independent_score.precision, 4)

            row["variants"][variant] = entry_row
            suffix = (
                f"   (independent {entry_row['independent_hits']}/{score.considered})"
                if independent
                else "   (no independent ground truth)"
            )
            print(
                f"  {variant:16} {score.hits}/{score.considered} = {score.precision:.2f}{suffix}"
            )
            print(f"      {', '.join(predicted[: args.k])}")
        per_repo.append(row)

    summary = {}
    independent_summary = {}
    scored_independently = len(independent_scores[FULL])

    print(f"\n{'Variant':24} {'union':>8} {'independent':>13}")
    for variant in (FULL, PAGERANK):
        mean = mean_precision(scores[variant])
        summary[variant] = round(mean, 4)
        if independent_scores[variant]:
            independent_mean = mean_precision(independent_scores[variant])
            independent_summary[variant] = round(independent_mean, 4)
            print(f"{variant:24} {mean:8.3f} {independent_mean:13.3f}")
        else:
            print(f"{variant:24} {mean:8.3f} {'n/a':>13}")
    print(f"{MODEL:24} {'not run':>8} {'not run':>13}")

    print(
        f"\nunion       = mean precision@{args.k} over all {len(entries)} repositories\n"
        f"independent = same, over the {scored_independently} repositories that have "
        "ground-truth entries from a source the scorer does not itself consume"
    )

    RESULTS.write_text(
        json.dumps(
            {
                "k": args.k,
                "repositories": per_repo,
                "mean_precision_union": summary,
                "mean_precision_independent": independent_summary,
                "independent_repository_count": scored_independently,
                "not_run": {
                    MODEL: "requires a live model call; no verified live path exists"
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
