"""Ready-made validation rules offered by the builder's Insert preset menu.

The list lives in ``validator_presets.json`` so the Python and JavaScript tests read the
same file. Each preset carries example values that must pass and fail in both engines.
"""

import json
from functools import lru_cache
from pathlib import Path

PRESETS_PATH = Path(__file__).resolve().parent / "validator_presets.json"


@lru_cache(maxsize=1)
def validator_presets():
    """Return every preset, with its examples, in menu order."""
    return json.loads(PRESETS_PATH.read_text(encoding="utf-8"))["presets"]


def builder_presets():
    """Return what the builder menu needs: id, label, and the rule it inserts."""
    return [
        {
            "id": preset["id"],
            "label": preset["label"],
            "regex": preset["regex"],
            "message": preset["message"],
            "flags": list(preset.get("flags") or []),
        }
        for preset in validator_presets()
    ]
