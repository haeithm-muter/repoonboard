# Releasing

Everything here has been verified except the final upload, which needs
credentials this project has never held.

## What is already checked

- `python -m build` produces both a wheel and an sdist.
- `twine check` passes on both.
- The wheel contains `weights.toml`. It is read at runtime by
  `graph.load_weights`, so a wheel without it installs cleanly and then fails
  on the first `plan`. This was verified by installing the wheel into an empty
  virtual environment and running `analyze` and `plan` against a real
  repository, not by reading the build config.
- The name `repoonboard` is unclaimed on both PyPI and TestPyPI (both returned
  404 at the time of writing — re-check before releasing, names get taken).
- `.claude/` and `CLAUDE.md` are excluded from the sdist. Tests and `eval/` are
  included on purpose, so the suite and the README's numbers can both be
  reproduced from the source distribution.

## One-time setup

Trusted Publishing means no API token is ever stored in the repository. On
<https://pypi.org/manage/account/publishing/>, add a pending publisher:

| Field | Value |
|---|---|
| PyPI project name | `repoonboard` |
| Owner | `haeithm-muter` |
| Repository | `repoonboard` |
| Workflow | `publish.yml` |
| Environment | `pypi` |

Then create a GitHub environment named `pypi` in the repository settings.
Repeat on <https://test.pypi.org> if you want the TestPyPI dry run.

## Releasing

1. Update `__version__` in `src/repoonboard/__init__.py`. That is the only
   place the version lives; `pyproject.toml` reads it from there, and
   `tests/test_packaging.py` asserts the installed metadata agrees.
2. Commit, tag, push.
3. Run the `Publish` workflow manually with `target: testpypi` first, and
   install from TestPyPI into a clean environment to confirm the package works
   when it is not built from the local tree.
4. Create a GitHub Release. That triggers the upload to PyPI.

A version can only be uploaded once. There is no overwriting a bad release,
only yanking it and publishing a new number.

## Known gaps in this process

- **Neither workflow has ever run.** `ci.yml` and `publish.yml` are committed
  and their YAML parses, but no push has exercised either, so the CI badge
  state and the 3.11/3.12/3.13 matrix results are unverified. If tree-sitter
  has no wheel for 3.13, that job will fail; narrow `requires-python` rather
  than ignore it.
- **There is no changelog.** Commit messages carry the detail; nothing
  summarises a release for someone who will not read them.
- **The upload step itself is untested**, by definition — it needs credentials
  and it cannot be rehearsed against PyPI without consuming a version number.
  TestPyPI is the rehearsal; use it.
- **`Direct model ordering` in the README results table is untested**, because
  no Anthropic API key was available. Publishing does not change that: the
  project's central comparison ships unmeasured, and the README says so.
