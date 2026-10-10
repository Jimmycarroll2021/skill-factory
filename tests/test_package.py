"""Smoke test: the package imports and reports its version."""

import skillz_factory


def test_version():
    assert skillz_factory.__version__ == "0.1.0"
