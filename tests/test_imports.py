from pathlib import Path

from repoonboard.imports import RawImport, extract, resolve_js, resolve_python


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def test_python_absolute_import():
    specs = extract(b"import os\n", "python")
    assert RawImport("os", 0) in specs


def test_python_dotted_import():
    specs = extract(b"import app.models\n", "python")
    assert RawImport("app.models", 0) in specs


def test_python_aliased_import():
    specs = extract(b"import app.models as models\n", "python")
    assert RawImport("app.models", 0) in specs


def test_python_from_import_absolute():
    specs = extract(b"from app.core import thing\n", "python")
    assert RawImport("app.core", 0) in specs


def test_python_from_import_relative_single_dot():
    specs = extract(b"from .utils import helper\n", "python")
    assert RawImport("utils", 1) in specs


def test_python_from_import_relative_double_dot():
    specs = extract(b"from ..pkg.sub import helper\n", "python")
    assert RawImport("pkg.sub", 2) in specs


def test_python_from_import_bare_dot():
    specs = extract(b"from . import sibling\n", "python")
    assert RawImport("", 1) in specs


def test_javascript_static_import():
    specs = extract(b"import { a } from './widget';\n", "javascript")
    assert RawImport("./widget", 0) in specs


def test_javascript_default_import():
    specs = extract(b"import React from 'react';\n", "javascript")
    assert RawImport("react", 0) in specs


def test_javascript_require():
    specs = extract(b"const x = require('../lib/auth');\n", "javascript")
    assert RawImport("../lib/auth", 0) in specs


def test_javascript_dynamic_import():
    specs = extract(b"const mod = import('./lazy');\n", "javascript")
    assert RawImport("./lazy", 0) in specs


def test_javascript_export_from():
    specs = extract(b"export { thing } from './thing';\n", "javascript")
    assert RawImport("./thing", 0) in specs


def test_typescript_static_import():
    specs = extract(b"import { Widget } from './widget';\n", "typescript")
    assert RawImport("./widget", 0) in specs


def test_typescript_type_import():
    specs = extract(b"import type { Props } from './types';\n", "typescript")
    assert RawImport("./types", 0) in specs


# ---------------------------------------------------------------------------
# Resolution — Python
# ---------------------------------------------------------------------------


PY_FILES = frozenset(
    Path(p)
    for p in [
        "app/__init__.py",
        "app/core.py",
        "app/utils.py",
        "app/sub/__init__.py",
        "app/sub/helper.py",
        "main.py",
    ]
)


def test_resolve_python_absolute_dotted():
    result = resolve_python(RawImport("app.core", 0), Path("main.py"), PY_FILES)
    assert result == Path("app/core.py")


def test_resolve_python_absolute_package():
    result = resolve_python(RawImport("app.sub", 0), Path("main.py"), PY_FILES)
    assert result == Path("app/sub/__init__.py")


def test_resolve_python_relative_single_dot():
    result = resolve_python(RawImport("utils", 1), Path("app/core.py"), PY_FILES)
    assert result == Path("app/utils.py")


def test_resolve_python_relative_bare_dot_sibling():
    # "from . import core" written from app/utils.py — level 1, no module name.
    result = resolve_python(RawImport("", 1), Path("app/utils.py"), PY_FILES)
    assert result == Path("app/__init__.py")


def test_resolve_python_relative_double_dot():
    result = resolve_python(RawImport("core", 2), Path("app/sub/helper.py"), PY_FILES)
    assert result == Path("app/core.py")


def test_resolve_python_external_package_returns_none():
    result = resolve_python(RawImport("numpy", 0), Path("main.py"), PY_FILES)
    assert result is None


def test_resolve_python_src_layout():
    files = frozenset(
        Path(p) for p in ["src/pkg/__init__.py", "src/pkg/core.py", "tests/test_core.py"]
    )
    result = resolve_python(RawImport("pkg.core", 0), Path("tests/test_core.py"), files)
    assert result == Path("src/pkg/core.py")


# ---------------------------------------------------------------------------
# Resolution — JS/TS
# ---------------------------------------------------------------------------


JS_FILES = frozenset(
    Path(p)
    for p in [
        "src/index.ts",
        "src/widget.ts",
        "src/lib/auth.ts",
        "src/components/Button.tsx",
        "src/components/index.ts",
    ]
)


def test_resolve_js_relative_with_extension_added():
    result = resolve_js(RawImport("./widget", 0), Path("src/index.ts"), JS_FILES)
    assert result == Path("src/widget.ts")


def test_resolve_js_parent_relative():
    result = resolve_js(RawImport("../lib/auth", 0), Path("src/components/Button.tsx"), JS_FILES)
    assert result == Path("src/lib/auth.ts")


def test_resolve_js_directory_index():
    result = resolve_js(RawImport("./components", 0), Path("src/index.ts"), JS_FILES)
    assert result == Path("src/components/index.ts")


def test_resolve_js_bare_specifier_is_external():
    result = resolve_js(RawImport("react", 0), Path("src/index.ts"), JS_FILES)
    assert result is None


def test_resolve_js_scoped_package_is_external():
    result = resolve_js(RawImport("@scope/pkg", 0), Path("src/index.ts"), JS_FILES)
    assert result is None


def test_resolve_js_tsx_extension():
    result = resolve_js(RawImport("./Button", 0), Path("src/components/index.ts"), JS_FILES)
    assert result == Path("src/components/Button.tsx")
