"""End-to-end coverage of `check` against a real git repository.

Every assertion here is about what the command actually reports for a change
that was actually made — the point of the milestone is that staleness is
derived from the repository, not assumed.
"""

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from repoonboard.cli import app

runner = CliRunner()

FILES = {
    "main.py": (
        '"""Entry point."""\n'
        "from app.routes.users import router\n\n\n"
        "def main():\n"
        "    return router()\n"
    ),
    "app/__init__.py": "",
    "app/routes/__init__.py": "",
    "app/routes/users.py": (
        '"""User routes."""\n'
        "from app.services.user_service import get_user\n\n\n"
        "def router():\n"
        "    return get_user(1)\n"
    ),
    "app/services/__init__.py": "",
    "app/services/user_service.py": (
        '"""User service."""\n'
        "from app.models.user import User\n\n\n"
        "def get_user(user_id):\n"
        "    return User(user_id)\n"
    ),
    "app/models/__init__.py": "",
    "app/models/user.py": (
        '"""User model."""\n\n\n'
        "class User:\n"
        "    def __init__(self, identifier):\n"
        "        self.identifier = identifier\n"
    ),
    "app/utils/__init__.py": "",
    "app/utils/format.py": (
        '"""Formatting helpers."""\n\n\ndef shorten(text):\n    return text[:10]\n'
    ),
}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message)


@pytest.fixture
def toured(tmp_path: Path) -> Path:
    for relative, content in FILES.items():
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    _git(tmp_path, "init", "-q")
    _commit(tmp_path, "init")

    result = runner.invoke(app, ["generate", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0, result.output
    return tmp_path


def _flat(output: str) -> str:
    """Collapse whitespace so assertions survive rich's line wrapping."""
    return " ".join(output.split())


def _stations(root: Path) -> list[dict]:
    payload = json.loads((root / ".repoonboard" / "stations.json").read_text(encoding="utf-8"))
    return payload["stations"]


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_a_freshly_generated_tour_is_current(toured):
    result = runner.invoke(app, ["check", str(toured)])
    assert result.exit_code == 0, result.output
    assert "The tour is current" in _flat(result.output)


def test_an_unrelated_change_leaves_the_tour_current(toured):
    (toured / "README.md").write_text("# Notes\n", encoding="utf-8")
    _commit(toured, "add readme")

    result = runner.invoke(app, ["check", str(toured)])
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Each kind of staleness, produced by an actual change
# ---------------------------------------------------------------------------


def test_editing_a_cited_range_is_reported_as_answers_changed(toured):
    station = _stations(toured)[0]
    target = toured / station["path"]

    lines = target.read_text(encoding="utf-8").splitlines()
    location = station["questions"][0]["answer_location"]
    index = location["start_line"] - 1
    lines[index] = lines[index] + "  # edited"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _commit(toured, "edit inside a cited range")

    result = runner.invoke(app, ["check", str(toured)])
    assert result.exit_code == 1
    # Asserted on the derived count rather than on the rendered table, whose
    # long paths rich truncates with an ellipsis.
    assert "answers changed" in _flat(result.output)
    assert "1 of" in _flat(result.output) and "need attention" in _flat(result.output)


def test_inserting_above_a_station_shifts_its_lines(toured):
    station = _stations(toured)[0]
    target = toured / station["path"]

    target.write_text("# a new first line\n" + target.read_text(encoding="utf-8"), encoding="utf-8")
    _commit(toured, "insert at the top")

    result = runner.invoke(app, ["check", str(toured)])
    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "lines shifted" in flat or "answers changed" in flat


def test_deleting_a_station_file_is_reported(toured):
    station = _stations(toured)[0]
    (toured / station["path"]).unlink()
    _commit(toured, "delete a station")

    result = runner.invoke(app, ["check", str(toured)])
    assert result.exit_code == 1
    assert "deleted" in _flat(result.output)


def test_renaming_a_station_file_is_reported_as_moved(toured):
    station = _stations(toured)[0]
    source = toured / station["path"]
    destination = source.with_name("renamed_" + source.name)
    _git(toured, "mv", station["path"], str(destination.relative_to(toured)).replace("\\", "/"))
    _commit(toured, "rename a station")

    result = runner.invoke(app, ["check", str(toured)])
    assert result.exit_code == 1
    assert "moved" in _flat(result.output)


# ---------------------------------------------------------------------------
# The case the README says no other tool handles
# ---------------------------------------------------------------------------


def test_a_new_file_that_outranks_the_tour_is_surfaced(toured):
    # A new module that everything imports outranks whatever is in the tour.
    core = toured / "app" / "core.py"
    core.write_text(
        '"""Core domain logic."""\n\n\ndef compute(value):\n    return value * 2\n',
        encoding="utf-8",
    )
    for relative in ("app/routes/users.py", "app/services/user_service.py", "app/models/user.py"):
        target = toured / relative
        text = target.read_text(encoding="utf-8")
        target.write_text(text + "\nfrom app.core import compute\n", encoding="utf-8")
    _commit(toured, "add a widely imported core module")

    result = runner.invoke(app, ["check", str(toured)])
    assert result.exit_code == 1
    assert "would now be selected" in _flat(result.output)
    assert "app/core.py" in _flat(result.output)


# ---------------------------------------------------------------------------
# Refusing to guess
# ---------------------------------------------------------------------------


def test_check_without_a_tour_says_so(tmp_path):
    _git(tmp_path, "init", "-q")
    result = runner.invoke(app, ["check", str(tmp_path)])
    assert result.exit_code == 1
    assert "No tour found" in _flat(result.output)


def test_check_reports_a_pinned_commit_that_is_gone(toured):
    payload = json.loads((toured / ".repoonboard" / "stations.json").read_text(encoding="utf-8"))
    payload["commit"] = "0" * 40
    (toured / ".repoonboard" / "stations.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    result = runner.invoke(app, ["check", str(toured)])
    assert result.exit_code == 1
    assert "not in this clone" in _flat(result.output)


def test_check_reports_an_unpinned_tour(toured):
    payload = json.loads((toured / ".repoonboard" / "stations.json").read_text(encoding="utf-8"))
    payload["commit"] = None
    (toured / ".repoonboard" / "stations.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    result = runner.invoke(app, ["check", str(toured)])
    assert result.exit_code == 1
    assert "not pinned to a commit" in _flat(result.output)


def test_check_reports_a_corrupt_tour_file(toured):
    (toured / ".repoonboard" / "stations.json").write_text("{ not json", encoding="utf-8")
    result = runner.invoke(app, ["check", str(toured)])
    assert result.exit_code == 1
    assert "Could not read" in _flat(result.output)


def test_uncommitted_changes_are_reported_as_such(toured):
    station = _stations(toured)[0]
    target = toured / station["path"]
    target.write_text("# uncommitted first line\n" + target.read_text(encoding="utf-8"),
                      encoding="utf-8")
    # deliberately not committed

    result = runner.invoke(app, ["check", str(toured)])
    assert result.exit_code == 1
    assert "uncommitted changes" in _flat(result.output)


def test_a_clean_tree_at_the_pinned_commit_says_so_without_hedging(toured):
    result = runner.invoke(app, ["check", str(toured)])
    assert "HEAD is the pinned commit." in _flat(result.output)
    assert "uncommitted changes" not in _flat(result.output)


def test_the_all_clear_is_never_printed_alongside_a_finding(toured):
    (toured / _stations(toured)[0]["path"]).unlink()
    _commit(toured, "delete a station")

    result = runner.invoke(app, ["check", str(toured)])
    assert "The tour is current" not in _flat(result.output)
    assert "still hold" not in _flat(result.output)
