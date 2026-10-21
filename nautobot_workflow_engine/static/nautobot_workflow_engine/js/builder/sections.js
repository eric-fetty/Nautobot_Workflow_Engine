/**Details, Inputs, Steps, and Review. */

import { field, h } from "./dom.js";
import { jinjaEditor, referenceList } from "./jinja.js";
import { TYPE_ORDER } from "./schema.js";
import { phaseLabel } from "./store.js";

const PHASES = [
    ["pre", "Before"],
    ["main", "Main"],
    ["post", "Always after"],
];

export function renderRail(labels, payload, section, errors) {
    const counts = {
        details: errors.filter((item) => item.section === "details").length,
        inputs: errors.filter((item) => item.section === "inputs").length,
        steps: errors.filter((item) => item.section === "steps").length,
        review: errors.filter((item) => item.section === "review").length,
    };
    const items = [
        ["details", "Details", ""],
        ["inputs", `Inputs (${payload.inputs.length})`, counts.inputs ? `${counts.inputs} problems` : ""],
        ["steps", "Steps", stepSummary(payload)],
        ["review", "Review & Publish", counts.review ? `${counts.review} problems` : ""],
    ];
    return h(
        "ul",
        { class: "nav nav-pills flex-column gap-1" },
        items.map(([id, text, note]) => {
            const link = h(
                "button",
                {
                    type: "button",
                    class: `nav-link text-start w-100 ${section === id ? "active" : ""}`,
                    "data-action": "goto",
                    "data-section": id,
                    "aria-current": section === id ? "page" : undefined,
                },
                text
            );
            if (note) {
                link.append(h("span", { class: "d-block small", text: note }));
            }
            if (id === "steps") {
                return h(
                    "li",
                    { class: "nav-item" },
                    link,
                    h(
                        "ul",
                        { class: "list-unstyled small ms-3 mb-2" },
                        PHASES.map(([phase, title]) =>
                            h("li", { text: `${title}: ${(payload.actions[phase] || []).length}` })
                        )
                    )
                );
            }
            return h("li", { class: "nav-item" }, link);
        })
    );
}

function stepSummary(payload) {
    return PHASES.map(([phase, title]) => `${title} ${(payload.actions[phase] || []).length}`).join(" · ");
}

export function renderSection(section, context) {
    if (section === "inputs") {
        return renderInputs(context);
    }
    if (section === "steps") {
        return renderSteps(context);
    }
    if (section === "review") {
        return renderReview(context);
    }
    return renderDetails(context);
}

function renderDetails({ labels, payload, config, errorFor, readOnly }) {
    const lockKey = Boolean(config.published_number);
    return h(
        "div",
        {},
        h("h2", { class: "h4", text: "Details" }),
        field(
            labels.workflow.name.label,
            labels.workflow.name.help,
            h("input", {
                class: "form-control",
                id: "field-name",
                "data-bind": "name",
                value: payload.name,
                disabled: readOnly || undefined,
            }),
            { id: "field-name", error: errorFor("name"), required: true }
        ),
        field(
            labels.workflow.key.label,
            labels.workflow.key.help,
            h("input", {
                class: "form-control",
                id: "field-key",
                "data-bind": "key",
                value: payload.key,
                readonly: lockKey || undefined,
                disabled: readOnly || undefined,
            }),
            { id: "field-key", error: errorFor("key"), required: true }
        ),
        lockKey ? h("p", { class: "form-text", text: "Duplicate this workflow to use a different ID." }) : null,
        field(
            labels.workflow.description.label,
            labels.workflow.description.help,
            h("textarea", {
                class: "form-control",
                id: "field-description",
                rows: "3",
                "data-bind": "description",
                value: payload.description,
                disabled: readOnly || undefined,
            }),
            { id: "field-description" }
        ),
        field(
            labels.workflow.category.label,
            labels.workflow.category.help,
            h("input", {
                class: "form-control",
                id: "field-category",
                "data-bind": "category",
                value: payload.category,
                list: "category-list",
                disabled: readOnly || undefined,
            }),
            { id: "field-category" }
        ),
        h(
            "datalist",
            { id: "category-list" },
            (config.categories || []).map((item) => h("option", { value: item }))
        ),
        field(
            labels.workflow.manual_duration_minutes.label,
            labels.workflow.manual_duration_minutes.help,
            h("input", {
                class: "form-control",
                id: "field-minutes",
                type: "number",
                inputmode: "numeric",
                min: "0",
                "data-bind": "manual_duration_minutes",
                value: payload.manual_duration_minutes,
                disabled: readOnly || undefined,
            }),
            { id: "field-minutes" }
        ),
        field(
            "Git repository",
            "Where scripts come from and where the published YAML is saved. YAML key: source_repo.",
            h(
                "select",
                {
                    class: "form-select",
                    id: "field-repo",
                    "data-bind": "source_repo",
                    disabled: readOnly || undefined,
                },
                h("option", { value: "", text: "Not in Git" }),
                ...(config.repositories || []).map((repo) =>
                    h("option", { value: repo.id, text: repo.name, selected: payload.source_repo === repo.id || undefined })
                )
            ),
            { id: "field-repo", error: errorFor("source_repo") }
        ),
        h(
            "div",
            { class: "form-check mb-3" },
            h("input", {
                class: "form-check-input",
                type: "checkbox",
                id: "field-approval",
                "data-bind": "approval_required",
                checked: payload.approval_required,
                disabled: readOnly || undefined,
            }),
            h("label", { class: "form-check-label", for: "field-approval", text: labels.workflow.approval_required.label, title: labels.workflow.approval_required.help })
        ),
        h("div", { class: "form-text mb-3", text: labels.workflow.approval_required.help }),
        renderSchedule(labels, payload, readOnly)
    );
}

function renderSchedule(labels, payload, readOnly) {
    const schedule = payload.schedule || {};
    return h(
        "div",
        { class: "card" },
        h("div", { class: "card-header", text: labels.schedule.label }),
        h(
            "div",
            { class: "card-body" },
            h("p", { class: "form-text", text: labels.schedule.help }),
            h(
                "div",
                { class: "form-check mb-3" },
                h("input", {
                    class: "form-check-input",
                    type: "checkbox",
                    id: "field-snow",
                    "data-bind": "schedule.enabled",
                    checked: schedule.enabled,
                    disabled: readOnly || undefined,
                }),
                h("label", { class: "form-check-label", for: "field-snow", text: "Set up a ServiceNow change window" })
            ),
            schedule.enabled
                ? h(
                      "div",
                      {},
                      snowInput("ServiceNow connection", "Which External Integration to use. YAML key: integration_name.", "schedule.integration_name", schedule.integration_name, readOnly),
                      snowInput("Change ticket field", "The input that holds the change number. YAML key: change_input_key.", "schedule.change_input_key", schedule.change_input_key, readOnly),
                      snowInput("Allowed change states", "For example Scheduled, Implement. Separate with commas. YAML key: allowed_states.", "schedule.allowed_states", schedule.allowed_states, readOnly),
                      snowInput("Time zone of change times", "YAML key: timezone.", "schedule.timezone", schedule.timezone, readOnly),
                      snowInput("Ticket number format", "A pattern. CHG\\d{7} is a common example. YAML key: ticket_regex.", "schedule.ticket_regex", schedule.ticket_regex, readOnly)
                  )
                : null
        )
    );
}

function snowInput(label, help, bind, value, readOnly) {
    const id = `field-${bind.replace(/\./g, "-")}`;
    return field(
        label,
        help,
        h("input", { class: "form-control", id, "data-bind": bind, value: value || "", disabled: readOnly || undefined }),
        { id }
    );
}

function renderInputs(context) {
    const { labels, payload, config, state, errorFor, readOnly } = context;
    const typeLabels = labels.input.type.labels || {};
    return h(
        "div",
        {},
        h(
            "div",
            { class: "d-flex justify-content-between align-items-center mb-3" },
            h("h2", { class: "h4 mb-0", text: "Inputs" }),
            readOnly
                ? null
                : h(
                      "div",
                      { class: "d-flex gap-2" },
                      h(
                          "select",
                          { class: "form-select form-select-sm", id: "add-input-type", "aria-label": "Field type to add" },
                          TYPE_ORDER.map((type) => h("option", { value: type, text: typeLabels[type] || type }))
                      ),
                      h("button", { type: "button", class: "btn btn-outline-primary", "data-action": "add-input", text: "Add input" })
                  )
        ),
        h(
            "div",
            { class: "row" },
            h(
                "div",
                { class: "col-xl-7" },
                payload.inputs.length
                    ? h(
                          "div",
                          { class: "list-group" },
                          payload.inputs.map((input, index) => renderInputRow(input, index, context))
                      )
                    : h("p", { class: "text-muted", text: "No inputs yet. Add the questions people answer when they launch this workflow." })
            ),
            h(
                "div",
                { class: "col-xl-5" },
                h(
                    "div",
                    { class: "card" },
                    h("div", { class: "card-header", text: "Launch form preview" }),
                    h("div", { class: "card-body", id: "launch-preview" }, h("p", { class: "text-muted mb-0", text: "The launch form appears here." }))
                )
            )
        )
    );
}

function renderInputRow(input, index, context) {
    const { labels, state, errorFor, readOnly, payload } = context;
    const open = state.expandedInput === index;
    const typeLabels = labels.input.type.labels || {};
    const summary = [input.key || "new-field", input.label || "Untitled", typeLabels[input.type] || input.type];
    if (input.required) {
        summary.push("Required");
    }
    return h(
        "div",
        { class: "list-group-item", id: `input-row-${index}` },
        h(
            "div",
            { class: "d-flex flex-wrap gap-2 align-items-center nwe-input-summary" },
            h("button", { type: "button", class: "btn btn-sm btn-outline-secondary", "data-action": "move-input", "data-index": String(index), "data-dir": "-1", text: "Up", disabled: readOnly || undefined, title: "Move up. Alt+Up also works." }),
            h("button", { type: "button", class: "btn btn-sm btn-outline-secondary", "data-action": "move-input", "data-index": String(index), "data-dir": "1", text: "Down", disabled: readOnly || undefined, title: "Move down. Alt+Down also works." }),
            h("button", {
                type: "button",
                class: "btn btn-link text-start text-decoration-none flex-grow-1",
                "data-action": "toggle-input",
                "data-index": String(index),
                text: summary.join(" · "),
                "aria-expanded": open ? "true" : "false",
            })
        ),
        open ? renderInputEditor(input, index, context) : null
    );
}

function renderInputEditor(input, index, { labels, payload, config, errorFor, readOnly }) {
    const typeLabels = labels.input.type.labels || {};
    const prefix = `inputs.${index}`;
    return h(
        "div",
        { class: "mt-3" },
        field(labels.input.label.label, labels.input.label.help, h("input", { class: "form-control", id: `field-input-${index}-label`, "data-bind": `${prefix}.label`, value: input.label, disabled: readOnly || undefined }), { id: `field-input-${index}-label`, error: errorFor(`${prefix}.label`), required: true }),
        h(
            "div",
            { class: "mb-3" },
            h("div", { class: "form-label", text: labels.input.key.label, title: labels.input.key.help }),
            h("div", { class: "form-text", text: labels.input.key.help }),
            h("code", { text: input.key ? `{{ inputs.${input.key} }}` : "Set a label to generate the variable name." }),
            readOnly
                ? null
                : h(
                      "div",
                      { class: "input-group input-group-sm mt-2" },
                      h("input", { class: "form-control", id: `field-input-${index}-rename`, value: input.key, "aria-label": "New variable name" }),
                      h("button", { type: "button", class: "btn btn-outline-secondary", "data-action": "rename-input", "data-index": String(index), text: "Rename" })
                  )
        ),
        field(
            labels.input.type.label,
            labels.input.type.help,
            h(
                "select",
                { class: "form-select", id: `field-input-${index}-type`, "data-action": "change-type", "data-index": String(index), disabled: readOnly || undefined },
                TYPE_ORDER.map((type) => h("option", { value: type, text: typeLabels[type] || type, selected: input.type === type || undefined }))
            ),
            { id: `field-input-${index}-type` }
        ),
        field(labels.input.help_text.label, labels.input.help_text.help, h("input", { class: "form-control", id: `field-input-${index}-help`, "data-bind": `${prefix}.help_text`, value: input.help_text, disabled: readOnly || undefined }), { id: `field-input-${index}-help` }),
        h(
            "div",
            { class: "form-check mb-3" },
            h("input", { class: "form-check-input", type: "checkbox", id: `field-input-${index}-required`, "data-bind": `${prefix}.required`, checked: input.required, disabled: readOnly || undefined }),
            h("label", { class: "form-check-label", for: `field-input-${index}-required`, text: labels.input.required.label, title: labels.input.required.help })
        ),
        field(labels.input.default.label, labels.input.default.help, h("input", { class: "form-control", id: `field-input-${index}-default`, "data-bind": `${prefix}.default`, value: input.default == null ? "" : input.default, disabled: readOnly || undefined }), { id: `field-input-${index}-default` }),
        input.type === "object" || input.type === "multiobject" ? renderModel(input, index, config, labels, readOnly, payload) : null,
        input.type === "choice" || input.type === "multichoice" ? renderChoices(input, index, labels, readOnly, payload) : null,
        renderDepends(input, index, payload, labels, readOnly),
        renderShowIf(input, index, labels, payload, readOnly),
        renderValidators(input, index, labels, readOnly),
        readOnly
            ? null
            : h("button", { type: "button", class: "btn btn-outline-danger", "data-action": "remove-input", "data-index": String(index), text: "Remove" })
    );
}

function renderModel(input, index, config, labels, readOnly, payload) {
    const search = h("input", { class: "form-control mb-2", type: "search", placeholder: "Search object types", "aria-label": "Search object types" });
    const select = h(
        "select",
        { class: "form-select", id: `field-input-${index}-model`, "data-bind": `inputs.${index}.model`, disabled: readOnly || undefined },
        h("option", { value: "", text: "Select an object type…" }),
        ...(config.content_types || []).map((item) => h("option", { value: item.value, text: item.label, selected: input.model === item.value || undefined }))
    );
    search.addEventListener("input", () => {
        const query = search.value.toLowerCase();
        Array.from(select.options).forEach((option, optionIndex) => {
            if (optionIndex === 0) {
                return;
            }
            option.hidden = query && !option.textContent.toLowerCase().includes(query);
        });
    });
    const queryText = typeof input.filter_query === "string" ? input.filter_query : "";
    return h(
        "div",
        {},
        field(labels.input.model.label, labels.input.model.help, h("div", {}, search, select), { id: `field-input-${index}-model` }),
        h("div", { class: "form-label", text: labels.input.filter.label, title: labels.input.filter.help }),
        h("div", { class: "form-text", text: labels.input.filter.help }),
        input.filter_query && typeof input.filter_query === "object"
            ? h("p", { class: "form-text", text: "This filter is stored as a structured query. Typing below replaces it." })
            : null,
        jinjaEditor({
            id: `field-input-${index}-filter`,
            value: queryText,
            bind: `inputs.${index}.filter_query`,
            pointer: `/inputs/${index}/filter/query`,
            getReferences: () => referenceList(payload, "pre", 0),
            readOnly,
        })
    );
}

function renderChoices(input, index, labels, readOnly, payload) {
    const rows = (input.choices || []).map((choice, choiceIndex) =>
        h(
            "div",
            { class: "row g-2 mb-2" },
            h("div", { class: "col-md-5" }, h("input", { class: "form-control", "data-bind": `inputs.${index}.choices.${choiceIndex}.value`, value: choice.value || "", placeholder: "Value", "aria-label": "Option value", disabled: readOnly || undefined })),
            h("div", { class: "col-md-5" }, h("input", { class: "form-control", "data-bind": `inputs.${index}.choices.${choiceIndex}.label`, value: choice.label || "", placeholder: "Display text", "aria-label": "Option display text", disabled: readOnly || undefined })),
            h("div", { class: "col-md-2" }, h("button", { type: "button", class: "btn btn-outline-danger w-100", "data-action": "remove-choice", "data-index": String(index), "data-choice": String(choiceIndex), text: "Remove", disabled: readOnly || undefined }))
        )
    );
    return h(
        "div",
        { class: "mb-3" },
        h("div", { class: "form-label", text: labels.input.choices.label, title: labels.input.choices.help }),
        h("div", { class: "form-text", text: labels.input.choices.help }),
        ...rows,
        readOnly ? null : h("button", { type: "button", class: "btn btn-sm btn-outline-secondary mb-2", "data-action": "add-choice", "data-index": String(index), text: "Add option" }),
        h("div", { class: "form-text", text: "Or compute the options with Jinja. Jinja2 is allowed. YAML key: choices." }),
        jinjaEditor({
            id: `field-input-${index}-choices`,
            value: input.choices_template || "",
            bind: `inputs.${index}.choices_template`,
            pointer: `/inputs/${index}/choices`,
            getReferences: () => referenceList(payload, "pre", 0),
            readOnly,
        })
    );
}

function renderDepends(input, index, payload, labels, readOnly) {
    const selected = new Set(input.depends_on || []);
    const search = h("input", { class: "form-control form-control-sm mb-2", type: "search", placeholder: "Search fields", "aria-label": "Search fields to wait for" });
    const list = h(
        "div",
        { class: "border rounded p-2 nwe-multiselect-list" },
        payload.inputs
            .map((other, otherIndex) => ({ other, otherIndex }))
            .filter((item) => item.otherIndex !== index && item.other.key)
            .map(({ other }) => {
                const box = h("input", {
                    class: "form-check-input",
                    type: "checkbox",
                    checked: selected.has(other.key),
                    "data-action": "toggle-depends",
                    "data-index": String(index),
                    "data-key": other.key,
                    disabled: readOnly || undefined,
                });
                const row = h(
                    "label",
                    { class: "form-check nwe-depend-option" },
                    box,
                    h("span", { class: "form-check-label", text: `${other.label || other.key} (${other.key})` })
                );
                row.dataset.search = `${other.label || ""} ${other.key}`.toLowerCase();
                return row;
            })
    );
    search.addEventListener("input", () => {
        const query = search.value.toLowerCase();
        list.querySelectorAll(".nwe-depend-option").forEach((row) => {
            row.hidden = query && !row.dataset.search.includes(query);
        });
    });
    return h(
        "div",
        { class: "mb-3", id: `depends-${index}` },
        h("div", { class: "form-label", text: labels.input.depends_on.label, title: labels.input.depends_on.help }),
        h("div", { class: "form-text", text: labels.input.depends_on.help }),
        h(
            "div",
            { class: "d-flex gap-2" },
            search,
            h("button", { type: "button", class: "btn btn-sm btn-outline-secondary", "data-action": "clear-depends", "data-index": String(index), text: "Clear", disabled: readOnly || undefined })
        ),
        list
    );
}

function renderShowIf(input, index, labels, payload, readOnly) {
    const value = typeof input.show_if === "string" ? input.show_if : "";
    return h(
        "div",
        { class: "mb-3" },
        h("div", { class: "form-label", text: labels.input.show_if.label, title: labels.input.show_if.help }),
        h("div", { class: "form-text", text: labels.input.show_if.help }),
        input.show_if && typeof input.show_if === "object"
            ? h("p", { class: "form-text", text: "This field uses a structured condition. Typing below replaces it with a Jinja expression." })
            : null,
        jinjaEditor({
            id: `field-input-${index}-showif`,
            value,
            bind: `inputs.${index}.show_if`,
            pointer: `/inputs/${index}/show_if`,
            getReferences: () => referenceList(payload, "pre", 0),
            readOnly,
        })
    );
}

function renderValidators(input, index, labels, readOnly) {
    const flagLabels = { IGNORECASE: "Case-insensitive", MULTILINE: "Multi-line", DOTALL: "Dot matches newline" };
    const rules = input.validators || [];
    return h(
        "div",
        { class: "mb-3" },
        h("div", { class: "form-label", text: labels.input.validators.label, title: labels.input.validators.help }),
        h("div", { class: "form-text", text: labels.input.validators.help }),
        ...rules.map((rule, ruleIndex) => renderValidator(rule, index, ruleIndex, flagLabels, readOnly)),
        readOnly ? null : h("button", { type: "button", class: "btn btn-sm btn-outline-secondary", "data-action": "add-validator", "data-index": String(index), text: "Add validation rule" })
    );
}

function renderValidator(rule, index, ruleIndex, flagLabels, readOnly) {
    const flags = new Set(rule.flags || []);
    return h(
        "div",
        { class: "card nwe-validator mb-2" },
        h(
            "div",
            { class: "card-body" },
            h(
                "div",
                { class: "row g-2" },
                h(
                    "div",
                    { class: "col-md-6" },
                    h("label", { class: "form-label", text: "Pattern", title: "Regular expression the value must match. YAML key: regex." }),
                    h("input", { class: "form-control", "data-bind": `inputs.${index}.validators.${ruleIndex}.regex`, value: rule.regex || "", "aria-label": "Pattern", disabled: readOnly || undefined })
                ),
                h(
                    "div",
                    { class: "col-md-6" },
                    h("label", { class: "form-label", text: "Error text", title: "Shown when the value does not match. YAML key: message." }),
                    h("input", { class: "form-control", "data-bind": `inputs.${index}.validators.${ruleIndex}.message`, value: rule.message || "", "aria-label": "Error text", disabled: readOnly || undefined })
                )
            ),
            h(
                "div",
                { class: "d-flex flex-wrap gap-3 mt-2" },
                ...Object.entries(flagLabels).map(([flag, label]) =>
                    h(
                        "div",
                        { class: "form-check" },
                        h("input", {
                            class: "form-check-input",
                            type: "checkbox",
                            checked: flags.has(flag),
                            "data-action": "toggle-flag",
                            "data-index": String(index),
                            "data-rule": String(ruleIndex),
                            "data-flag": flag,
                            disabled: readOnly || undefined,
                            id: `flag-${index}-${ruleIndex}-${flag}`,
                        }),
                        h("label", { class: "form-check-label", for: `flag-${index}-${ruleIndex}-${flag}`, text: label })
                    )
                )
            ),
            readOnly
                ? null
                : h("button", { type: "button", class: "btn btn-sm btn-outline-danger mt-2", "data-action": "remove-validator", "data-index": String(index), "data-rule": String(ruleIndex), text: "Remove" })
        )
    );
}

function renderSteps(context) {
    const { labels, payload, readOnly } = context;
    return h(
        "div",
        {},
        h("h2", { class: "h4", text: "Steps" }),
        ...PHASES.map(([phase]) => renderPhase(phase, labels.phases[phase], context))
    );
}

function renderPhase(phase, meta, context) {
    const { payload, readOnly, state } = context;
    const actions = payload.actions[phase] || [];
    return h(
        "div",
        { class: "card mb-3" },
        h(
            "div",
            { class: "card-header d-flex justify-content-between align-items-center" },
            h("span", { text: `${meta.label} (${actions.length})` }),
            readOnly
                ? null
                : h(
                      "div",
                      { class: "btn-group btn-group-sm" },
                      h("button", { type: "button", class: "btn btn-outline-primary", "data-action": "add-step", "data-phase": phase, "data-type": "awx", text: "Add AWX step" }),
                      h("button", { type: "button", class: "btn btn-outline-primary", "data-action": "add-step", "data-phase": phase, "data-type": "nautobot", text: "Add script step" })
                  )
        ),
        h(
            "div",
            { class: "card-body" },
            h("p", { class: "text-muted", text: meta.help }),
            actions.length
                ? h(
                      "div",
                      { class: "list-group" },
                      actions.map((action, index) => renderStep(action, phase, index, context))
                  )
                : h("p", { class: "mb-0", text: "No steps in this part." })
        )
    );
}

function renderStep(action, phase, index, context) {
    const { labels, payload, config, state, errorFor, readOnly } = context;
    const open = state.expandedStep.phase === phase && state.expandedStep.index === index;
    const typeLabels = labels.action.type.labels || {};
    const title = `${index + 1}. ${action.name || "Untitled"} · ${typeLabels[action.type] || action.type}`;
    return h(
        "div",
        { class: "list-group-item" },
        h("button", {
            type: "button",
            class: "btn btn-link text-start text-decoration-none px-0",
            "data-action": "toggle-step",
            "data-phase": phase,
            "data-index": String(index),
            text: title,
            "aria-expanded": open ? "true" : "false",
        }),
        open ? renderStepEditor(action, phase, index, context) : null
    );
}

function renderStepEditor(action, phase, index, { labels, payload, config, errorFor, readOnly }) {
    const prefix = `actions.${phase}.${index}`;
    const scripts = ((config.scripts_by_repo || {})[payload.source_repo] || []).slice();
    return h(
        "div",
        { class: "mt-3" },
        field(labels.action.name.label, labels.action.name.help, h("input", { class: "form-control", id: `field-step-${phase}-${index}-name`, "data-bind": `${prefix}.name`, value: action.name, disabled: readOnly || undefined }), { id: `field-step-${phase}-${index}-name`, error: errorFor(`${prefix}.name`), required: true }),
        h("p", { class: "form-text", text: action.name ? `Results are available as {{ results.${action.name} }}.` : "Name the step to refer to its results." }),
        action.type === "awx"
            ? field(labels.action.template_id.label, labels.action.template_id.help, h("input", { class: "form-control", id: `field-step-${phase}-${index}-template`, type: "number", inputmode: "numeric", min: "1", "data-bind": `${prefix}.template_id`, value: action.template_id, disabled: readOnly || undefined }), { id: `field-step-${phase}-${index}-template`, error: errorFor(`${prefix}.template_id`), required: true })
            : field(
                  labels.action.script.label,
                  labels.action.script.help,
                  h(
                      "select",
                      { class: "form-select", id: `field-step-${phase}-${index}-script`, "data-bind": `${prefix}.script`, disabled: readOnly || undefined },
                      h("option", { value: "", text: "Select a script…" }),
                      h("option", { value: "scripts/notify.py", text: "scripts/notify.py", selected: action.script === "scripts/notify.py" || undefined }),
                      ...scripts.filter((path) => path !== "scripts/notify.py").map((path) => h("option", { value: path, text: path, selected: action.script === path || undefined }))
                  ),
                  { id: `field-step-${phase}-${index}-script`, error: errorFor(`${prefix}.script`), required: true }
              ),
        action.type === "awx"
            ? h(
                  "div",
                  { class: "form-check mb-3" },
                  h("input", { class: "form-check-input", type: "checkbox", id: `field-step-${phase}-${index}-monitor`, "data-bind": `${prefix}.monitor`, checked: action.monitor, disabled: readOnly || undefined }),
                  h("label", { class: "form-check-label", for: `field-step-${phase}-${index}-monitor`, text: labels.action.monitor.label, title: labels.action.monitor.help })
              )
            : null,
        renderMapping(action.type === "awx" ? action.extra_vars : action.with, phase, index, action.type === "awx" ? "extra_vars" : "with", action.type === "awx" ? labels.action.extra_vars : labels.action.with, payload, readOnly),
        h("div", { class: "form-label mt-3", text: labels.action.when.label, title: labels.action.when.help }),
        h("div", { class: "form-text", text: labels.action.when.help }),
        jinjaEditor({
            id: `field-step-${phase}-${index}-when`,
            value: action.when || "",
            bind: `${prefix}.when`,
            pointer: `/${phase === "pre" ? "pre_actions" : phase === "post" ? "post_actions" : "actions"}/${index}/when`,
            getReferences: () => referenceList(payload, phase, index),
            readOnly,
        }),
        readOnly ? null : h("button", { type: "button", class: "btn btn-outline-danger mt-3", "data-action": "remove-step", "data-phase": phase, "data-index": String(index), text: "Remove" })
    );
}

function renderMapping(mapping, phase, index, fieldName, meta, payload, readOnly) {
    const entries = Object.entries(mapping || {});
    return h(
        "div",
        { class: "mb-3" },
        h("div", { class: "form-label", text: meta.label, title: meta.help }),
        h("div", { class: "form-text", text: meta.help }),
        ...entries.map(([key, value]) =>
            h(
                "div",
                { class: "row g-2 mb-2" },
                h("div", { class: "col-md-3" }, h("input", { class: "form-control", value: key, readonly: true, "aria-label": "Variable name" })),
                h(
                    "div",
                    { class: "col-md-7" },
                    jinjaEditor({
                        id: `kv-${phase}-${index}-${fieldName}-${key}`,
                        value: value == null ? "" : String(value),
                        bind: `actions.${phase}.${index}.${fieldName}.${key}`,
                        pointer: `/${phase === "pre" ? "pre_actions" : phase === "post" ? "post_actions" : "actions"}/${index}/${fieldName}`,
                        getReferences: () => referenceList(payload, phase, index),
                        readOnly,
                    })
                ),
                h("div", { class: "col-md-2" }, h("button", { type: "button", class: "btn btn-outline-danger w-100", "data-action": "remove-kv", "data-phase": phase, "data-index": String(index), "data-field": fieldName, "data-key": key, text: "Remove", disabled: readOnly || undefined }))
            )
        ),
        readOnly
            ? null
            : h(
                  "div",
                  { class: "input-group" },
                  h("input", { class: "form-control", id: `kv-new-${phase}-${index}-${fieldName}`, placeholder: "Variable name", "aria-label": "New variable name" }),
                  h("button", { type: "button", class: "btn btn-outline-secondary", "data-action": "add-kv", "data-phase": phase, "data-index": String(index), "data-field": fieldName, text: "Add variable" })
              )
    );
}

function renderReview({ payload, config, errorFor, readOnly }) {
    const makeAvailable = h("input", {
        class: "form-check-input",
        type: "checkbox",
        id: "field-enable",
        "data-bind": "enabled",
        checked: Boolean(config.published_number) && payload.enabled,
        disabled: readOnly || undefined,
    });
    return h(
        "div",
        {},
        h("h2", { class: "h4", text: "Review & Publish" }),
        h("div", { id: "review-problems", class: "alert alert-secondary", role: "status", text: "Checking this workflow…" }),
        h(
            "div",
            { class: "card mb-3" },
            h(
                "div",
                { class: "card-header d-flex justify-content-between" },
                h("span", { text: "YAML preview" }),
                h("button", { type: "button", class: "btn btn-sm btn-outline-secondary", id: "copy-yaml", text: "Copy YAML" })
            ),
            h(
                "div",
                { class: "card-body" },
                h("pre", { id: "yaml-preview", class: "nwe-yaml mb-2" }),
                h("div", { id: "yaml-errors", class: "text-danger small" })
            )
        ),
        h(
            "div",
            { class: "card" },
            h("div", { class: "card-header", text: "Publish" }),
            h(
                "div",
                { class: "card-body" },
                field("Summary", "Shown in the history and, when this workflow is in Git, in the commit message.", h("input", { class: "form-control", id: "field-summary", "data-summary": "1" }), { id: "field-summary" }),
                h("div", { class: "form-check mb-3" }, makeAvailable, h("label", { class: "form-check-label", for: "field-enable", text: "Make available to launch after publishing" })),
                h("p", { class: "form-text", text: config.published_number ? "This keeps the current availability unless you change it." : "New workflows stay off the launch page until you check this." }),
                h("p", { class: "mb-0", id: "publish-target", text: publishTarget(payload, config) })
            )
        )
    );
}

function publishTarget(payload, config) {
    const repo = (config.repositories || []).find((item) => item.id === payload.source_repo);
    if (!repo) {
        return "This workflow is not saved to Git.";
    }
    const file = `workflows/${payload.key || "workflow"}.yaml`;
    return `Git: push to ${repo.name} as ${file}.`;
}

export function errorText(message) {
    return String(message || "")
        .replace(/\/[A-Za-z0-9_~]+(?:\/[A-Za-z0-9_~]+)+/g, "")
        .replace(/\s{2,}/g, " ")
        .trim();
}
