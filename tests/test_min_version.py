"""The minimum Home Assistant version is maintained in hacs.json only."""

import json
from pathlib import Path
import re

ROOT = Path(__file__).parent.parent


def _min_version() -> str:
    return json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))["homeassistant"]


def test_min_version_is_tested() -> None:
    """The tests-min CI job runs the minimum version from hacs.json."""
    lock = (ROOT / "requirements_test_min.txt").read_text(encoding="utf-8")
    assert re.search(rf"^homeassistant=={re.escape(_min_version())} ", lock, re.M)


def test_review_instructions_use_min_version() -> None:
    """The CodeRabbit instructions name the minimum version from hacs.json."""
    config = (ROOT / ".coderabbit.yaml").read_text(encoding="utf-8")
    assert re.findall(r"min\. HA ([0-9.]+)", config) == [_min_version()]
