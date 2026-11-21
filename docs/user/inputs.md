# Inputs

Inputs are the fields someone fills in on the launch form. Open **Inputs** in the builder and choose **Add input**.

![Inputs step with an object field and its filter open, and the Launch form preview](../assets/images/builder/03-inputs.png)

## Field types

| Type | Shown as | Value passed to steps |
| --- | --- | --- |
| Text | A text box. | The text. |
| Whole number | A number box that rejects decimals. Optional minimum and maximum. | A number. |
| Yes/no | A switch. | `true` or `false`. |
| Pick one | Option buttons or a list. | The option's value. |
| Pick many | A list where several options can be ticked. | A list of values. |
| Nautobot object | A searchable picker, for example a device. | The object. Use `.id`, `.name` and so on in steps. |
| Several Nautobot objects | A searchable picker that takes several. | A list of objects. |
| List of text | One line per item. Optional at least / at most. | A list of text. |
| Table | An editable grid with columns you define. | A list of rows. |

Each input row has a drag grip, a type icon, tags that sum up its settings, **Move up**, **Move down**, **Duplicate**, **Delete**, and a chevron that opens it. **Delete** can be undone from the notice that appears.

## Common settings

- **Label**: shown beside the field.
- **Required**: the form can't be sent without it.
- **Help text**: shown under the field.
- **Placeholder**: a grey hint inside an empty field.
- **Default**: filled in when the form opens.
- **Validation**: patterns the value must match, and the message people see when it doesn't. See [Validation rules](#validation-rules).
- **Advanced · variable name**: how steps refer to the value, as `{{ inputs.<name> }}`. It is made from the label and can't change after the first publish.

## Nautobot object fields

Choose **Which kind of object?**, for example *Device*. **Only offer devices where…** narrows the list with WHERE and AND rows: a field, a plain-word operator such as *is* or *contains*, and either a fixed value or a value from another input. The builder shows how many objects match right now, for example *6 devices match with Location = AMS01*.

When a filter uses another input, the field waits for it. On the launch form it says *Choose Location first.* until that field has a value. **Switch to advanced (Jinja)** is there for filters the rows can't express.

## Show only when

**Show only when** hides a field until another field has a certain value, for example *Confirm power cycle* only when *Reboot type* is *Hard power cycle*. A hidden field is not sent with the form.

## Validation rules

Text inputs, list-of-text inputs, and text columns in a table have a **Validation** panel. Each rule has:

- **Pattern**: a regular expression the value must match. Start it with `^` and end it with `$` to match the whole value.
- **Error text**: what people see under the field when the value doesn't match.
- **Case-insensitive**, **Multi-line**, and **Dot matches newline**: optional flags.

A value has to match every rule. The launch form shows the error text of the first rule it fails. On a list input every line is checked and the message names the line, for example *Line 2: Enter an IPv4 address, like 10.0.0.1.* In a table, every cell in the column is checked and the message names the row.

**Insert preset** adds a ready-made rule that you can still edit afterwards:

| Preset | Accepts, for example |
| --- | --- |
| Hostname | `core-sw01`, `rtr1.dc1.example.com` |
| IPv4 address | `10.0.0.1` |
| IPv4 network (CIDR) | `10.20.0.0/24` |
| MAC address | `00:1A:2B:3C:4D:5E`, `00-1a-2b-3c-4d-5e`, `001a.2b3c.4d5e` |
| Interface name | `GigabitEthernet1/0/1`, `Ethernet1/1.100`, `xe-0/0/0`, `Port-channel10` |
| VLAN name | `USERS`, `voice_vlan`, `mgmt-10` (1 to 32 characters) |
| ServiceNow change number | `CHG0012345` |

The patterns are listed in the [schema reference](workflow_schema.md#validation-presets).

**Try a value** checks a sample against the rules as you type. It shows whether the launch form would accept it, the message people would see, and which rules pass or fail. Nothing is saved.

![Interface column with a preset rule and a custom rule, and Try a value rejecting Port-channel10](../assets/images/builder/21-validation-table-column.png)

A pattern that can't be read is outlined in red with the reason, and it counts as an issue, so **Publish** stays off until it is fixed. The builder checks the pattern in the browser as you type, and the server checks it again with Python when the draft saves. Some syntax works in only one of them, for example the named group `(?<site>...)`. A rule with an empty pattern is also an issue, and it is left out of the YAML.

On the launch form the browser checks each value before the form is sent, and the server checks it again when the run is submitted:

![Launch form with a hostname, a change number, a DNS line, and a table row rejected by their rules](../assets/images/launch/08-validation-errors.png)

## Tables and CSV import

A table collects rows, for example a list of VLANs. In the builder, add columns. Each column has a key, a label, a type (text, whole number, decimal number, yes/no, or pick one), and optional *required*, *unique*, minimum, and maximum settings. A text column also has its own [validation rules](#validation-rules). **At least** and **At most** set the number of rows.

On the launch form the table is an editable grid:

![Table input on the launch form with Add row, Download template and Import CSV](../assets/images/launch/03-table-input.png)

- **Add row** adds an empty row. The **×** at the end of a row removes it.
- **Download template** saves a CSV file whose headings match the columns.
- **Import CSV** reads a CSV file into the grid. You can also paste rows copied from a spreadsheet.
- If the table already has rows, import asks whether to **Replace** them or **Add** to them.

![A CSV import with the rows that need fixing marked in red](../assets/images/launch/04-csv-imported.png)

After an import, a notice says how many rows came in and lists the rows to fix. Each problem is shown under its row and the cell is outlined in red.

CSV rules:

- Headings are matched to a column's key first, then its label.
- A pick-one column accepts the option's value or its label.
- A yes/no column accepts `true`/`false`, `yes`/`no`, `on`/`off` and `1`/`0`.
- Rows beyond **At most** are skipped, and the notice says so. Files over 2 MB are refused.
- A cell that starts with `=`, `+`, `-` or `@` is flagged, because spreadsheet apps would treat it as a formula. A negative number in a number column is fine.
- The file is read in your browser. It is not uploaded or stored. Only the rows in the grid are sent with the form.

To hide **Import CSV** and **Download template** for one table, turn off **Allow CSV import** for that input.

## Referring to inputs

Steps use inputs as `{{ inputs.<name> }}`, for example `{{ inputs.device.name }}`, `{{ inputs.change_ticket }}`, or `{{ inputs.vlans }}` for a whole table. The [steps page](steps.md#parameters) explains how to wire them without typing any of this.
