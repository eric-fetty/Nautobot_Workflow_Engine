# Security and Data Handling

## Trust Boundary

Workflow scripts, helper modules, and custom Jinja filters execute Python in the
Nautobot environment. They are trusted code, not sandboxed user content. Restrict
who can modify and synchronize workflow sources, and review changes before use.
Individual actions may have access beyond the permissions of the person launching
the workflow, depending on the script and integration credentials.

## Inputs, Results, and Logs

Workflow runs retain submitted inputs, results, execution diagnostics, and script
output. Detailed script execution logging is an intentional feature. Treat these
records, exports, screenshots, and database backups as operational data.

Use Nautobot object permissions to limit workflow and run access. Check the actual
visibility with representative operator accounts before allowing sensitive workflows.
Do not assume that permission to launch a workflow also limits what its script can do.

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