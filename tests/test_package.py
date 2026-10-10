"""Smoke test: the package imports and reports its version."""

import skill_factory


def test_version():
    assert skill_factory.__version__ == "0.1.0"
