"""Shared test configuration.

Two environment quirks motivate this file:

* ``pytest`` is not installed in every environment this project is developed in
  (the suite can also be driven by a tiny runner that stubs the few pytest
  features the tests use), so nothing here may rely on pytest internals.
* The operating system temp directory may be read-only, which makes pytest's
  built-in ``tmp_path`` fail. The fixture below therefore hands out a fresh
  scratch directory *inside the project* and deletes it when the test ends.

The fixture is deliberately named ``tmp_path``: a fixture defined in
``conftest.py`` takes precedence over the built-in one, so every test keeps
using ``tmp_path`` unchanged, whether pytest is installed or not.
"""

from __future__ import annotations

import itertools
import shutil
from pathlib import Path
from typing import Iterator

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRATCH_ROOT = _PROJECT_ROOT / "_scratch"
_counter = itertools.count(1)


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    """Yield an empty scratch directory, then remove it (even on failure)."""
    _SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    path = _SCRATCH_ROOT / f"case{next(_counter):03d}"
    if path.exists():
        shutil.rmtree(path)
    path.mkdir()

    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
