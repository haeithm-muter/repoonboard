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
Milestones 1, 2 and 3 done: discovery, filtering, churn, import extraction and
resolution, dependency graph, scoring, layer classification, station selection
and ordering, snippet extraction, grounded explanation and verification
questions behind the grounding gate. 144 tests green. `analyze`, `plan` and
`generate` all run end to end on real repositories.

The generation ladder is: model, then one retry with the gate's rejections
quoted back, then a structural explanation built with no model at all. The
structural output passes the same gate as everything else, and a test enforces
that. Every station records its provenance, and the CLI prints it.

Note the gate's real limit before extending it: it verifies paths, symbols,
question count and answer locations — all structural. It does not verify that
a sentence is true. See NOTES.md.

Next: milestone 4 — export to `.tour`, `ONBOARDING.md` and `architecture.mmd`.
Known gaps deliberately left open during milestone 2 are listed in NOTES.md;
read them before extending the scoring model, and do not silently rely on
`doc_signal`. `ruff check` is not clean on the milestone 1-2 modules by
design; it is clean on everything added in milestone 3.
