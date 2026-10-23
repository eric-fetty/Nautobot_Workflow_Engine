/**Labels and help text come from the workflow schema. */

const FALLBACK = {
    key: ["Workflow ID", "Used in URLs and the Git file name. YAML key: key."],
    name: ["Name", "Shown on the launch page. YAML key: name."],
    description: ["Description", "One or two sentences shown to people launching it. YAML key: description."],
    category: ["Category", "Groups workflows on the launch page. YAML key: category."],
    approval_required: ["Require approval before running", "YAML key: approval_required."],
    manual_duration_minutes: ["Time saved per run (minutes)", "Used by the Dashboard. YAML key: manual_duration_minutes."],
    "input.key": ["Variable name", "Use it as {{ inputs.<name> }}. YAML key: key."],
    "input.label": ["Label", "Text shown on the launch form. YAML key: label."],
    "input.type": ["Field type", "YAML key: type."],
    "input.required": ["Required", "YAML key: required."],
    "input.default": ["Default value", "YAML key: default."],
    "input.help_text": ["Help text", "Shown under the field on the launch form. YAML key: help_text."],
    "input.placeholder": ["Placeholder", "Grey hint inside the empty field. YAML key: placeholder."],
    "input.depends_on": ["Wait for these fields", "YAML key: depends_on."],
    "input.show_if": ["Show only when", "Jinja2 is allowed. YAML key: show_if."],
    "input.validators": ["Validation rules", "YAML key: validators."],
    "input.model": ["Object type", "YAML key: model."],
    "input.filter": ["Only show objects where", "Jinja2 is allowed. YAML key: filter.query."],
    "input.choices": ["Options", "Jinja2 is allowed. YAML key: choices."],
    "action.name": ["Step name", "Results are available as {{ results.<name> }}. YAML key: name."],
    "action.when": ["Run only if", "Jinja2 is allowed. YAML key: when."],
    "action.script": ["Script", "YAML key: script."],
    "action.with": ["Script parameters", "Jinja2 is allowed. YAML key: with."],
    "action.template_id": ["Job template", "The numeric ID from AWX. YAML key: template_id."],
    "action.extra_vars": ["Extra variables", "Jinja2 is allowed. YAML key: extra_vars."],
    "action.monitor": ["Wait for the AWX job to finish", "YAML key: monitor."],
    schedule: ["ServiceNow change window", "YAML key: schedule.servicenow."],
};

export function createLabels(schema) {
    const defs = (schema && schema.$defs) || {};
    const props = (schema && schema.properties) || {};

    function ui(node) {
        return (node && node["x-ui"]) || {};
    }

    function pair(node, fallbackKey) {
        const meta = ui(node);
        const fallback = FALLBACK[fallbackKey] || ["", ""];
        return {
            label: meta.label || fallback[0],
            help: meta.help || fallback[1],
            labels: meta.labels || {},
        };
    }

    const input = (defs.input && defs.input.properties) || {};
    const action = (defs.action && defs.action.properties) || {};
    return {
        workflow: {
            key: pair(props.key, "key"),
            name: pair(props.name, "name"),
            description: pair(props.description, "description"),
            category: pair(props.category, "category"),
            approval_required: pair(props.approval_required, "approval_required"),
            manual_duration_minutes: pair(props.manual_duration_minutes, "manual_duration_minutes"),
        },
        phases: {
            pre: pair(props.pre_actions, "pre"),
            main: pair(props.actions, "main"),
            post: pair(props.post_actions, "post"),
        },
        input: {
            key: pair(input.key, "input.key"),
            label: pair(input.label, "input.label"),
            type: pair(input.type, "input.type"),
            required: pair(input.required, "input.required"),
            default: pair(input.default, "input.default"),
            help_text: pair(input.help_text, "input.help_text"),
            placeholder: pair(input.placeholder, "input.placeholder"),
            depends_on: pair(input.depends_on, "input.depends_on"),
            show_if: pair(input.show_if, "input.show_if"),
            validators: pair(input.validators, "input.validators"),
            model: pair(input.model, "input.model"),
            filter: pair((input.filter && input.filter.properties && input.filter.properties.query) || input.filter, "input.filter"),
            choices: pair(input.choices, "input.choices"),
        },
        action: {
            type: pair(action.type, "action.type"),
            name: pair(action.name, "action.name"),
            when: pair(action.when, "action.when"),
            script: pair(action.script, "action.script"),
            with: pair(action.with, "action.with"),
            template_id: pair(action.template_id, "action.template_id"),
            extra_vars: pair(action.extra_vars, "action.extra_vars"),
            monitor: pair(action.monitor, "action.monitor"),
        },
        schedule: pair(props.schedule, "schedule"),
        flags: ((defs.validator && defs.validator.properties && defs.validator.properties.flags) || {})["x-ui"] || {},
    };
}

export const TYPE_ORDER = ["string", "integer", "boolean", "choice", "multichoice", "object", "multiobject", "list", "listdict"];
