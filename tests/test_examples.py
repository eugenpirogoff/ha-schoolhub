"""The example views: valid YAML, and the English one generated from the German one."""

from __future__ import annotations

from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "script"))

from generate_examples import SOURCE, TARGET, generate  # noqa: E402


def test_english_example_is_up_to_date() -> None:
    """Run script/generate_examples.py after changing child_view.de.yaml."""
    assert TARGET.read_text(encoding="utf-8") == generate()


def test_examples_are_single_views() -> None:
    for path in (SOURCE, TARGET):
        view = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert view["type"] == "sections", path.name
        assert view["sections"], path.name
