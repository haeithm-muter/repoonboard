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
