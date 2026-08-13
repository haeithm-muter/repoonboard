# RepoOnboard

**Generates CodeTour learning paths for any repository — ordered by the real dependency graph, not by a language model's opinion — and tells you when the tour has gone stale.**

[![CI](https://github.com/haeithm-muter/repoonboard/actions/workflows/ci.yml/badge.svg)](https://github.com/haeithm-muter/repoonboard/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

```bash
pip install repoonboard
repoonboard analyze ./some-repo
```

---

## The problem

You join a repository you don't know. Four hundred files. The README covers
installation. `src/` holds twenty directories that all look equally important.
You ask a model and get a fluent summary you have no way to verify. Two hours
in, you still don't know the only question that matters: **where do I start
reading, and in what order?**

## What this does differently

Existing tools ask a model *what is important here* and hand you back an
opinion — unverifiable, unrepeatable, and stale the moment the code moves.
RepoOnboard computes the answer from the code's own structure and uses the
model only to explain what was already chosen.

Four decisions hold the whole design up:

1. **Ordering is derived, not opined.** Stations are selected and sequenced
   from the dependency graph with reversed PageRank, fan-in, layer
   classification and a topological sort that follows execution flow. The
   model never selects and never orders. The output is deterministic: the same
   commit produces the same tour.
2. **The output is executable.** A `.tour` file opens inside VS Code at the
   correct line, not another web page to skim. An `ONBOARDING.md` is written
   alongside it for people who don't use VS Code.
3. **Verification, not summary.** Every station carries two or three questions,
   and every question carries the location of its answer in the code — file
   and line range. Passive reading becomes active recall.
4. **The tour knows when it is stale.** Tours are pinned to a commit hash.
   `repoonboard check` classifies each station after the code moves, including
   the case no documentation tool handles: a file has appeared that scores
   higher than an existing station, so the tour itself is now incomplete.

## Commands

| Command | What it does | Status |
|---------|--------------|--------|
| `analyze` | Inventory the repository: files kept, files filtered, churn per file | ✅ working |
| `plan` | Select and order stations — no model call at all | 🚧 milestone 2 |
| `generate` | Write grounded explanations and questions, export the tour | 🚧 milestones 3–4 |
| `check` | Report which stations went stale since the tour was pinned | 🚧 milestone 5 |

## How importance is computed

```
importance(f) = 0.35 · pagerank_reversed(f)   # how widely it is imported
              + 0.20 · fan_in_normalized(f)   # how many files import it
              + 0.15 · entry_proximity(f)     # distance to nearest entry point
              + 0.15 · churn_normalized(f)    # commits in the last 12 months
              + 0.10 · test_coverage(f)       # test files referencing it
              + 0.05 · doc_signal(f)          # named in README, or documented
```

Weights live in `weights.toml`. `--explain` shows each component's
contribution to a file's score, so any ranking can be argued with.

Selecting the top scorers directly would be wrong — it returns six files from
the same layer. Stations are constrained to cover `entry → routing →
core/domain → data → utils`, capped for folder similarity, and then ordered
topologically along execution flow. That constraint is the difference between
a list of important files and a learning path.

## Where the model is allowed to act

**Permitted:** explaining a file that was already selected, phrasing why it
matters from the graph signals it was given, writing verification questions
from the visible snippet, naming domain terms.

**Forbidden:** selecting stations, ordering them, asserting behaviour not
visible in the snippet, mentioning files or symbols absent from its context.

Every generation passes a grounding gate: each path must exist, each symbol
must appear in the snippet, each question must carry a valid answer location.
A failure gets one retry with tighter instructions, then falls back to a
structural explanation with no model involved. Unverified content is never
emitted.

## Results

Measured on four pinned open-source repositories (two Python, two
TypeScript), against ground truth built from three independent sources:
files named in `CONTRIBUTING.md`, the ten most-committed files, and files
referenced from `good first issue` threads.

<!-- Numbers go here once the evaluation harness runs. Report what it
     measures, including the case where computed ordering loses to the
     model's. An honest number is worth more than a flattering one. -->

| Variant | Precision@6 |
|---------|-------------|
| Full scoring | _pending_ |
| PageRank only | _pending_ |
| Direct model ordering | _pending_ |

## Scope

**In v1:** local and public GitHub repositories, tree-sitter dependency graph,
entry point detection, computed ordering, grounded explanation, verification
questions, `.tour` + `ONBOARDING.md` + `architecture.mmd` export, staleness
detection, evaluation harness.

**Not in v1:** a question-and-answer interface, a hosted site, languages
beyond Python and TypeScript/JavaScript, monorepos, function-level analysis,
private repositories.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

Tests make no network calls and no model calls; generation is tested against
recorded fixtures.

## License

MIT
