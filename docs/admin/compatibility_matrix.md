# Compatibility Matrix

The current package declares the following requirements:

| Workflow Engine | Nautobot | Python |
| --- | --- | --- |
| 1.0.0 | `>=3.0.0,<4.0.0` | `>=3.10,<3.14` |

These are dependency constraints, not a claim that every combination has been tested.
Choose a Python release supported by the selected Nautobot version as well. The
development image defaults to Nautobot 3.0.0 and Python 3.12.

The open-source release series starts at 1.0.0. The version reset does not change
the Nautobot requirements or reset database migrations. Do not infer compatibility
from the app's major version alone; this source tree does not support Nautobot 2.x.

Review [upgrade notes](upgrade.md) and validate your workflows before updating an
existing deployment. PostgreSQL and MySQL development configurations are provided;
database support also depends on the selected Nautobot release.
