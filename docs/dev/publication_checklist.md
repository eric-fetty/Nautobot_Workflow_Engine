# Publication Checklist

This checklist covers the reviewed source snapshot and its built artifacts. It does
not cover repository hosting, history, or publication operations.

## Current Status

**Not ready for public release.** Source cleanup has started, but artifact rebuilding,
full content review, runtime validation, and owner approvals are outstanding.

Completed source changes:

- Removed known private contact addresses and destination links from package metadata,
  documentation, configuration schema, and release-note generation.
- Replaced placeholder onboarding/admin pages and organized documentation by reader task.
- Added public contribution guidance and documented intended execution logging.
- Removed obsolete project-generation metadata and an empty API reference page.
- Added explicit package/Docker exclusions for local data and generated outputs.
- Made additional development apps opt-in without pruning dependencies or changing the lockfile.

The filename-only scan used a small set of known private identifiers and selected key
signatures. It is not a complete credential, personal-data, or proprietary-content
audit. Absence of a match is not a safety guarantee.

## Source Export Review

Do not copy the entire working directory. Assemble the public snapshot from reviewed
source files and inspect every included file, including hidden files and assets.

| Material | Disposition |
| --- | --- |
| Application Python, migrations, templates, and non-generated static assets | Keep; finish content/provenance review. |
| Tests and examples | Keep useful cases; review fixtures, service assumptions, and identifiers. |
| Documentation sources and theme assets | Keep sanitized sources; review images and asset provenance. |
| Build tooling, dependency metadata, lockfile, safe development templates | Keep; validate from a clean environment. |
| Real environment files, credentials, database dumps/backups, run logs | Retain privately; never include in the export. |
| Local resolved compose/invoke configuration and editor workspace settings | Exclude; use reviewed portable templates instead. |
| Caches, editor metrics, temporary plans, old distributions | Exclude. |
| Previously generated app documentation and search indexes | Do not export as-is; rebuild from sanitized sources. |
| Hosting-specific configuration and policies | Owner-managed, outside this cleanup's review scope. |

Bootstrap metadata was removed because it contained private generation answers.
Automatic template synchronization is no longer configured by those files; the
existing application and development source are retained.

## Required Gates

- [x] Apache-2.0 license and matching package metadata are in place; retain required notices
  and finish any remaining third-party provenance review.
- [ ] Establish a private vulnerability-reporting route and update the security policy.
- [ ] Review retained author attribution for public release without removing legally
  required notices.
- [ ] Audit all prospective export content, including examples, fixtures, screenshots,
  binary assets, configuration templates, private package sources, and attachment metadata.
- [ ] Complete review of independently identified runtime access/disclosure concerns.
  Intentional script execution logging is not a finding and must remain unchanged.
- [ ] Validate additional-plugin opt-in behavior and schema generation in the approved
  development environment.
- [ ] Complete existing regression tests and relevant Python, template, YAML, and
  Markdown checks using the established development workflow.
- [ ] Build documentation strictly, verify links/navigation, and replace stale bundled
  HTML and search indexes. Existing generated files still contain private references.
- [ ] Build fresh wheel and source distributions; inspect extracted contents and package
  metadata. Explicit includes can override exclusions, so inspect actual archives.
- [ ] Review the Docker build context and resulting image contents independently.
- [ ] Scan the exact final export and rebuilt artifacts with an approved local secret
  scanner and a private-identifier review; keep raw findings outside publishable content.
- [ ] Resolve all high-risk findings and document narrowly justified false positives.

Tests and builds have not been run from the host editor environment. Editor diagnostics
do not substitute for the Docker-based checks. No existing archives or generated docs
should be treated as sanitized by these source changes.