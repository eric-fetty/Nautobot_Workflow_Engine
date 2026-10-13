# Multiobject Input Type

This directory contains an example workflow demonstrating the **multiobject** input type, which allows users to select multiple objects from a Nautobot model.

## Overview

The `multiobject` input type is similar to the `object` input type, but allows selecting multiple objects using checkboxes instead of a single selection. This is useful for bulk operations that need to be performed on multiple resources.

## Input Types Comparison

| Input Type | Description | Selection | Example Use Case |
|------------|-------------|-----------|------------------|
| `object` | Single object selection | Radio/dropdown | Select one device |
| `multiobject` | Multiple object selection | Checkboxes | Select multiple devices for bulk update |
| `choice` | Single choice from static list | Radio/dropdown | Select one status |
| `multichoice` | Multiple choices from static list | Checkboxes | Select multiple tags |

## Example Workflow

The `bulk-device-operations.yaml` workflow demonstrates:

- **multiobject** for selecting multiple devices
- **multichoice** for selecting multiple tags
- **object** for selecting single objects (site, tenant)
- **choice** for selecting from predefined options
- Conditional field visibility using `show_if`
- Field dependencies using `depends_on`

## Key Features

### Multiobject Field Definition

```yaml
- key: devices
  label: Devices
  type: multiobject
  model: dcim.Device
  required: true
  depends_on: ["site"]
  filter:
    query: {"site_id": "{{ inputs.site.id }}"}
```

### In Python Scripts

The multiobject input is received as a **list of object IDs**:

```python
def run(*, devices, **kwargs):
    # devices is a list of IDs: [1, 2, 3, ...]
    device_objects = Device.objects.filter(id__in=devices)
    
    for device in device_objects:
        # Perform operation on each device
        print(f"Processing {device.name}")
```

### Multichoice vs Multiobject

- **multichoice**: Static list of values defined in YAML
  - Returns: List of string values
  - Example: `["production", "staging"]`

- **multiobject**: Dynamic list from Nautobot models
  - Returns: List of object IDs (integers)
  - Example: `[1, 2, 3]` (device IDs)

## Usage Tips

1. **Always validate input**: Check that the list is not empty
2. **Use bulk operations**: Leverage Django's `filter(id__in=...)` for efficiency
3. **Provide feedback**: Print status for each processed item
4. **Handle errors gracefully**: Some objects in the list might fail operations
5. **Use with dependencies**: Combine with `depends_on` to filter options dynamically

## Example Output

When the workflow runs successfully:

```
🔧 Performing 'add_tags' on 5 device(s) in DC1
  ✅ Added 2 tag(s) to device-1
  ✅ Added 2 tag(s) to device-2
  ✅ Added 2 tag(s) to device-3
  ✅ Added 2 tag(s) to device-4
  ✅ Added 2 tag(s) to device-5

✅ Bulk operation completed successfully on 5 device(s)
```

## Files

- `bulk-device-operations.yaml` - Example workflow definition
- `bulk_device_operations.py` - Example Python script showing how to process multiobject inputs
