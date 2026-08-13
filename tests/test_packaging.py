"""Guards on the published artefact.

These are cheap and they cover the failure mode that only appears after
install, when it is too late: a version that misreports itself, or a data file
the code reads at runtime that never made it into the wheel.
"""

from importlib import metadata
from pathlib import Path

import repoonboard
from repoonboard.graph import load_weights


def test_the_declared_version_matches_the_installed_metadata():
    # The version is declared once, in __init__.py, and hatchling reads it
    # from there. If that wiring breaks, the package ships announcing a
    # version it is not.
    assert repoonboard.__version__ == metadata.version("repoonboard")


def test_weights_toml_travels_with_the_package():
    # load_weights reads this file at runtime. A wheel without it installs
    # perfectly and then fails on the first `plan`.
    bundled = Path(repoonboard.__file__).parent / "weights.toml"
    assert bundled.is_file()


def test_the_bundled_weights_actually_parse():
    weights = load_weights()
    assert weights.pagerank_reversed > 0
    assert weights.entry_threshold > 0


def test_the_console_script_is_declared():
    scripts = metadata.entry_points(group="console_scripts")
    assert any(entry.name == "repoonboard" for entry in scripts)
