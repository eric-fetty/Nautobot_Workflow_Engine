"""Lossless canonical round trip for examples and the schema corpus."""

import unittest
from pathlib import Path

from nautobot_workflow_engine.schema.normalize import normalize
from nautobot_workflow_engine.schema.validate import validate
from nautobot_workflow_engine.schema.yaml_io import from_yaml, to_yaml

ROOT = Path(__file__).resolve().parents[2]
CORPUS = list((Path(__file__).resolve().parent / "fixtures" / "schema_corpus").glob("*.yaml"))
EXAMPLES = list((ROOT / "examples" / "workflows").glob("*.yaml"))


def _round_trip(text):
    first = normalize(from_yaml(text))
    errors = validate(first)
    assert not errors, errors
    second = normalize(from_yaml(to_yaml(first)))
    return first, second


class SchemaRoundTripTestCase(unittest.TestCase):
    """Every example and corpus file survives YAML and normalize unchanged."""

    def test_examples_and_corpus(self):
        paths = EXAMPLES + CORPUS
        self.assertGreaterEqual(len(paths), 3)
        for path in paths:
            with self.subTest(path=path.name):
                first, second = _round_trip(path.read_text(encoding="utf-8"))
                self.assertEqual(second, first)
                self.assertNotIn("enabled", first)
