# Notes

After every milestone, answer both questions here before moving on.

## Milestone 1 (done)

**Weakest assumption so far:** that file-level import edges are enough to
recover a reading order. A repo built around dependency injection or a plugin
registry hides its real edges from static imports.

**What will break on a real repository:** TypeScript path aliases from
tsconfig, re-export barrels (`index.ts` that only re-exports), and Python
namespace packages. Resolution rate must be printed, not silently assumed.

## Milestone 2 (done)

81 tests green. `plan` runs end to end on a real repository.

**Weakest assumption so far:** that five fixed layers (entry, routing, core,
data, utils) inferred from *directory names* describe how repositories are
actually organised. On this repo every non-entry station classified as `core`,
because a flat `src/repoonboard/` package has no folder named `routes` or
`models`. The layer signal degrades to noise on single-package layouts, and
the folder-diversity cap degrades with it — see the finding below.

**What will break on a real repository:** the importance score is published as
six components but only five are live, so any explanation derived from it is
arguing from an incomplete model. Entry-point detection is weaker than the
configuration file implies. Both are recorded below rather than fixed, to keep
milestone 3 from turning into a second pass over milestone 2.

## Milestone 3 (done)

144 tests green. `generate` runs end to end; `--dry-run` exercises the whole
pipeline with no network and no model.

**Weakest assumption so far:** that a claim is grounded if the identifiers and
line numbers in it are real. The gate decides paths, symbols, question count,
and answer locations — all structural facts. It cannot decide whether a
sentence is *true*. Fluent prose that cites only real symbols and points at
real lines, and is wrong about what the code does, passes. The gate bounds
hallucination to the vocabulary of the file; it does not verify semantics, and
nothing in this milestone should be read as claiming otherwise.

The backtick convention is the load-bearing part of that bound: an identifier
the model writes without backticks is invisible to the gate. The prompt
requires backticks, but a model that ignores the instruction degrades the
check silently rather than loudly. Worth measuring in the evaluation harness.

**What will break on a real repository:** files larger than the snippet budget
are reduced to selected regions, so a question can only ever be anchored in a
region that was shown — correct, but it means large files get shallower
questions than small ones, and nothing currently reports that asymmetry. Pure
re-export barrels raise `UnverifiableStation` rather than producing a station,
which is honest but means station count can silently drop below the minimum
of five; selection does not yet know that a barrel is unusable.

## Milestone 4 (done)

185 tests green. `generate` writes `.tours/onboarding.tour`, `ONBOARDING.md`,
`architecture.mmd` and `.repoonboard/stations.json`, all from one `Tour` value.
The suite gained its first end-to-end CLI test, which builds a real git
repository in a temp directory and asserts on the files that come out.

**Weakest assumption so far:** that a station's anchor line — the line the tour
opens at — is well chosen by taking the first top-level definition. It is
*safe* (never an import, never elided, never blank, and tested on all three),
but "first definition in the file" is not the same as "the line that best
explains this file". A module whose interesting function is its fourth will
open on its least interesting one. Nothing measures this yet.

The `.tour` `ref` field is doing real work: CodeTour itself warns when the
working tree has moved past the pinned commit. That covers the simple staleness
case for free, and leaves milestone 5 the harder question the README actually
promises — a file has appeared that outranks an existing station.

**What will break on a real repository:** `architecture.mmd` draws only direct
import edges between selected stations. On a repository where stations sit two
or three hops apart, the diagram will be a set of disconnected boxes — honest,
but not useful. Drawing transitive edges would be a lie about the code, so the
fix is either to label the hop count or to say plainly that no direct edges
exist; currently it does the latter and nothing more.

Writing `ONBOARDING.md` into the repository root can collide with a file the
team already keeps there. Generated files carry a marker and anything without
it is refused rather than replaced, but that check is content-based: a hand-
written file that happens to quote the marker string would be overwritten.

## Milestone 5 (done)

226 tests green. `check` classifies every station against the pinned commit
and exits 1 when anything needs attention, so it can gate CI.

**Weakest assumption so far:** that "the file changed below every cited line"
means the station is still fine. It is true for line numbers and false for
meaning — appending a function that changes how an existing one behaves leaves
every cited range untouched while making the explanation wrong. `check` is a
line-range diff, not a semantic one, and it cannot see that. This is the same
boundary the grounding gate has: both verify structure, neither verifies truth.

**What will break on a real repository:** rename detection is git's, with the
default similarity threshold. A file that was renamed *and* heavily rewritten
in the same commit is reported as a delete plus a newcomer rather than a move,
which overstates the damage. Second, `check` compares against the working tree
rather than HEAD, so a dirty tree reports staleness that a clean CI checkout
would not — deliberate, documented in the command's docstring and in the
output, but it will surprise someone.

### Defect this milestone uncovered

**Selection picks files that cannot be stations.** `select_stations` chose two
empty `__init__.py` files in the end-to-end fixture. `generate` then refuses
them (no anchor line), so the tour silently came out with 4 stations against a
documented minimum of 5, and `check` initially reported those same files as
"newly outranking the tour" on a tour generated seconds earlier.

`check` now compares like with like — it filters the current selection through
the same "could this be a station?" rule — so the false positive is gone. The
underlying problem is not: selection does not know that an empty file cannot
carry a station, so tours can still come out undersized. The fix belongs in
`select_stations`, and per `weights.toml` any change there has to be
re-evaluated against `eval/repos.toml`, which is still empty. Left open
deliberately rather than patched blind.

## Evaluation harness (done)

246 tests green. `eval/fetch.py` builds the ground truth, `eval/run.py` scores
it, and both outputs are committed so the README's numbers can be recomputed.

**Weakest assumption so far:** that the three-source ground truth from the
original card describes what a newcomer should read. Measured, it does not.

- `CONTRIBUTING.md` yielded **zero** paths on all four repositories. These are
  process documents — how to run tests, how to file an issue, how to sign a
  CLA. scrapy's is six lines pointing at a website. The source is not weak
  here, it is structurally wrong: contributing guides do not name architecture.
- `good first issue` is absent from hono entirely and produced no file
  references for poetry. It worked for scrapy (40 paths) and barely for
  kysely (4).
- That leaves churn as the only source that fires everywhere — and churn is an
  input to the scorer under test, at 0.15 of the weight. For poetry and hono
  the ground truth is churn alone, so scoring against it is partly circular.

The `independent` column in the results reports precision against only the
non-churn part of the ground truth, which exists for two of four repositories.
It is lower than the union column (0.250 against 0.375) and it is the number
to believe.

**What will break on a real repository:** the harness assumes a path mentioned
in prose is a reference worth counting. It requires a directory separator and
a source extension precisely to avoid counting the word "engine.py" in a
sentence, but that same strictness silently drops real references written as
module paths (`scrapy.core.engine`) or as directories. Nothing measures what
it misses.

### What the numbers do and do not support

Full scoring beats the PageRank-only ablation on both columns, entirely on the
strength of the two TypeScript repositories. That supports the narrow claim
that layer diversity and the folder cap earn their keep. It does **not**
support the headline claim that computed ordering beats a model's opinion:
that comparison needs a live model call and has never been run. The results
table says "not run" rather than leaving the row blank, because a blank row
invites the reader to assume it was simply not interesting.

### Defect fixed by measurement

`discovery.py` did not exclude `example/`, `examples/`, `benchmark/`,
`benchmarks/`, `demo/`, `sample/`, `fixtures/`, `site/` or `website/`. On
kysely the PageRank ablation returned six `example/` and `site/` files; on
hono it returned benchmark harnesses. Example and benchmark code is small,
densely interlinked and self-contained, which is exactly the shape that scores
well on an import graph — and exactly what a newcomer should not be sent to.
Excluding it moved full scoring from 0.333 to 0.375.

The exclusion was adopted because that code is not the project, not because it
improved the number. With four repositories and a ground truth this thin,
tuning against the measurement would be overfitting; the defect recorded under
"Milestone 5" — selection picking empty `__init__.py` files — is still open for
the same reason, and now has a harness to be judged against when someone does
fix it.

## Replacing the dead ground-truth source

255 tests green. `CONTRIBUTING.md` is gone as a source; documentation
references replace it. `eval/probe_sources.py` records what was measured.

**Every candidate was probed before choosing, not assumed.** Yields, using the
same extractor and `known` filter the harness scores with:

| candidate | repositories yielding anything |
|---|---|
| README alone | 0 of 4 |
| `CODEOWNERS` | 0 of 4 — the file exists in none of them |
| `ARCHITECTURE.md` / `docs/architecture*` | 0 of 4 — exists in none of them |
| `docs/` tree, paths | 1 of 4 |
| all prose, paths | 2 of 4 |
| all prose, dotted modules | 2 of 4 (both Python) |
| **all prose, both forms** | **3 of 4** |

`docs/` was the wrong place to look: kysely documents under `site/` and hono
documents in a separate repository. Only the widest form reaches three, and
nothing reaches four — hono's documentation is not in the repository at all,
so no source located inside the pin can ever cover it.

**One proposal was disqualified before testing.** "Files touched by more than
half of merged pull requests" is churn under another name. It would have
inflated the union while contributing nothing to the independent column, whose
entire purpose is to exclude the scorer's own inputs.

**Weakest assumption so far:** that a documentation reference indicates
importance. scrapy falsifies it — its Sphinx tree names 50.8% of its modules,
each reference correct and the set worthless, because six files drawn at
random would score about 0.49 against it. Hence the selectivity guard at 25%.
The threshold itself is a judgement call with one observation behind it: 50.8%
is clearly too broad and 5.3% (poetry) is clearly fine, but nothing in the
data says where between them the line belongs.

**What will break on a real repository:** the guard is a share of repository
size, so a large repository with proportionally thorough documentation passes
where a small one with the same habit fails. And the dotted-module extractor
is Python-only by construction — TypeScript has no equivalent convention — so
the documentation source is systematically stronger for Python repositories.

### The result got worse, and that is the finding

**The independent score dropped from 0.250 to 0.222 when the new source was
added.** Write it that way round and do not soften it. The tool did not
improve and then get measured differently; the measurement got wider and the
tool did worse under it. Coverage went from two repositories to three, poetry
entered with full scoring at 0/6, and the average fell. The earlier 0.250 was
the more flattering number because it rested on less evidence.

**Full scoring loses to the PageRank-only ablation on poetry**: 0/6 against
1/6 on independent ground truth. It wins on scrapy (3/6 against 2/6) and on
kysely (1/6 against 0/6), so it wins on average — two of three, not three of
three. The thesis of this project is that computed selection beats the
alternatives; on one of the three repositories that can be measured
independently, the crudest alternative beat it.

Both facts are stated at the top of the README results section, above the
commentary, because the natural place for them is exactly where a reader
skimming for a headline number will not look.

The union column is unchanged at 0.375 / 0.250, because the documentation
source added paths that churn and beginner issues had largely already found.

### Direct model ordering was never measured

No Anthropic API key was available in the environment this was built in —
`ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` are unset, the `ant` CLI is not
installed, and there is no credential profile on disk. So `AnthropicGenerator`
has never executed against a live API, and the `Direct model ordering` row
reads "not tested" rather than carrying a number.

This is the one claim in the README that remains entirely unevidenced: the
project is built on the premise that computed ordering beats asking a model,
and that comparison has not been run. Anyone with a key can run it; nothing
else is missing.

## Findings carried into milestone 3

Recorded during the milestone 2 review, deliberately not fixed yet.

1. **`doc_signal` is dead.** `graph.py` hardcodes it to `0.0` pending README
   parsing, but `weights.toml` gives it 0.05 and README publishes a six-term
   formula. The formula in the README is currently a claim, not a description.
2. **Five of seven entry-point signals are unimplemented.** Only
   `conventional_filename` and `zero_in_degree_high_out` are read.
   `named_in_manifest`, `main_guard_or_app_init`, `dockerfile_or_makefile` and
   `named_in_readme_codeblock` exist as `Weights` fields that nothing consumes.
   Note that `test_graph.py` has a test named
   `test_entry_point_detected_by_convention_and_main_guard` — it passes on the
   convention signal alone, so its name overstates what is covered.
3. **`[selection]` in `weights.toml` is never read.** `min_stations`,
   `max_stations`, `require_layer_mix` and `same_folder_repeat` are duplicated
   as hardcoded defaults in `select_stations`. Editing the file does nothing.
4. **The folder cap is abandoned whenever layer diversity comes up short.**
   The top-up loop that reaches `min_count` ignores `used_folders` entirely, so
   on a single-package repo the tour is four files from one directory — exactly
   the "six files from the same layer" failure the module docstring says it
   exists to prevent. Working as written; the design is what is questionable.
5. **`numpy` and `scipy` were undeclared dependencies.** `nx.pagerank`
   delegates to its scipy implementation, which imports both; networkx ships
   them only under its optional `default` extra. 19 of 81 tests failed on a
   clean install. Fixed in `pyproject.toml` — the only item here that was not
   left alone, because the suite cannot be green without it.
6. **`Path(".").name` is empty**, so `repoonboard plan .` and `analyze .` print
   a table titled `— learning path` with no repository name. Cosmetic.
7. **Dead code in `stations.py`:** `networkx` is imported but unused, and the
   candidate comprehension carries `if not item.language or True`, a filter
   that is always true. `graph.py` computes `max_distance` and never reads it.
   All three are reported by `ruff check` and left in place deliberately;
   `ruff` is not clean on the pre-milestone-3 modules.
8. **The CI badge in README points at `.github/workflows/ci.yml`, which does
   not exist** in the repository. The badge cannot ever turn green.
9. **CLAUDE.md says it is gitignored, but it is committed** and published on
   GitHub. Either the comment is wrong or the file should be untracked.
