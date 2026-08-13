# RepoOnboard

**Generates CodeTour learning paths for any repository — ordered by the real dependency graph, not by a language model's opinion — and tells you when the tour has gone stale.**

[![CI](https://github.com/haeithm-muter/repoonboard/actions/workflows/ci.yml/badge.svg)](https://github.com/haeithm-muter/repoonboard/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

```bash
pip install repoonboard
repoonboard analyze ./some-repo
```

> Not yet on PyPI. The package builds, passes `twine check`, and has been
> installed from its own wheel into a clean environment and exercised — but it
> has never been uploaded. See [RELEASING.md](RELEASING.md). Until then:
> `pip install git+https://github.com/haeithm-muter/repoonboard`.

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
   `repoonboard check` classifies each station after the code moves — *fresh*,
   *lines shifted*, *answers changed*, *moved*, *deleted* — and handles the
   case no documentation tool does: a file has appeared that now outranks a
   station, so the tour is not wrong, it is incomplete. It exits non-zero when
   anything needs attention, so it can gate CI.

   What it does not do is judge meaning. `check` compares line ranges. Code
   appended below every cited line can still make an explanation wrong, and
   nothing here will notice.

## Commands

| Command | What it does | Status |
|---------|--------------|--------|
| `analyze` | Inventory the repository: files kept, files filtered, churn per file | ✅ working |
| `plan` | Select and order stations — no model call at all | ✅ working |
| `generate` | Write grounded explanations, questions, and all four output files | ✅ working |
| `check` | Report which stations went stale since the tour was pinned | ✅ working |

`generate` writes four files:

| File | For |
|------|-----|
| `.tours/onboarding.tour` | CodeTour in VS Code — opens each station at the right line, and warns when the pinned commit has moved |
| `ONBOARDING.md` | Everyone who doesn't use VS Code |
| `architecture.mmd` | A Mermaid graph of the stations and the import edges that actually exist between them |
| `.repoonboard/stations.json` | The machine-readable tour, pinned to the commit |

`ONBOARDING.md` and `architecture.mmd` land in the repository root, so they
carry a generated-file marker: a file without that marker is never replaced
unless you pass `--force`.

`generate --dry-run` runs the entire pipeline with no model and no network,
producing structural explanations only. It is the fastest way to see what the
tour's shape looks like on your repository before spending a single token.

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
must appear in the snippet, each question must carry a valid answer location
that lies inside the lines the model was shown and is not an import line.
A failure gets one retry with the rejections quoted back, then falls back to a
structural explanation with no model involved — and that fallback passes the
same gate. Every station records which of the three it came from.

**What the gate does not do:** it checks structure, not truth. Prose that
cites only real symbols and points at real lines can still be wrong about what
the code *does*, and it will pass. The gate bounds hallucination to the
vocabulary of the file; it is not a fact checker, and this README will not
pretend otherwise.

## Results

Measured on four pinned repositories — scrapy, poetry (Python), kysely, hono
(TypeScript) — with `eval/fetch.py` and `eval/run.py`. The pins, the ground
truth and the raw results are committed in `eval/`, so every number below can
be recomputed rather than taken on trust.

| Variant | Precision@6 (union) | Precision@6 (independent) |
|---------|---------------------|---------------------------|
| Full scoring | **0.375** | **0.222** |
| PageRank only | 0.250 | 0.167 |
| Direct model ordering | not tested | not tested |

Per repository, full scoring: scrapy 3/6, poetry 3/6, kysely 2/6, hono 1/6.

> **Two results that reflect badly on this tool, stated before anything else.**
>
> **The independent score went down, from 0.250 to 0.222.** Adding a third
> ground-truth source did not improve the tool; it widened coverage from two
> repositories to three, and the repository that joined scored badly. The
> earlier, higher number was measured against less evidence.
>
> **Full scoring loses to the PageRank-only ablation on poetry:** 0/6 against
> 1/6 on independent ground truth. It wins on scrapy (3/6 against 2/6) and
> kysely (1/6 against 0/6), so it wins on average — two of three, not three of
> three. The central claim of this project is that computed selection beats
> the alternatives. On one of three measurable repositories, the simplest
> alternative beat it.
>
> **`Direct model ordering` is untested.** It needs a live model call, and no
> Anthropic API key was available in the environment this was built in — so
> the row says "not tested" rather than carrying a number. The baseline the
> README claims to improve on has never been measured.

**Read these numbers with the following in mind. They are weaker than the
table makes them look.**

*The ground truth is thinner than the method promised.* The original design
took files named in `CONTRIBUTING.md` as one of three sources. Measured, it
contributed **nothing on all four repositories** — contributing guides
describe process, not architecture; scrapy's is six lines pointing at a
website. It was replaced by references found anywhere in a repository's prose,
resolving both `path/to/file.ts` and Python's dotted `package.module` form.
That reaches three of four repositories. Nothing reaches all four: hono keeps
its documentation in a separate repository, and no candidate tried —
`CODEOWNERS`, an `ARCHITECTURE.md`, the README alone — exists in even one of
the four.

*One source had to be discarded for being too broad.* scrapy documents nearly
every module it has, so extracting references yielded 50.8% of the repository.
Each entry was correct and the set was still useless: six files picked at
random would score about 0.49 against it. Any source naming more than a
quarter of a repository is now recorded and excluded, because a ground truth
that names half the code cannot tell a good selection from a lucky one.

*The union column is partly circular.* Churn is an input to the scorer under
test, at 0.15 of the weight, and after the above it is the only source that
fires on every repository. The **independent** column scores only against
sources the scorer never sees; it covers three of the four repositories and is
the number to believe. It is lower.

*What can be said:* computed selection beats the PageRank-only ablation on
both columns, on two of the three repositories that can be measured
independently. That supports the narrow claim that layer diversity and the
folder cap earn their place. It does not establish the broader claim that
computed ordering beats a model's opinion, because that comparison has never
been run.

*Sample size is four.* The one discovery fix made after seeing these results —
excluding `example/`, `benchmark/`, `site/` and similar directories — was
adopted because that code is not the project, not because it moved the number.
It moved full scoring from 0.333 to 0.375.

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
