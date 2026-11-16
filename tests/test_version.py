"""The version has to move with the tag.

Every release up to v0.1.4 shipped with ``version = "0.1.0"`` in pyproject.toml,
so ``pip show devopen`` could not tell two tags apart and deployments had to be
verified by reading the installed source. These tests keep the two copies of
the version in step and refuse to let a tag be cut from a stale placeholder.
"""

from __future__ import annotations

import pathlib
import re

import devopen

PYPROJECT = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"


def _packaged_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match is not None, "pyproject.toml has no [project] version"
    return match.group(1)


def test_source_and_packaging_agree_on_the_version():
    assert devopen.__version__ == _packaged_version()


def test_version_is_more_specific_than_the_original_placeholder():
    # 0.1.0 was the version at every tag from v0.1.0 to v0.1.4; a release cut
    # from it is indistinguishable from any other in pip metadata.
    assert devopen.__version__ != "0.1.0"
