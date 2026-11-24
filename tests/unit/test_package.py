import importlib

import onramp_scraper

SUBPACKAGES = ["crawler", "processing", "storage", "worker", "api", "autoscaler"]


def test_version_is_set() -> None:
    assert onramp_scraper.__version__ == "0.1.0"


def test_subpackages_import() -> None:
    for name in SUBPACKAGES:
        importlib.import_module(f"onramp_scraper.{name}")
