# Security and Data Handling

## Trust Boundary

Workflow scripts, helper modules, and custom Jinja filters execute Python in the
Nautobot environment. Scripts and filters are trusted code that was reviewed in Git.
Jinja templates in `when`, `with`, `extra_vars`, choice lists, and filter queries
render in a sandbox that blocks private attributes, secret-bearing models, and
methods that change data. Restrict who can author templates and who can publish,
and review Git changes before use.
Individual actions may have access beyond the permissions of the person launching
the workflow, depending on the script and integration credentials.

The builder never executes anything. **Preview run** renders templates in the sandbox
and does not start scripts, call AWX, or write runs, jobs or object changes. Script
parameters are read from the file's `PARAMETERS` dict without importing it. Scripts
written in the builder are only parsed, never imported, until a workflow step runs them.
They are starters that print the values they receive. Grant **Write scripts** only to
people whose code you are happy to have in the repository, and review changes there.

Publishing uses the repository's HTTP(S) token from its secrets group. The token is
removed from every message, log line and stored Git error.

## Inputs, Results, and Logs

Workflow runs retain submitted inputs, results, execution diagnostics, and script
output. Detailed script execution logging is an intentional feature. Treat these
records, exports, screenshots, and database backups as operational data.

Use Nautobot object permissions to limit workflow and run access; see
[Granting Permissions](install.md#permissions) for how to scope access by name,
category, or entirely, and how the reporting dashboard respects the same object
permissions. Check the actual visibility with representative operator accounts
before allowing sensitive workflows. Do not assume that permission to launch a
workflow also limits what its script can do.

Avoid collecting credentials through ordinary workflow inputs. Retrieve integration
credentials through Nautobot Secrets Groups or your approved secrets provider, and
do not print secrets from scripts. Review retention and backup access under your
organization's policies; do not assume runs are automatically purged.

## External Integrations

Use HTTPS with certificate verification for external services. Grant integration
accounts only the privileges their workflows need. Store credentials outside
workflow definitions, examples, and application source. See the
[integration guide](../user/external_interactions.md).

## Sharing Diagnostics

Reproduce problems with synthetic inputs whenever possible. Before sharing logs,
screenshots, archives, or workflow definitions, remove credentials, internal service
addresses, personal data, and real inventory or ticket identifiers. Do not attach a
production database dump to a support request.

## Reporting Vulnerabilities

Do not post exploit details or sensitive diagnostics in public issues. A private
reporting contact has not yet been designated for the public project. Until that
route is established, report concerns through your deployment's internal security
process. A working private reporting route is required before public release.