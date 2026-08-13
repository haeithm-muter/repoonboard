from pathlib import Path

import pytest

from repoonboard.discovery import (
    SourceFile,
    discover,
    dominant_languages,
    is_generated,
    is_test_file,
)


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    files = {
        "main.py": "import app\n",
        "app/core.py": "x = 1\n",
        "app/utils.py": "y = 2\n",
        "tests/test_core.py": "assert True\n",
        "web/index.ts": "export const a = 1;\n",
        "web/widget.spec.ts": "it('works', () => {});\n",
        "node_modules/left-pad/index.js": "module.exports = 1;\n",
        "dist/bundle.min.js": "var a=1;\n",
        "app/schema_pb2.py": "GENERATED\n",
        "README.md": "# docs\n",
        "app/__pycache__/core.cpython-311.pyc": "binary\n",
    }
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp_path


def test_excluded_directories_are_dropped(sample_repo: Path):
    found = {item.posix for item in discover(sample_repo)}
    assert "node_modules/left-pad/index.js" not in found
    assert "dist/bundle.min.js" not in found
    assert "app/__pycache__/core.cpython-311.pyc" not in found


def test_generated_files_are_dropped(sample_repo: Path):
    found = {item.posix for item in discover(sample_repo)}
    assert "app/schema_pb2.py" not in found


def test_non_source_files_are_dropped(sample_repo: Path):
    found = {item.posix for item in discover(sample_repo)}
    assert "README.md" not in found


def test_authored_sources_are_kept(sample_repo: Path):
    found = {item.posix for item in discover(sample_repo)}
    assert {"main.py", "app/core.py", "app/utils.py", "web/index.ts"} <= found


def test_tests_are_flagged_but_retained(sample_repo: Path):
    by_path = {item.posix: item for item in discover(sample_repo)}
    assert by_path["tests/test_core.py"].is_test is True
    assert by_path["web/widget.spec.ts"].is_test is True
    assert by_path["app/core.py"].is_test is False


def test_discovery_is_deterministic(sample_repo: Path):
    first = [item.posix for item in discover(sample_repo)]
    second = [item.posix for item in discover(sample_repo)]
    assert first == second == sorted(first)


def test_subdir_restricts_scope(sample_repo: Path):
    found = {item.posix for item in discover(sample_repo, subdir="web")}
    assert found == {"web/index.ts", "web/widget.spec.ts"}


def test_missing_subdir_raises(sample_repo: Path):
    with pytest.raises(NotADirectoryError):
        discover(sample_repo, subdir="nope")


def test_line_counts_are_recorded(sample_repo: Path):
    by_path = {item.posix: item for item in discover(sample_repo)}
    assert by_path["main.py"].line_count == 1


@pytest.mark.parametrize(
    "path",
    ["tests/test_core.py", "app/core_test.py", "src/__tests__/a.ts", "ui/button.spec.tsx"],
)
def test_test_file_detection_positive(path: str):
    assert is_test_file(Path(path)) is True


@pytest.mark.parametrize("path", ["app/core.py", "src/latest.py", "web/index.ts"])
def test_test_file_detection_negative(path: str):
    assert is_test_file(Path(path)) is False


@pytest.mark.parametrize("path", ["a.min.js", "types.d.ts", "schema_pb2.py"])
def test_generated_detection_positive(path: str):
    assert is_generated(Path(path)) is True


def test_generated_detection_negative():
    assert is_generated(Path("app/core.py")) is False


def test_dominant_languages_excludes_tests():
    files = [
        SourceFile(Path("a.py"), "python", False, 10),
        SourceFile(Path("b.py"), "python", False, 10),
        SourceFile(Path("c.ts"), "typescript", False, 10),
        SourceFile(Path("test_a.py"), "python", True, 10),
    ]
    assert dominant_languages(files) == {"python": 2, "typescript": 1}
