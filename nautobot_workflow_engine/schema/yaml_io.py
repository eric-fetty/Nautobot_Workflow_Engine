"""Canonical YAML for a workflow definition."""

import hashlib

import yaml

from . import CANONICAL_KEY_ORDER


class _Literal(str):
    """A string that should be dumped as a block scalar."""


def _represent_literal(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


def _represent_str(dumper, data):
    if "\n" in data:
        return _represent_literal(dumper, data)
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


class _Dumper(yaml.SafeDumper):
    """Dumper that keeps mapping order and uses block scalars for multi-line strings."""


_Dumper.add_representer(str, _represent_str)
_Dumper.add_representer(_Literal, _represent_literal)


def content_hash(text):
    """Return the sha256 hex digest of YAML bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def to_yaml(canonical):
    """Dump a canonical definition. Key order follows the schema. Empty values are already omitted."""
    dumped = yaml.dump(
        canonical if isinstance(canonical, dict) else {},
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        width=100,
        default_flow_style=False,
    )
    if dumped is None:
        return ""
    if not dumped.endswith("\n"):
        dumped += "\n"
    return dumped


def from_yaml(text):
    """Parse YAML text with ``safe_load``. Non-mappings are returned as loaded."""
    if text is None:
        return None
    loaded = yaml.safe_load(text)
    return loaded


def order_mapping(data):
    """Return ``data`` with schema keys first and unknown keys after, in their original order."""
    if not isinstance(data, dict):
        return data
    ordered = {}
    for key in CANONICAL_KEY_ORDER:
        if key in data:
            ordered[key] = data[key]
    for key, value in data.items():
        if key not in ordered:
            ordered[key] = value
    return ordered
