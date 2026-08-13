# RepoOnboard — Project Rules

This file is gitignored on purpose. It is local configuration, not published
content.

## Architectural law (non-negotiable)
Station selection and ordering are computed from the dependency graph and Git
signals only (networkx: reversed PageRank, fan-in, topological sort).
The language model EXPLAINS. It never SELECTS and never ORDERS.
Any proposal that lets the model choose or rank files is rejected outright.

## The four constraints
1. Ordering derived from code structure, never from model opinion.
2. Output is executable (.tour for CodeTour), not another readable page.
3. Verification, not summary: 2-3 questions per station, each carrying an
   answer_location (file + line range).
4. The tour knows when it is stale: pinned to a commit hash; `check`
   classifies every affected station.

## Out of scope for v1 — do not implement; write to BACKLOG.md instead
Q&A interface, hosted site, database, web UI, monorepos, function-level
analysis, private repos and auth, any language other than Python and TS/JS.

## Hard limits
- Repos > 1500 files: warn and suggest --subdir. Do not attempt the full run.
- Import path resolution: accept ~85% accuracy, print the rate to the user,
  and move on. Never spend more than one session on it.
- Never target a station at an import line.
- No unverified content is ever emitted.

## Engineering rules
- Python 3.11+, pyproject.toml, typer + rich, pydantic, networkx,
  tree-sitter + tree-sitter-languages, pytest.
- No server, no database, no web UI.
- No live model calls in tests. Use recorded fixtures.
- All code, comments, docstrings, CLI output, README and docs in English.
- Commit messages: plain, factual, English. No AI attribution, no
  "Generated with" lines, no Co-Authored-By trailers.
- Do not advance while any test is red.

## Current state
Milestone 1 partially done: discovery, filtering, churn, CLI shell, 28 tests
green. Next: tree-sitter import extraction and path resolution.
