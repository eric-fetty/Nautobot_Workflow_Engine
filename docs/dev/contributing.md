# Contributing to the App

The project is packaged with a light [development environment](dev_environment.md) based on `docker-compose` to help with the local development of the project and to run tests.

The project uses the following checks:

- Python linting and formatting: `pylint` and `ruff`.
- YAML linting is done with `yamllint`.
- Django unit test to ensure the app is working properly.
- Django Template linting: `djlint`
- Django Template formatting: `djhtml`

Documentation is built using [mkdocs](https://www.mkdocs.org/). The [Docker based development environment](dev_environment.md#docker-development-environment) automatically starts a container hosting a live version of the documentation website on [http://localhost:8001](http://localhost:8001) that auto-refreshes when you make any changes to your local files.

## Creating Changelog Fragments

Include a changelog fragment for user-visible changes in the `changes` directory.
Use the associated issue number and fragment type as the filename, for example
`2362.added`. Valid types are `added`, `changed`, `deprecated`, `fixed`, `removed`,
and `security`. Write complete sentences in the past tense, one entry per line.
Use separate files for changes in different categories. Do not include private
identifiers, credentials, or unredacted operational data in fragments.

!!! example

    **Wrong**
    ```plaintext title="changes/1234.fixed"
    fix critical bug in documentation
    ```

    **Right**
    ```plaintext title="changes/1234.fixed"
    Fixed critical bug in documentation.
    ```

!!! example "Multiple Entry Example"

    This will generate 2 entries in the `fixed` category and one entry in the `changed` category.

    ```plaintext title="changes/1234.fixed"
    Fixed critical bug in documentation.
    Fixed release notes generation.
    ```

    ```plaintext title="changes/1234.changed"
    Changed release notes generation.
    ```

## Change Scope and Tests

Keep changes focused and include a regression test for behavioral fixes. Reuse the
existing executor, action-ordering, form, filter, and scheduling tests. Preserve
database migrations and public workflow contracts, including intentional execution
logging. Update examples and documentation when behavior changes.

Use synthetic fixtures only. Do not contribute environment files, database dumps,
real inventory exports, credentials, or generated distribution archives. See
[security and data handling](../admin/security.md) for diagnostic-sharing guidance.

Test and lint through the project's existing Docker-based development tasks. Include
which checks ran and any environment limitations in the change description. Older
release support and backports require an explicit maintainer decision.

## Release Policy

There is no fixed release schedule. Do not assume a maintenance commitment for older
versions beyond what is explicitly documented for a release.

The steps taken by maintainers when creating a new release are documented in the [release checklist](./release_checklist.md).
