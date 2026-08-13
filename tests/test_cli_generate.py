"""End-to-end coverage of `generate --dry-run`.

Exercises discovery, the graph, selection, ordering, snippets, the gate, the
structural path and all four exports in one pass, with no model and no
network. If this test is green, the offline pipeline works on a real
directory rather than only on fixtures.
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
        '"""Formatting helpers."""\n\n\n'
        "def shorten(text):\n"
        "    return text[:10]\n"
    ),
    "tests/test_user.py": (
        "from app.models.user import User\n\n\n"
        "def test_user():\n"
        "    assert User(1).identifier == 1\n"
    ),
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for relative, content in FILES.items():
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"],
        check=True,
        capture_output=True,
    )
    return tmp_path


def test_dry_run_succeeds_and_writes_every_artefact(repo):
    result = runner.invoke(app, ["generate", str(repo), "--dry-run"])
    assert result.exit_code == 0, result.output

    assert (repo / ".repoonboard" / "stations.json").is_file()
    assert (repo / ".tours" / "onboarding.tour").is_file()
    assert (repo / "ONBOARDING.md").is_file()
    assert (repo / "architecture.mmd").is_file()


def test_the_tour_is_pinned_to_the_head_commit(repo):
    runner.invoke(app, ["generate", str(repo), "--dry-run"])

    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    document = json.loads((repo / ".tours" / "onboarding.tour").read_text(encoding="utf-8"))
    assert document["ref"] == head


def test_no_step_opens_on_an_import_line(repo):
    runner.invoke(app, ["generate", str(repo), "--dry-run"])
    document = json.loads((repo / ".tours" / "onboarding.tour").read_text(encoding="utf-8"))

    for step in document["steps"]:
        source_line = (repo / step["file"]).read_text(encoding="utf-8").splitlines()[
            step["line"] - 1
        ]
        assert not source_line.lstrip().startswith(("import ", "from ")), step


def test_every_answer_location_is_inside_its_file(repo):
    runner.invoke(app, ["generate", str(repo), "--dry-run"])
    payload = json.loads((repo / ".repoonboard" / "stations.json").read_text(encoding="utf-8"))

    for station in payload["stations"]:
        for question in station["questions"]:
            location = question["answer_location"]
            total = len((repo / location["path"]).read_text(encoding="utf-8").splitlines())
            assert 1 <= location["start_line"] <= location["end_line"] <= total


def test_test_files_never_become_stations(repo):
    runner.invoke(app, ["generate", str(repo), "--dry-run"])
    payload = json.loads((repo / ".repoonboard" / "stations.json").read_text(encoding="utf-8"))
    assert all(not s["path"].startswith("tests/") for s in payload["stations"])


def test_running_twice_produces_identical_output(repo):
    runner.invoke(app, ["generate", str(repo), "--dry-run"])
    first = (repo / "ONBOARDING.md").read_text(encoding="utf-8")

    runner.invoke(app, ["generate", str(repo), "--dry-run"])
    second = (repo / "ONBOARDING.md").read_text(encoding="utf-8")

    assert first == second


def test_a_hand_written_onboarding_file_is_not_overwritten(repo):
    mine = "# Our own onboarding notes\n\nWritten by a human.\n"
    (repo / "ONBOARDING.md").write_text(mine, encoding="utf-8")

    result = runner.invoke(app, ["generate", str(repo), "--dry-run"])

    assert (repo / "ONBOARDING.md").read_text(encoding="utf-8") == mine
    assert "Refused to overwrite" in result.output
    # The rest of the tour is still produced.
    assert (repo / ".tours" / "onboarding.tour").is_file()


def test_force_replaces_a_hand_written_file(repo):
    (repo / "ONBOARDING.md").write_text("# Mine\n", encoding="utf-8")

    runner.invoke(app, ["generate", str(repo), "--dry-run", "--force"])

    assert "Generated by RepoOnboard" in (repo / "ONBOARDING.md").read_text(encoding="utf-8")


def test_regenerating_over_our_own_output_needs_no_force(repo):
    runner.invoke(app, ["generate", str(repo), "--dry-run"])
    result = runner.invoke(app, ["generate", str(repo), "--dry-run"])

    assert "Refused to overwrite" not in result.output
