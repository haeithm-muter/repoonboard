import subprocess
from pathlib import Path

import pytest

from repoonboard.graph import build, load_weights


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def small_repo(tmp_path: Path) -> Path:
    """A tiny but realistic layered Python repo with a real git history."""
    files = {
        "main.py": (
            "from app.core import run\n"
            "if __name__ == '__main__':\n"
            "    run()\n"
        ),
        "app/__init__.py": "",
        "app/core.py": "from app.utils import helper\ndef run():\n    helper()\n",
        "app/utils.py": "def helper():\n    pass\n",
        "tests/test_core.py": "from app.core import run\ndef test_run():\n    run()\n",
    }
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "dev@example.com")
    _git(tmp_path, "config", "user.name", "Dev")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")

    for _ in range(3):
        (tmp_path / "app" / "core.py").write_text(
            (tmp_path / "app" / "core.py").read_text() + "# tweak\n", encoding="utf-8"
        )
        _git(tmp_path, "add", "app/core.py")
        _git(tmp_path, "commit", "-qm", "edit core")

    return tmp_path


def test_load_weights_sums_to_one():
    weights = load_weights()
    total = (
        weights.pagerank_reversed
        + weights.fan_in_normalized
        + weights.entry_proximity
        + weights.churn_normalized
        + weights.test_coverage
        + weights.doc_signal
    )
    assert total == pytest.approx(1.0, abs=1e-6)


def test_build_creates_edges(small_repo: Path):
    repo_graph = build(small_repo)
    assert (Path("main.py"), Path("app/core.py")) in repo_graph.graph.edges
    assert (Path("app/core.py"), Path("app/utils.py")) in repo_graph.graph.edges


def test_resolution_rate_is_high_on_clean_repo(small_repo: Path):
    repo_graph = build(small_repo)
    assert repo_graph.resolution_rate == pytest.approx(1.0)


def test_entry_point_detected_by_convention_and_main_guard(small_repo: Path):
    repo_graph = build(small_repo)
    assert Path("main.py") in repo_graph.entry_points


def test_every_file_has_a_score(small_repo: Path):
    repo_graph = build(small_repo)
    for item in repo_graph.files:
        assert item.path in repo_graph.scores


def test_higher_churn_file_scores_higher_than_untouched_sibling(small_repo: Path):
    repo_graph = build(small_repo)
    # app/core.py was committed 4 times, app/utils.py once — and core is also
    # imported by both main.py and the test, so it should clearly outrank it.
    assert repo_graph.scores[Path("app/core.py")] > repo_graph.scores[Path("app/utils.py")]


def test_entry_point_has_zero_distance(small_repo: Path):
    repo_graph = build(small_repo)
    breakdown = repo_graph.score_breakdown[Path("main.py")]
    # entry_proximity component should be at its max weight for the entry itself
    assert breakdown["entry_proximity"] == pytest.approx(0.15, abs=1e-6)


def test_score_breakdown_components_present(small_repo: Path):
    repo_graph = build(small_repo)
    breakdown = repo_graph.score_breakdown[Path("app/core.py")]
    assert set(breakdown) == {
        "pagerank_reversed",
        "fan_in_normalized",
        "entry_proximity",
        "churn_normalized",
        "test_coverage",
        "doc_signal",
    }


def test_test_files_are_not_entry_points(small_repo: Path):
    repo_graph = build(small_repo)
    assert Path("tests/test_core.py") not in repo_graph.entry_points


def test_fallback_entry_point_never_picks_a_test_file(tmp_path: Path):
    # A library with no conventional entry point at all — the only
    # zero-in-degree, high-out-degree file is the test suite. The fallback
    # must still refuse to hand onboarding to it.
    files = {
        "pkg/__init__.py": "from pkg.core import thing\n",
        "pkg/core.py": "x = 1\n",
        "tests/test_all.py": (
            "from pkg.core import thing\n"
            "from pkg import thing2\n"
            "from pkg.core import thing3\n"
            "from pkg.core import thing4\n"
        ),
    }
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    repo_graph = build(tmp_path)
    assert all(not repo_graph.graph.nodes[e].get("is_test") for e in repo_graph.entry_points)


def test_repo_without_git_still_builds(tmp_path: Path):
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    repo_graph = build(tmp_path)
    assert repo_graph.scores[Path("main.py")] >= 0


def test_deterministic_scores_across_runs(small_repo: Path):
    first = build(small_repo).scores
    second = build(small_repo).scores
    assert first == second
