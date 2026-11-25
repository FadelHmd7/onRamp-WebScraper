"""Versions that live in two places must agree."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_dockerfile_playwright_matches_pyproject() -> None:
    """The worker installs Chromium for the Dockerfile's version and runs the
    project's version; a mismatch makes Playwright look for a browser that is
    not there."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pins = [
        d for d in pyproject["project"]["optional-dependencies"]["browser"] if "playwright" in d
    ]
    assert pins == [pins[0]] and pins[0].startswith("playwright==")
    project_version = pins[0].split("==")[1]

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(r"^ARG PLAYWRIGHT_VERSION=(\S+)$", dockerfile, re.MULTILINE)
    assert match is not None
    assert match.group(1) == project_version
