# Extending the App

This page covers repository-backed extension points for workflow authors, with a focus on custom Jinja filters.

## Repo-Backed Filters

Custom Jinja filters are defined in Python modules under the repository's top-level `filters/` directory by default. During Git sync, the app stores those modules in the database, validates them using the same import path used at runtime, and makes them available to workflow rendering.

Each filter module must define a `FILTERS` dictionary mapping the exposed filter name to a callable object.

```python
# filters/string_filters.py
from scripts.lib.text_helpers import normalize_hostname


def normalize_for_dns(value):
    """Normalize a user-provided hostname for DNS-safe usage."""
    return normalize_hostname(value).replace("_", "-")


FILTERS = {
    "normalize_for_dns": normalize_for_dns,
}
```

## Import Conventions

Shared code should live in the workflow repository rather than in the plugin codebase. When helper modules are stored under `scripts/lib`, import them with normal absolute imports that match the materialized repo package layout:

```python
from scripts.lib.text_helpers import normalize_hostname
```

This same convention applies to action scripts and filter modules. Keep examples and custom content aligned with that pattern.

## Validation Rules

Git sync validates filter modules before workflows use them. Sync fails when any of the following are true:

- The module cannot be imported.
- The module does not define a `FILTERS` dictionary.
- A `FILTERS` entry is not callable.
- A repo-backed filter name collides with a built-in filter name.
- Two repo-backed modules define the same filter name.
- The module imports a third-party dependency that is not installed in the Nautobot runtime.

Failing sync early keeps launch-time rendering and workflow execution predictable.

## Repo-Local Dependencies

Repo-local helper modules are supported because the app materializes both helper modules and filter modules into one importable package. This is the expected migration path for filters that currently depend on plugin-local support code.

## Contributing New Extension Points

Extending the application itself is welcome, but it is best to open an issue first so the design and maintenance impact can be discussed before a pull request is started.
