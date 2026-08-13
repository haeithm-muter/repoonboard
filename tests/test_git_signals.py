import subprocess
from pathlib import Path

import pytest

from repoonboard.git_signals import (
    NotAGitRepository,
    churn,
    head_commit,
    is_git_repository,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "dev@example.com")
    _git(tmp_path, "config", "user.name", "Dev")

    (tmp_path / "core.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "quiet.py").write_text("y = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")

    for index in range(3):
        (tmp_path / "core.py").write_text(f"x = {index + 2}\n", encoding="utf-8")
        _git(tmp_path, "add", "core.py")
        _git(tmp_path, "commit", "-qm", f"edit {index}")

    return tmp_path


def test_churn_counts_commits_per_file(git_repo: Path):
    counts = churn(git_repo)
    assert counts["core.py"] == 4
    assert counts["quiet.py"] == 1


def test_churn_separates_hot_from_cold(git_repo: Path):
    counts = churn(git_repo)
    assert counts["core.py"] > counts["quiet.py"]


def test_head_commit_is_a_full_hash(git_repo: Path):
    commit = head_commit(git_repo)
    assert len(commit) == 40
    assert all(char in "0123456789abcdef" for char in commit)


def test_head_commit_is_stable(git_repo: Path):
    assert head_commit(git_repo) == head_commit(git_repo)


def test_is_git_repository_true(git_repo: Path):
    assert is_git_repository(git_repo) is True


def test_is_git_repository_false(tmp_path: Path):
    assert is_git_repository(tmp_path) is False


def test_head_commit_raises_outside_a_repository(tmp_path: Path):
    with pytest.raises(NotAGitRepository):
        head_commit(tmp_path)
