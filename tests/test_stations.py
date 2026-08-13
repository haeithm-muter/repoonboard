import subprocess
from pathlib import Path

import pytest

from repoonboard.graph import build
from repoonboard.stations import classify_layer, order_stations, select_stations


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


# ---------------------------------------------------------------------------
# Layer classification
# ---------------------------------------------------------------------------


def test_entry_flag_overrides_folder_name():
    assert classify_layer(Path("api/main.py"), is_entry=True) == "entry"


def test_routing_folder():
    assert classify_layer(Path("app/routes/users.py"), is_entry=False) == "routing"


def test_data_folder():
    assert classify_layer(Path("app/models/user.py"), is_entry=False) == "data"


def test_utils_folder():
    assert classify_layer(Path("src/utils/format.ts"), is_entry=False) == "utils"


def test_default_layer_is_core():
    assert classify_layer(Path("app/service.py"), is_entry=False) == "core"


def test_nested_marker_is_detected():
    assert classify_layer(Path("src/api/v1/handlers/create.ts"), is_entry=False) == "routing"


# ---------------------------------------------------------------------------
# Station selection — built on a small layered repo
# ---------------------------------------------------------------------------


@pytest.fixture
def layered_repo(tmp_path: Path) -> Path:
    files = {
        "main.py": "from app.routes.users import router\nrouter()\n",
        "app/__init__.py": "",
        "app/routes/__init__.py": "",
        "app/routes/users.py": "from app.services.user_service import get_user\ndef router():\n    get_user()\n",
        "app/routes/orders.py": "from app.services.order_service import get_order\ndef router():\n    get_order()\n",
        "app/services/__init__.py": "",
        "app/services/user_service.py": "from app.models.user import User\ndef get_user():\n    return User()\n",
        "app/services/order_service.py": "from app.models.order import Order\ndef get_order():\n    return Order()\n",
        "app/models/__init__.py": "",
        "app/models/user.py": "class User: pass\n",
        "app/models/order.py": "class Order: pass\n",
        "app/utils/__init__.py": "",
        "app/utils/formatting.py": "def fmt(x): return str(x)\n",
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
    return tmp_path


def test_selection_respects_min_and_max(layered_repo: Path):
    repo_graph = build(layered_repo)
    stations = select_stations(repo_graph, min_count=5, max_count=8)
    assert 5 <= len(stations) <= 8


def test_selection_includes_the_entry_point(layered_repo: Path):
    repo_graph = build(layered_repo)
    stations = select_stations(repo_graph)
    assert any(s.path == Path("main.py") for s in stations)


def test_selection_covers_multiple_layers(layered_repo: Path):
    repo_graph = build(layered_repo)
    stations = select_stations(repo_graph)
    layers = {s.layer for s in stations}
    assert len(layers) >= 3  # entry, routing/core, data at minimum


def test_selection_is_deterministic(layered_repo: Path):
    repo_graph = build(layered_repo)
    first = [s.path for s in select_stations(repo_graph)]
    second = [s.path for s in select_stations(repo_graph)]
    assert first == second


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_entry_point_is_first_in_order(layered_repo: Path):
    repo_graph = build(layered_repo)
    stations = select_stations(repo_graph)
    result = order_stations(repo_graph, stations)
    assert result.stations[0] == Path("main.py")


def test_order_follows_execution_not_raw_score(layered_repo: Path):
    repo_graph = build(layered_repo)
    stations = select_stations(repo_graph)
    result = order_stations(repo_graph, stations)

    # A model/data file must never appear before the route that leads to it,
    # even if it independently scores higher (e.g. imported by many things).
    if Path("app/routes/users.py") in result.stations and Path("app/models/user.py") in result.stations:
        assert result.stations.index(Path("app/routes/users.py")) < result.stations.index(
            Path("app/models/user.py")
        )


def test_ordering_includes_every_selected_station(layered_repo: Path):
    repo_graph = build(layered_repo)
    stations = select_stations(repo_graph)
    result = order_stations(repo_graph, stations)
    assert set(result.stations) == {s.path for s in stations}


def test_ordering_handles_a_cycle_without_hanging(tmp_path: Path):
    files = {
        "main.py": "from a import run\nrun()\n",
        "a.py": "from b import helper\ndef run():\n    helper()\n",
        "b.py": "from a import run as _r\ndef helper():\n    pass\n",  # a <-> b cycle
        "c.py": "def standalone(): pass\n",
        "d.py": "def other(): pass\n",
    }
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")

    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "dev@example.com")
    _git(tmp_path, "config", "user.name", "Dev")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")

    repo_graph = build(tmp_path)
    stations = select_stations(repo_graph, min_count=3, max_count=5)
    result = order_stations(repo_graph, stations)  # must terminate, not infinite-loop

    assert set(result.stations) == {s.path for s in stations}
