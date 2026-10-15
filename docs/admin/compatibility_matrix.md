# Compatibility Matrix

The current package declares the following requirements:

| Workflow Engine | Nautobot | Python |
| --- | --- | --- |
| 1.0.0 | `>=3.0.0,<4.0.0` | `>=3.10,<3.14` |

These are dependency constraints, not a claim that every combination has been tested.
Choose a Python release supported by the selected Nautobot version as well. The
development image defaults to Nautobot 3.0.0 and Python 3.12; `invoke.yml` may pin a
newer release for local work (for example 3.2.x).

The open-source release series starts at 1.0.0. The version reset does not change
the Nautobot requirements or reset database migrations. Do not infer compatibility
from the app's major version alone; this source tree does not support Nautobot 2.x.

### Optional approval integration

YAML `approval_required` and automatic Approval Workflow Definition sync require
**Nautobot 3.2 or later**. On Nautobot 3.0–3.1 the flag is accepted in YAML and stored
on the workflow model, but definition sync and the delayed-immediate approval launch
path are skipped.

Review [upgrade notes](upgrade.md) and validate your workflows before updating an
existing deployment. PostgreSQL and MySQL development configurations are provided;
database support also depends on the selected Nautobot release.
