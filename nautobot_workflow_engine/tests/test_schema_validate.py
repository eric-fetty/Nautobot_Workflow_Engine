"""Every FieldError code the schema package can emit."""

from pathlib import Path

from django.test import TestCase
from nautobot.extras.models import GitRepository

from nautobot_workflow_engine.models import WorkflowHelper
from nautobot_workflow_engine.schema import load_schema
from nautobot_workflow_engine.schema.builder_payload import definition_from_builder
from nautobot_workflow_engine.schema.errors import BuilderValidationError
from nautobot_workflow_engine.schema.normalize import normalize
from nautobot_workflow_engine.schema.references import find_references, rename_reference
from nautobot_workflow_engine.schema.validate import FIELD_ERROR_CODES, reserved_key_errors, validate


def _definition(**overrides):
    data = {"key": "sample", "name": "Sample"}
    data.update(overrides)
    return data


class FieldErrorCodeTestCase(TestCase):
    """One case per code in FIELD_ERROR_CODES."""

    def _codes(self, document, **kwargs):
        as_written = kwargs.pop("as_written", False)
        canonical = document if as_written else normalize(document)
        return {item["code"] for item in validate(canonical, **kwargs)}

    def test_every_code_is_produced(self):
        produced = set()
        produced |= self._codes({"name": "Sample"})
        produced |= self._codes(_definition(key=""), as_written=True)
        produced |= self._codes(_definition(key="a" * 51))
        produced |= self._codes(_definition(key="Not A Slug"))
        produced |= self._codes(_definition(actions=[{"type": "other", "name": "run"}]))
        produced |= self._codes({"key": 1, "name": "Sample"}, as_written=True)
        produced |= self._codes(_definition(manual_duration_minutes=-1))
        produced |= self._codes(_definition(inputs=[{"key": "mode", "type": "choice", "choices": []}]))
        produced |= self._codes(_definition(inputs="nope"), as_written=True)
        produced |= self._codes(
            _definition(inputs=[{"key": "host", "type": "string"}, {"key": "host", "type": "string"}])
        )
        produced |= self._codes(_definition(inputs=[{"key": "host", "type": "string", "depends_on": ["host"]}]))
        produced |= self._codes(_definition(inputs=[{"key": "host", "type": "string", "depends_on": ["missing"]}]))
        produced |= self._codes(
            _definition(
                inputs=[
                    {"key": "left", "type": "string", "depends_on": ["right"]},
                    {"key": "right", "type": "string", "depends_on": ["left"]},
                ]
            )
        )
        produced |= self._codes(
            _definition(inputs=[{"key": "mode", "type": "choice", "default": "nope", "choices": [{"value": "yes"}]}])
        )
        produced |= self._codes(
            _definition(
                actions=[
                    {"type": "awx", "name": "run", "template_id": 1},
                    {"type": "awx", "name": "run", "template_id": 2},
                ]
            )
        )
        produced |= self._codes(_definition(inputs=[{"key": "device", "type": "object"}]))
        produced |= self._codes(_definition(actions=[{"type": "nautobot", "name": "run", "script": ""}]))
        produced |= self._codes(_definition(actions=[{"type": "awx", "name": "run"}]))
        produced |= self._codes(
            _definition(actions=[{"type": "awx", "name": "run", "template_id": 1, "when": "inputs.confirm"}])
        )
        produced |= self._codes(
            _definition(actions=[{"type": "awx", "name": "run", "template_id": 1, "when": "{{ inputs.confirm "}])
        )
        produced |= self._codes(
            _definition(
                actions=[
                    {"type": "awx", "name": "run", "template_id": 1, "when": '{{ results.later.status == "success" }}'},
                    {"type": "awx", "name": "later", "template_id": 2},
                ]
            )
        )
        produced |= self._codes(
            _definition(
                actions=[
                    {
                        "type": "awx",
                        "name": "run",
                        "template_id": 1,
                        "when": "{{ cycler.__init__.__globals__ }}",
                    }
                ]
            )
        )
        produced |= self._codes(
            _definition(
                actions=[{"type": "awx", "name": "run", "template_id": 1, "when": "{{ " + ("a" * 11000) + " }}"}]
            )
        )
        produced |= self._codes(
            _definition(inputs=[{"key": "user", "type": "object", "model": "users.User"}]),
            enforce_policy=True,
        )
        produced |= self._codes(
            _definition(
                inputs=[
                    {
                        "key": "device",
                        "type": "object",
                        "model": "dcim.Device",
                        "filter": {"query": {"user__password__startswith": "a"}},
                    }
                ]
            ),
            enforce_policy=True,
        )
        produced |= self._codes(
            _definition(
                inputs=[{"key": "hostname", "type": "string"}],
                schedule={"servicenow": {"change_input_key": "missing", "timezone": "Not/AZone"}},
            )
        )
        produced |= {item["code"] for item in reserved_key_errors({"actions": [], "main_actions": []})}
        produced |= {item["code"] for item in reserved_key_errors({"preserved": ["nope"]})}
        try:
            definition_from_builder(
                {
                    "key": "sample",
                    "name": "Sample",
                    "inputs": [{"key": "device", "type": "object", "model": "dcim.Device", "filter_query": ":\n  -"}],
                }
            )
        except BuilderValidationError as exc:
            produced |= {item["code"] for item in exc.errors}

        repo = GitRepository.objects.create(name="Schema Repo", remote_url="https://example.com/schema.git")
        produced |= self._codes(
            _definition(actions=[{"type": "nautobot", "name": "run", "script": "scripts/missing.py"}]),
            repo=repo,
            check_scripts=True,
        )
        produced |= self._codes(
            _definition(actions=[{"type": "nautobot", "name": "run", "script": "scripts/missing.py"}]),
            repo=None,
            check_scripts=True,
        )
        produced |= self._codes(
            _definition(actions=[{"type": "nautobot", "name": "run", "script": "filters/run.py"}]),
            enforce_policy=True,
        )

        missing = set(FIELD_ERROR_CODES) - produced
        self.assertEqual(missing, set(), msg=sorted(missing))

    def test_when_message_and_cycle_text(self):
        errors = validate(
            normalize(_definition(actions=[{"type": "awx", "name": "run", "template_id": 1, "when": "inputs.confirm"}]))
        )
        self.assertIn("{{ inputs.confirm }}", errors[0]["message"])

    def test_long_when_is_not_capped_at_200_characters(self):
        when = "{{ inputs.confirm }}" + (" " * 220)
        self.assertGreater(len(when), 200)
        errors = validate(
            normalize(_definition(actions=[{"type": "awx", "name": "run", "template_id": 1, "when": when}]))
        )
        self.assertEqual(errors, [])

    def test_schema_help_flags_and_depends_on_shape(self):
        schema = load_schema()
        depends = schema["$defs"]["input"]["properties"]["depends_on"]
        self.assertEqual(depends["type"], "array")
        self.assertTrue(depends["uniqueItems"])
        self.assertEqual(depends["x-ui"]["widget"], "multiselect")
        show_if = schema["$defs"]["input"]["properties"]["show_if"]
        self.assertIn("string", [branch.get("type") for branch in show_if["oneOf"]])
        self.assertTrue(show_if["x-ui"]["code"])
        self.assertIn("Jinja2 is allowed", show_if["x-ui"]["help"])
        labels = schema["$defs"]["validator"]["properties"]["flags"]["x-ui"]["labels"]
        self.assertEqual(labels["IGNORECASE"], "Case-insensitive")
        self.assertEqual(schema["$defs"]["validator"]["properties"]["flags"]["x-ui"]["widget"], "checkboxes")

    def test_references_include_dependencies_results_and_user(self):
        definition = normalize(
            _definition(
                inputs=[
                    {"key": "site", "type": "string"},
                    {"key": "role", "type": "string"},
                    {"key": "target", "type": "string", "depends_on": ["site", "role"], "show_if": "{{ inputs.site }}"},
                ],
                actions=[
                    {"type": "awx", "name": "first", "template_id": 1},
                    {"type": "awx", "name": "second", "template_id": 2, "when": "{{ results.first }}"},
                ],
            )
        )
        refs = find_references(definition)
        self.assertEqual([item["key"] for item in refs["inputs"]], ["site", "role", "target"])
        self.assertEqual(refs["results"][1]["order"], 2)
        self.assertIn("username", refs["user"]["attributes"])
        renamed, changes = rename_reference(definition, "rename_input", "site", "location")
        self.assertIn("location", renamed["inputs"][2]["depends_on"])
        self.assertTrue(any(change["pointer"].endswith("depends_on") for change in changes))
        self.assertIn("inputs.location", renamed["inputs"][2]["show_if"])

    def test_alias_lists_live_only_in_the_schema_package(self):
        package = Path(__file__).resolve().parents[1]
        self.assertFalse((package / "builders.py").exists())
        needles = ("main_actions", "main-actions", "pre-actions", "before_actions", "after_actions")
        offenders = []
        for path in package.rglob("*.py"):
            if "schema" in path.parts or "tests" in path.parts or "migrations" in path.parts:
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if any(f'"{needle}"' in line or f"'{needle}'" in line for needle in needles):
                    offenders.append(f"{path.name}:{stripped}")
        self.assertEqual(offenders, [])

    def test_script_lookup_uses_the_repository_row(self):
        repo = GitRepository.objects.create(name="Script Repo", remote_url="https://example.com/scripts.git")
        WorkflowHelper.objects.create(
            source_repo=repo, file_path="scripts/run.py", content="def run(context):\n    return {}\n"
        )
        errors = validate(
            normalize(_definition(actions=[{"type": "nautobot", "name": "run", "script": "scripts/run.py"}])),
            repo=repo,
            check_scripts=True,
        )
        self.assertEqual(errors, [])
