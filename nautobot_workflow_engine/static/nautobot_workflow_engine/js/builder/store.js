/**Builder payload state. Edits mutate the payload; the page re-renders on structure changes. */

export function slugify(text) {
    return String(text || "")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 50);
}

/** Turn a validate error into the data-bind path the page uses. */
export function toBindPath(item) {
    const pointer = (item && item.pointer) || "";
    if (pointer) {
        const parts = pointer
            .split("/")
            .slice(1)
            .map((part) => part.replace(/~1/g, "/").replace(/~0/g, "~"));
        if (parts.length && parts[0]) {
            if (parts[0] === "repository" || parts[0] === "source_repo") {
                return "source_repo";
            }
            if (parts[0] === "inputs" && parts[2] === "filter" && parts[3] === "query") {
                return `inputs.${parts[1]}.filter_query`;
            }
            if (parts[0] === "pre_actions") {
                return actionBind("pre", parts);
            }
            if (parts[0] === "post_actions") {
                return actionBind("post", parts);
            }
            if (parts[0] === "actions") {
                return actionBind("main", parts);
            }
            if (parts[0] === "schedule") {
                const rest = parts[1] === "servicenow" ? parts.slice(2) : parts.slice(1);
                return rest.length ? `schedule.${rest.join(".")}` : "schedule.enabled";
            }
            return parts.join(".");
        }
    }
    const path = String((item && item.path) || "").replace(/\[(\d+)\]/g, ".$1");
    if (path === "repository") {
        return "source_repo";
    }
    return path;
}

function actionBind(phase, parts) {
    const rest = parts.slice(2);
    return [`actions.${phase}.${parts[1] || "0"}`, ...rest].join(".");
}

export function createStore(payload) {
    const state = {
        payload: payload || emptyPayload(),
        keyTouched: Boolean(payload && payload.key),
        expandedInput: 0,
        expandedStep: { phase: "main", index: 0 },
    };
    return state;
}

export function emptyPayload() {
    return {
        key: "",
        name: "",
        description: "",
        category: "",
        enabled: false,
        approval_required: false,
        manual_duration_minutes: "",
        source_repo: "",
        schedule: {
            enabled: false,
            integration_name: "",
            change_input_key: "",
            allowed_states: "",
            timezone: "",
            start_fields: "",
            end_fields: "",
            ticket_regex: "",
            require_approved: false,
        },
        inputs: [],
        actions: { pre: [], main: [], post: [] },
    };
}

export function setPath(payload, path, value) {
    const parts = String(path).split(".");
    let cursor = payload;
    for (let index = 0; index < parts.length - 1; index += 1) {
        const key = numeric(parts[index]);
        if (cursor[key] == null) {
            cursor[key] = /^\d+$/.test(parts[index + 1]) ? [] : {};
        }
        cursor = cursor[key];
    }
    cursor[numeric(parts[parts.length - 1])] = value;
}

function numeric(part) {
    return /^\d+$/.test(part) ? Number(part) : part;
}

export function blankInput(type) {
    const input = {
        key: "",
        label: "",
        type,
        required: false,
        default: "",
        help_text: "",
        depends_on: [],
        show_if: "",
        validators: [],
        choices: [],
        choices_template: "",
        model: "",
        filter_query: "",
        columns: [],
    };
    if (type === "choice" || type === "multichoice") {
        input.choices = [
            { value: "", label: "" },
            { value: "", label: "" },
        ];
    }
    if (type === "listdict") {
        input.columns = [{ key: "column", label: "Column", type: "string", required: false }];
    }
    return input;
}

export function blankAction(type, phase) {
    return {
        type,
        name: "",
        when: "",
        script: type === "nautobot" ? "scripts/notify.py" : "",
        with: {},
        template_id: type === "awx" ? "" : "",
        extra_vars: {},
        monitor: type === "awx",
        phase,
    };
}

export function referencesTo(payload, key) {
    const hits = [];
    payload.inputs.forEach((input, index) => {
        if (input === undefined) {
            return;
        }
        if ((input.depends_on || []).includes(key) && input.key !== key) {
            hits.push(`Inputs › ${input.label || input.key || index + 1} › Wait for these fields`);
        }
        if (mentions(input.show_if, key)) {
            hits.push(`Inputs › ${input.label || input.key || index + 1} › Show only when`);
        }
        if (mentions(input.filter_query, key) || mentions(input.choices_template, key)) {
            hits.push(`Inputs › ${input.label || input.key || index + 1}`);
        }
    });
    if (mentions(payload.schedule && payload.schedule.change_input_key, key)) {
        hits.push("ServiceNow change window › Change ticket field");
    }
    ["pre", "main", "post"].forEach((phase) => {
        (payload.actions[phase] || []).forEach((action) => {
            if (mentions(action.when, key) || mentions(action.with, key) || mentions(action.extra_vars, key)) {
                hits.push(`${phaseLabel(phase)} › ${action.name || "step"}`);
            }
        });
    });
    return hits;
}

export function mentions(value, key) {
    if (!key) {
        return false;
    }
    const text = typeof value === "string" ? value : JSON.stringify(value || "");
    return text.includes(`inputs.${key}`) || text.includes(`inputs['${key}']`) || text === key;
}

export function phaseLabel(phase) {
    return { pre: "Before", main: "Main", post: "Always after" }[phase] || phase;
}

export function fromDefinition(definition, previous) {
    const payload = emptyPayload();
    const data = definition || {};
    payload.key = data.key || "";
    payload.name = data.name || "";
    payload.description = data.description || "";
    payload.category = data.category || "";
    payload.approval_required = Boolean(data.approval_required);
    payload.manual_duration_minutes = data.manual_duration_minutes == null ? "" : data.manual_duration_minutes;
    payload.enabled = Boolean(previous && previous.enabled);
    payload.source_repo = (previous && previous.source_repo) || "";
    payload.inputs = (data.inputs || []).filter((item) => item && typeof item === "object").map(inputFromDefinition);
    payload.actions = {
        pre: actionsFrom(data.pre_actions),
        main: actionsFrom(data.actions),
        post: actionsFrom(data.post_actions),
    };
    const snow = (data.schedule && data.schedule.servicenow) || {};
    payload.schedule.enabled = Boolean(data.schedule && data.schedule.servicenow);
    payload.schedule.integration_name = snow.integration_name || "";
    payload.schedule.change_input_key = snow.change_input_key || "";
    payload.schedule.allowed_states = Array.isArray(snow.allowed_states) ? snow.allowed_states.join(", ") : "";
    payload.schedule.timezone = snow.timezone || "";
    payload.schedule.start_fields = Array.isArray(snow.start_fields) ? snow.start_fields.join(", ") : "";
    payload.schedule.end_fields = Array.isArray(snow.end_fields) ? snow.end_fields.join(", ") : "";
    payload.schedule.ticket_regex = snow.ticket_regex || "";
    payload.schedule.require_approved = Boolean(snow.require_approved);
    return payload;
}

function inputFromDefinition(item) {
    const input = blankInput(item.type || "string");
    input.key = item.key || "";
    input.label = item.label || "";
    input.required = Boolean(item.required);
    input.default = item.default == null ? "" : item.default;
    input.help_text = item.help_text || "";
    input.depends_on = Array.isArray(item.depends_on) ? item.depends_on.slice() : [];
    input.show_if = item.show_if == null ? "" : item.show_if;
    input.validators = Array.isArray(item.validators) ? item.validators.map((rule) => ({ ...rule })) : [];
    input.model = item.model || "";
    if (typeof item.choices === "string") {
        input.choices_template = item.choices;
        input.choices = [];
    } else if (Array.isArray(item.choices)) {
        input.choices = item.choices.map((choice) => ({
            value: choice && typeof choice === "object" ? choice.value : choice,
            label: choice && typeof choice === "object" ? choice.label || choice.value : String(choice),
        }));
    }
    const query = item.filter && item.filter.query;
    input.filter_query = query == null ? "" : query;
    input.columns = Array.isArray(item.columns) ? item.columns.map((column) => ({ ...column })) : [];
    return input;
}

function actionsFrom(items) {
    return (items || []).filter((item) => item && typeof item === "object").map((item) => ({
        type: item.type || "nautobot",
        name: item.name || "",
        when: item.when || "",
        script: item.script || "",
        with: item.with && typeof item.with === "object" ? { ...item.with } : {},
        template_id: item.template_id == null ? "" : item.template_id,
        extra_vars: item.extra_vars && typeof item.extra_vars === "object" ? { ...item.extra_vars } : {},
        monitor: Boolean(item.monitor),
    }));
}

export function fingerprint(payload) {
    return JSON.stringify(payload);
}
