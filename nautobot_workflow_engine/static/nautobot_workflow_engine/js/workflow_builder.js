/**
 * Workflow builder UI.
 *
 * The page posts a builder payload. YAML preview and export call the same
 * serializer on the server so the preview matches what Save stores.
 */
(function workflowBuilder() {
    const configNode = document.getElementById("workflow-builder-config");
    const form = document.getElementById("workflow-builder-form");
    if (!configNode || !form) {
        return;
    }

    const config = JSON.parse(configNode.textContent);
    const state = normalizePayload(config.payload || {});
    let activePhase = "main";
    let previewTimer = null;
    let previewText = "";

    const INPUT_TYPES = [
        ["string", "String"],
        ["integer", "Integer"],
        ["boolean", "Boolean"],
        ["choice", "Choice"],
        ["multichoice", "Multi-choice"],
        ["object", "Object"],
        ["multiobject", "Multi-object"],
        ["list", "List"],
        ["listdict", "List of dictionaries"],
    ];
    const COLUMN_TYPES = [
        ["string", "String"],
        ["integer", "Integer"],
        ["number", "Number"],
        ["boolean", "Boolean"],
        ["choice", "Choice"],
    ];

    form.addEventListener("input", onField);
    form.addEventListener("change", onField);
    form.addEventListener("click", onClick);
    form.addEventListener("submit", function onSubmit(event) {
        if (config.read_only) {
            event.preventDefault();
            return;
        }
        if (config.approval_was_required && !state.approval_required) {
            if (config.pending_approvals) {
                event.preventDefault();
                window.alert(config.pending_approvals + " runs are waiting for approval. Resolve them first.");
                state.approval_required = true;
                render();
                return;
            }
            if ((config.approval_stages || []).length) {
                const lines = config.approval_stages.map(function describeStage(stage) {
                    return (
                        stage.sequence +
                        ". " +
                        stage.name +
                        " (at least " +
                        stage.min_approvers +
                        ", " +
                        (stage.approver_group_name || "group") +
                        ")"
                    );
                });
                if (!window.confirm("Turn approval off?\n" + lines.join("\n"))) {
                    event.preventDefault();
                    state.approval_required = true;
                    render();
                    return;
                }
                const confirmInput = document.getElementById("approval-confirm");
                if (confirmInput) {
                    confirmInput.value = "1";
                }
            }
        }
        document.getElementById("builder-payload-input").value = JSON.stringify(serialize());
    });
    document.getElementById("copy-yaml").addEventListener("click", copyYaml);
    document.getElementById("download-yaml").addEventListener("click", downloadYaml);

    render();
    schedulePreview();

    function normalizePayload(payload) {
        const schedule = Object.assign(
            {
                enabled: false,
                integration_name: "",
                change_input_key: "",
                allowed_states: [],
                timezone: "",
                start_fields: [],
                end_fields: [],
                ticket_regex: "",
                require_approved: null,
                preserved_schedule: {},
                preserved_servicenow: {},
            },
            payload.schedule || {}
        );
        const actions = payload.actions || {};
        return {
            key: payload.key || "",
            name: payload.name || "",
            description: payload.description || "",
            category: payload.category || "",
            enabled: Boolean(payload.enabled),
            approval_required: Boolean(payload.approval_required),
            manual_duration_minutes: payload.manual_duration_minutes == null ? "" : String(payload.manual_duration_minutes),
            source_repo: payload.source_repo || "",
            preserved: payload.preserved || {},
            schedule: schedule,
            inputs: (payload.inputs || []).map(normalizeInput),
            actions: {
                pre: (actions.pre || []).map(normalizeAction),
                main: (actions.main || []).map(normalizeAction),
                post: (actions.post || []).map(normalizeAction),
            },
        };
    }

    function normalizeInput(input) {
        return {
            key: input.key || "",
            label: input.label || "",
            type: input.type || "string",
            required: Boolean(input.required),
            default: defaultForEditor(input),
            depends_on: input.depends_on || [],
            show_if_rows: showIfToRows(input.show_if),
            validators: (input.validators || []).map(normalizeValidator),
            choices: Array.isArray(input.choices) ? input.choices.map(normalizeChoice) : [],
            choices_template:
                typeof input.choices === "string" ? input.choices : input.choices_template || "",
            model: input.model || "",
            filter_query: input.filter_query || "",
            columns: (input.columns || []).map(normalizeColumn),
            preserved: input.preserved || {},
        };
    }

    function normalizeColumn(column) {
        return {
            key: column.key || "",
            label: column.label || "",
            type: column.type || "string",
            required: Boolean(column.required),
            unique: Boolean(column.unique),
            choices: column.choices || [],
            validators: (column.validators || []).map(normalizeValidator),
            preserved: column.preserved || {},
        };
    }

    function normalizeValidator(validator) {
        return {
            regex: validator.regex || "",
            message: validator.message || "",
            flags: Array.isArray(validator.flags) ? validator.flags.join(", ") : validator.flags || "",
        };
    }

    function normalizeAction(action) {
        return {
            type: action.type || "nautobot",
            name: action.name || "",
            when: action.when || "",
            script: action.script || "",
            with: action.with || "",
            template_id: action.template_id == null ? "" : String(action.template_id),
            extra_vars: action.extra_vars || "",
            monitor: action.monitor == null ? null : Boolean(action.monitor),
            preserved: action.preserved || {},
        };
    }

    function defaultForEditor(input) {
        if (input.type === "boolean") {
            return input.default == null ? "" : String(input.default);
        }
        if (input.default == null) {
            return "";
        }
        if (typeof input.default === "object") {
            return JSON.stringify(input.default);
        }
        return String(input.default);
    }

    function showIfToRows(showIf) {
        if (!showIf || typeof showIf !== "object" || Array.isArray(showIf)) {
            return [];
        }
        return Object.entries(showIf).map(function mapRow(entry) {
            const value = entry[1];
            return {
                control: entry[0],
                values: Array.isArray(value) ? value.map(String).join("|") : String(value),
            };
        });
    }

    function serialize() {
        const payload = {
            key: state.key.trim(),
            name: state.name.trim(),
            description: state.description,
            category: state.category,
            enabled: Boolean(state.enabled),
            approval_required: Boolean(state.approval_required),
            manual_duration_minutes: durationForPayload(state.manual_duration_minutes),
            source_repo: state.source_repo,
            preserved: state.preserved || {},
            schedule: Object.assign({}, state.schedule),
            inputs: state.inputs.map(serializeInput),
            actions: {
                pre: state.actions.pre.map(serializeAction),
                main: state.actions.main.map(serializeAction),
                post: state.actions.post.map(serializeAction),
            },
        };
        if (payload.schedule.require_approved === "") {
            payload.schedule.require_approved = null;
        }
        return payload;
    }

    function serializeInput(input) {
        const showIf = {};
        (input.show_if_rows || []).forEach(function addCondition(row) {
            const control = (row.control || "").trim();
            const raw = (row.values || "").trim();
            if (!control || !raw) {
                return;
            }
            showIf[control] = raw.includes("|") ? raw.split("|").map(coerceScalar) : coerceScalar(raw);
        });
        return {
            key: input.key.trim(),
            label: input.label,
            type: input.type,
            required: Boolean(input.required),
            default: serializeDefault(input),
            depends_on: input.depends_on || [],
            show_if: showIf,
            validators: (input.validators || []).map(serializeValidator),
            choices: input.choices_template ? input.choices_template : input.choices || [],
            model: input.model,
            filter_query: input.filter_query,
            columns: (input.columns || []).map(serializeColumn),
            preserved: input.preserved || {},
        };
    }

    function serializeColumn(column) {
        return {
            key: column.key,
            label: column.label,
            type: column.type,
            required: Boolean(column.required),
            unique: Boolean(column.unique),
            choices: column.choices || [],
            validators: (column.validators || []).map(serializeValidator),
            preserved: column.preserved || {},
        };
    }

    function serializeValidator(validator) {
        return {
            regex: validator.regex,
            message: validator.message,
            flags: validator.flags,
        };
    }

    function serializeAction(action) {
        return {
            type: action.type,
            name: action.name,
            when: action.when,
            script: action.script,
            with: action.with,
            template_id: action.template_id === "" ? null : action.template_id,
            extra_vars: action.extra_vars,
            monitor: action.type === "awx" && action.monitor == null ? true : action.monitor,
            preserved: action.preserved || {},
        };
    }

    function serializeDefault(input) {
        if (input.default === "" || input.default == null) {
            return null;
        }
        if (input.type === "boolean") {
            if (input.default === "true") {
                return true;
            }
            if (input.default === "false") {
                return false;
            }
            return null;
        }
        if (input.type === "integer" && /^-?\d+$/.test(String(input.default))) {
            return Number(input.default);
        }
        if (["list", "listdict", "multichoice", "multiobject", "object"].includes(input.type)) {
            try {
                return JSON.parse(input.default);
            } catch (error) {
                return input.default;
            }
        }
        return input.default;
    }

    function coerceScalar(text) {
        if (text === "true") {
            return true;
        }
        if (text === "false") {
            return false;
        }
        if (/^-?\d+$/.test(text)) {
            return Number(text);
        }
        return text;
    }

    function render() {
        renderMetadata();
        renderInputs();
        renderActions();
        renderSchedule();
        if (config.read_only) {
            form.querySelectorAll("input, select, textarea, button").forEach(function disableControl(node) {
                if (node.type === "hidden") {
                    return;
                }
                node.disabled = true;
            });
        }
    }

    function durationForPayload(value) {
        if (value === "" || value == null) {
            return null;
        }
        const text = String(value).trim();
        if (!/^\d+$/.test(text)) {
            return text;
        }
        return Number(text);
    }

    function normalizeChoice(choice) {
        if (choice && typeof choice === "object") {
            return {
                value: choice.value == null ? "" : choice.value,
                label: choice.label == null ? String(choice.value == null ? "" : choice.value) : choice.label,
            };
        }
        return {value: choice, label: String(choice)};
    }

    function renderChoicesTemplate(input, index) {
        const area = textArea(
            "inputs." + index + ".choices_template",
            input.choices_template,
            4,
            "{{ inputs.site }}"
        );
        if (!config.can_author_code) {
            area.readOnly = true;
        }
        const block = field("Advanced (YAML)", area);
        return block;
    }

    function renderMetadata() {
        const root = document.getElementById("builder-metadata");
        root.replaceChildren(
            field(
                "Key",
                textInput("key", state.key, {
                    readonly: config.is_edit ? "readonly" : null,
                    placeholder: "unique-workflow-key",
                })
            ),
            field("Name", textInput("name", state.name)),
            field("Description", textArea("description", state.description, 2)),
            field("Category", textInput("category", state.category)),
            checkbox("Enabled", "enabled", state.enabled),
            checkbox(
                "Approval required",
                "approval_required",
                state.approval_required,
                config.approvals_supported ? null : {disabled: "disabled"}
            ),
            field("Manual duration (minutes)", textInput("manual_duration_minutes", state.manual_duration_minutes)),
            field(
                "Source Git repository",
                selectInput(
                    "source_repo",
                    [{value: "", label: "None"}].concat(
                        (config.repositories || []).map(function mapRepo(repo) {
                            return {value: repo.id, label: repo.name};
                        })
                    ),
                    state.source_repo,
                    true
                )
            )
        );
        if (!config.approvals_supported) {
            root.append(el("p", {class: "form-text", text: "Requires Nautobot 3.2+"}));
        }
        root.append(
            el("p", {
                class: "form-text",
                text: "Nautobot script actions run from the repository selected here. AWX-only workflows can leave it empty.",
            })
        );
    }

    function renderInputs() {
        const root = document.getElementById("builder-inputs");
        root.replaceChildren();
        if (!state.inputs.length) {
            root.append(el("p", {class: "text-muted mb-0", text: "No inputs yet."}));
            return;
        }
        state.inputs.forEach(function renderOne(input, index) {
            root.append(renderInputCard(input, index));
        });
    }

    function renderInputCard(input, index) {
        const card = el("div", {class: "border rounded p-3 mb-3"});
        const header = el("div", {class: "d-flex justify-content-between align-items-center mb-3"});
        header.append(
            el("strong", {text: input.label || input.key || "Input " + (index + 1)}),
            buttonRow([
                ["move-input", "Up", {index: index, direction: "-1"}],
                ["move-input", "Down", {index: index, direction: "1"}],
                ["remove-input", "Remove", {index: index}],
            ])
        );
        card.append(header);
        card.append(
            field("Key", textInput("inputs." + index + ".key", input.key)),
            field("Label", textInput("inputs." + index + ".label", input.label)),
            field(
                "Type",
                selectInput(
                    "inputs." + index + ".type",
                    INPUT_TYPES.map(function mapType(item) {
                        return {value: item[0], label: item[1]};
                    }),
                    input.type,
                    true
                )
            ),
            checkbox("Required", "inputs." + index + ".required", input.required),
            field("Default", defaultControl(input, index)),
            field(
                "Depends on",
                selectInput(
                    "inputs." + index + ".depends_on",
                    otherInputOptions(index),
                    input.depends_on,
                    false,
                    true
                )
            )
        );
        card.append(renderShowIf(input, index));
        card.append(renderValidators(input.validators, "inputs." + index + ".validators", "add-validator", index));
        if (input.type === "choice" || input.type === "multichoice") {
            card.append(renderChoices(input.choices, "inputs." + index + ".choices", "add-choice", index));
            card.append(renderChoicesTemplate(input, index));
        }
        if (input.type === "object" || input.type === "multiobject") {
            card.append(
                field(
                    "Model",
                    selectInput(
                        "inputs." + index + ".model",
                        [{value: "", label: "Select a model"}].concat(config.content_types || []),
                        input.model,
                        false
                    )
                ),
                field(
                    "Filter query",
                    textArea("inputs." + index + ".filter_query", input.filter_query, 3, "location_id: \"{{ inputs.location.id }}\"")
                )
            );
        }
        if (input.type === "listdict") {
            card.append(renderColumns(input, index));
        }
        return card;
    }

    function defaultControl(input, index) {
        if (input.type === "boolean") {
            return selectInput(
                "inputs." + index + ".default",
                [
                    {value: "", label: "No default"},
                    {value: "true", label: "true"},
                    {value: "false", label: "false"},
                ],
                input.default,
                false
            );
        }
        return textInput("inputs." + index + ".default", input.default);
    }

    function renderShowIf(input, index) {
        const block = el("div", {class: "mb-3"});
        block.append(el("label", {class: "form-label", text: "Show if"}));
        (input.show_if_rows || []).forEach(function renderRow(row, rowIndex) {
            const line = el("div", {class: "row g-2 mb-2"});
            const controlCol = el("div", {class: "col-md-5"});
            const valueCol = el("div", {class: "col-md-5"});
            const buttonCol = el("div", {class: "col-md-2"});
            controlCol.append(
                selectInput(
                    "inputs." + index + ".show_if_rows." + rowIndex + ".control",
                    [{value: "", label: "Controlling input"}].concat(otherInputOptions(index)),
                    row.control,
                    false
                )
            );
            valueCol.append(
                textInput("inputs." + index + ".show_if_rows." + rowIndex + ".values", row.values, {
                    placeholder: "value or a|b",
                })
            );
            buttonCol.append(actionButton("remove-show-if", "Remove", {index: index, row: rowIndex}));
            line.append(controlCol, valueCol, buttonCol);
            block.append(line);
        });
        block.append(actionButton("add-show-if", "Add condition", {index: index}));
        return block;
    }

    function renderValidators(validators, path, action, index, column) {
        const block = el("div", {class: "mb-3"});
        block.append(el("label", {class: "form-label", text: "Validators"}));
        (validators || []).forEach(function renderValidator(validator, validatorIndex) {
            const line = el("div", {class: "row g-2 mb-2"});
            line.append(
                wrapCol(
                    textInput(path + "." + validatorIndex + ".regex", validator.regex, {placeholder: "regex"}),
                    "col-md-4"
                ),
                wrapCol(
                    textInput(path + "." + validatorIndex + ".message", validator.message, {placeholder: "message"}),
                    "col-md-4"
                ),
                wrapCol(
                    textInput(path + "." + validatorIndex + ".flags", validator.flags, {placeholder: "IGNORECASE"}),
                    "col-md-3"
                ),
                wrapCol(
                    actionButton("remove-validator", "Remove", {
                        index: index,
                        validator: validatorIndex,
                        column: column == null ? "" : String(column),
                    }),
                    "col-md-1"
                )
            );
            block.append(line);
        });
        block.append(
            actionButton(action, "Add validator", {index: index, column: column == null ? "" : String(column)})
        );
        return block;
    }

    function renderChoices(choices, path, action, index, column) {
        const block = el("div", {class: "mb-3"});
        block.append(el("label", {class: "form-label", text: "Choices"}));
        (choices || []).forEach(function renderChoice(choice, choiceIndex) {
            const line = el("div", {class: "row g-2 mb-2"});
            line.append(
                wrapCol(textInput(path + "." + choiceIndex + ".value", choice.value, {placeholder: "value"}), "col-md-5"),
                wrapCol(textInput(path + "." + choiceIndex + ".label", choice.label, {placeholder: "label"}), "col-md-5"),
                wrapCol(
                    actionButton("remove-choice", "Remove", {
                        index: index,
                        choice: choiceIndex,
                        column: column == null ? "" : String(column),
                    }),
                    "col-md-2"
                )
            );
            block.append(line);
        });
        block.append(actionButton(action, "Add choice", {index: index, column: column == null ? "" : String(column)}));
        return block;
    }

    function renderColumns(input, index) {
        const block = el("div", {class: "mb-2"});
        block.append(el("label", {class: "form-label", text: "Columns"}));
        input.columns.forEach(function renderColumn(column, columnIndex) {
            const card = el("div", {class: "border rounded p-2 mb-2"});
            card.append(
                field("Column key", textInput("inputs." + index + ".columns." + columnIndex + ".key", column.key)),
                field("Column label", textInput("inputs." + index + ".columns." + columnIndex + ".label", column.label)),
                field(
                    "Column type",
                    selectInput(
                        "inputs." + index + ".columns." + columnIndex + ".type",
                        COLUMN_TYPES.map(function mapType(item) {
                            return {value: item[0], label: item[1]};
                        }),
                        column.type,
                        true
                    )
                ),
                checkbox("Required", "inputs." + index + ".columns." + columnIndex + ".required", column.required),
                checkbox("Unique", "inputs." + index + ".columns." + columnIndex + ".unique", column.unique)
            );
            if (column.type === "choice") {
                card.append(
                    renderChoices(
                        column.choices,
                        "inputs." + index + ".columns." + columnIndex + ".choices",
                        "add-column-choice",
                        index,
                        columnIndex
                    )
                );
            }
            card.append(
                renderValidators(
                    column.validators,
                    "inputs." + index + ".columns." + columnIndex + ".validators",
                    "add-column-validator",
                    index,
                    columnIndex
                )
            );
            card.append(actionButton("remove-column", "Remove column", {index: index, column: columnIndex}));
            block.append(card);
        });
        block.append(actionButton("add-column", "Add column", {index: index}));
        return block;
    }

    function renderActions() {
        const root = document.getElementById("builder-actions");
        const tabs = el("ul", {class: "nav nav-tabs mb-3"});
        ["pre", "main", "post"].forEach(function renderTab(phase) {
            const item = el("li", {class: "nav-item"});
            const link = el("button", {
                type: "button",
                class: "nav-link" + (phase === activePhase ? " active" : ""),
                text: phase,
                "data-action": "select-phase",
                "data-phase": phase,
            });
            item.append(link);
            tabs.append(item);
        });
        root.replaceChildren(tabs);
        const actions = state.actions[activePhase];
        actions.forEach(function renderAction(action, index) {
            root.append(renderActionCard(action, index));
        });
        root.append(actionButton("add-action", "Add " + activePhase + " action", {phase: activePhase}));
    }

    function renderActionCard(action, index) {
        const path = "actions." + activePhase + "." + index;
        const card = el("div", {class: "border rounded p-3 mb-3"});
        const header = el("div", {class: "d-flex justify-content-between align-items-center mb-3"});
        header.append(
            el("strong", {text: action.name || action.type || "Action"}),
            buttonRow([
                ["move-action", "Up", {index: index, direction: "-1"}],
                ["move-action", "Down", {index: index, direction: "1"}],
                ["remove-action", "Remove", {index: index}],
            ])
        );
        card.replaceChildren(header);
        card.append(
            field("Name", textInput(path + ".name", action.name)),
            field(
                "Type",
                selectInput(
                    path + ".type",
                    [
                        {value: "nautobot", label: "Nautobot script"},
                        {value: "awx", label: "AWX job template"},
                    ],
                    action.type,
                    true
                )
            ),
            field(
                "When",
                textInput(path + ".when", action.when, {placeholder: "{{ inputs.confirm }}"})
            )
        );
        if (action.type === "awx") {
            card.append(
                field("Template ID", textInput(path + ".template_id", action.template_id)),
                field(
                    "Extra vars",
                    textArea(path + ".extra_vars", action.extra_vars, 4, 'hostname: "{{ inputs.hostname }}"')
                ),
                checkbox("Monitor job", path + ".monitor", action.monitor !== false)
            );
        } else {
            card.append(
                field("Script", scriptControl(path + ".script", action.script)),
                field("With", textArea(path + ".with", action.with, 4, 'hostname: "{{ inputs.hostname }}"'))
            );
        }
        return card;
    }

    function scriptControl(path, current) {
        const scripts = (config.scripts_by_repo || {})[state.source_repo] || [];
        if (!scripts.length && !current) {
            return textInput(path, current, {placeholder: "scripts/run.py"});
        }
        const options = [{value: "", label: "Select a script"}];
        scripts.forEach(function addScript(script) {
            options.push({value: script, label: script});
        });
        if (current && scripts.indexOf(current) === -1) {
            options.push({value: current, label: current});
        }
        return selectInput(path, options, current, false);
    }

    function renderSchedule() {
        const root = document.getElementById("builder-schedule");
        const schedule = state.schedule;
        root.replaceChildren(
            checkbox("Enable ServiceNow scheduling integration", "schedule.enabled", schedule.enabled),
            el("p", {
                class: "form-text",
                text: "The launch form offers Use ServiceNow Change Window when this is enabled. The ticket and its window are captured on the run, then refreshed by the sync job.",
            })
        );
        if (!schedule.enabled) {
            return;
        }
        const integrations = [{value: "", label: "Default (ServiceNow)"}].concat(
            (config.integrations || []).map(function mapIntegration(name) {
                return {value: name, label: name};
            })
        );
        if (schedule.integration_name && !(config.integrations || []).includes(schedule.integration_name)) {
            integrations.push({value: schedule.integration_name, label: schedule.integration_name});
        }
        root.append(
            field("External system", selectInput("schedule.integration_name", integrations, schedule.integration_name, false)),
            field(
                "Change ticket input",
                selectInput(
                    "schedule.change_input_key",
                    [{value: "", label: "Select an input"}].concat(
                        state.inputs.map(function mapInput(input) {
                            return {value: input.key, label: input.label || input.key};
                        })
                    ),
                    schedule.change_input_key,
                    false
                )
            ),
            actionButton("add-change-input", "Add change ticket input", {}),
            field(
                "Allowed states",
                textInput("schedule.allowed_states", (schedule.allowed_states || []).join(", "), {"data-kind": "csv"})
            ),
            field("Timezone", textInput("schedule.timezone", schedule.timezone, {placeholder: "UTC"})),
            field(
                "Start fields",
                textInput("schedule.start_fields", (schedule.start_fields || []).join(", "), {"data-kind": "csv"})
            ),
            field(
                "End fields",
                textInput("schedule.end_fields", (schedule.end_fields || []).join(", "), {"data-kind": "csv"})
            ),
            field("Ticket pattern", textInput("schedule.ticket_regex", schedule.ticket_regex, {placeholder: "CHG[0-9]+"})),
            checkbox("Require approved change", "schedule.require_approved", schedule.require_approved !== false)
        );
    }

    function onField(event) {
        const target = event.target;
        if (!target.dataset.bind) {
            return;
        }
        const structural = target.tagName === "SELECT" || target.type === "checkbox";
        if (event.type === "input" && structural) {
            return;
        }
        if (event.type === "change" && !structural) {
            return;
        }
        assign(state, target.dataset.bind, readValue(target));
        if (target.dataset.bind === "approval_required" && config.pending_approvals && !target.checked && config.approval_was_required) {
            window.alert(config.pending_approvals + " runs are waiting for approval. Resolve them first.");
            state.approval_required = true;
            render();
            return;
        }
        if (target.dataset.bind === "schedule.require_approved") {
            state.schedule.require_approved = target.checked;
        }
        if (target.dataset.bind.endsWith(".monitor")) {
            const action = lookup(state, target.dataset.bind.replace(/\.monitor$/, ""));
            if (action) {
                action.monitor = target.checked;
            }
        }
        if (target.dataset.rerender) {
            render();
        }
        schedulePreview();
    }

    function onClick(event) {
        const button = event.target.closest("[data-action]");
        if (!button || button.type === "submit") {
            return;
        }
        event.preventDefault();
        const action = button.dataset.action;
        const index = numberAttr(button, "index");
        if (action === "add-input") {
            state.inputs.push(normalizeInput({type: "string"}));
        } else if (action === "remove-input") {
            state.inputs.splice(index, 1);
        } else if (action === "move-input") {
            moveItem(state.inputs, index, numberAttr(button, "direction"));
        } else if (action === "add-show-if") {
            state.inputs[index].show_if_rows.push({control: "", values: ""});
        } else if (action === "remove-show-if") {
            state.inputs[index].show_if_rows.splice(numberAttr(button, "row"), 1);
        } else if (action === "add-validator") {
            state.inputs[index].validators.push({regex: "", message: "", flags: ""});
        } else if (action === "add-column-validator") {
            state.inputs[index].columns[numberAttr(button, "column")].validators.push({regex: "", message: "", flags: ""});
        } else if (action === "remove-validator") {
            const column = button.dataset.column;
            if (column === "") {
                state.inputs[index].validators.splice(numberAttr(button, "validator"), 1);
            } else {
                state.inputs[index].columns[Number(column)].validators.splice(numberAttr(button, "validator"), 1);
            }
        } else if (action === "add-choice") {
            state.inputs[index].choices.push({value: "", label: ""});
        } else if (action === "add-column-choice") {
            state.inputs[index].columns[numberAttr(button, "column")].choices.push({value: "", label: ""});
        } else if (action === "remove-choice") {
            const column = button.dataset.column;
            if (column === "") {
                state.inputs[index].choices.splice(numberAttr(button, "choice"), 1);
            } else {
                state.inputs[index].columns[Number(column)].choices.splice(numberAttr(button, "choice"), 1);
            }
        } else if (action === "add-column") {
            state.inputs[index].columns.push(normalizeColumn({type: "string"}));
        } else if (action === "remove-column") {
            state.inputs[index].columns.splice(numberAttr(button, "column"), 1);
        } else if (action === "select-phase") {
            activePhase = button.dataset.phase;
        } else if (action === "add-action") {
            state.actions[activePhase].push(normalizeAction({type: "nautobot", monitor: null}));
        } else if (action === "remove-action") {
            state.actions[activePhase].splice(index, 1);
        } else if (action === "move-action") {
            moveItem(state.actions[activePhase], index, numberAttr(button, "direction"));
        } else if (action === "add-change-input") {
            addChangeTicketInput();
        }
        render();
        schedulePreview();
    }

    function addChangeTicketInput() {
        const existing = state.inputs.find(function findTicket(input) {
            return input.key === "change_ticket";
        });
        if (!existing) {
            state.inputs.push(
                normalizeInput({
                    key: "change_ticket",
                    label: "Change Ticket",
                    type: "string",
                    required: true,
                })
            );
        }
        state.schedule.enabled = true;
        state.schedule.change_input_key = "change_ticket";
    }

    function schedulePreview() {
        window.clearTimeout(previewTimer);
        previewTimer = window.setTimeout(refreshPreview, 350);
    }

    function refreshPreview() {
        const preview = document.getElementById("yaml-preview");
        const errors = document.getElementById("yaml-errors");
        fetch(config.yaml_url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrfToken(),
            },
            body: JSON.stringify(serialize()),
        })
            .then(function readResponse(response) {
                return response.text().then(function withStatus(text) {
                    return {ok: response.ok, text: text};
                });
            })
            .then(function showPreview(result) {
                if (!result.ok) {
                    previewText = "";
                    preview.textContent = "";
                    errors.textContent = formatErrors(result.text);
                    return;
                }
                previewText = result.text;
                preview.textContent = result.text;
                errors.textContent = "";
            })
            .catch(function showFailure() {
                errors.textContent = "The YAML preview could not be refreshed.";
            });
    }

    function formatErrors(text) {
        try {
            const body = JSON.parse(text);
            return (body.errors || [])
                .map(function mapError(error) {
                    return (error.path ? error.path + ": " : "") + error.message;
                })
                .join("\n");
        } catch (error) {
            return "The YAML preview could not be refreshed.";
        }
    }

    function copyYaml() {
        if (!previewText) {
            return;
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(previewText);
        }
    }

    function downloadYaml() {
        fetch(config.yaml_url + "?download=1", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrfToken(),
            },
            body: JSON.stringify(serialize()),
        })
            .then(function readDownload(response) {
                const filename = filenameFrom(response) || (state.key || "workflow") + ".yaml";
                return response.blob().then(function withName(blob) {
                    return {ok: response.ok, blob: blob, filename: filename};
                });
            })
            .then(function saveFile(result) {
                if (!result.ok) {
                    return result.blob.text().then(function showDownloadError(text) {
                        document.getElementById("yaml-errors").textContent = formatErrors(text);
                    });
                }
                const link = document.createElement("a");
                link.href = URL.createObjectURL(result.blob);
                link.download = result.filename;
                link.click();
                URL.revokeObjectURL(link.href);
            });
    }

    function filenameFrom(response) {
        const header = response.headers.get("Content-Disposition") || "";
        const match = header.match(/filename="([^"]+)"/);
        return match ? match[1] : "";
    }

    function csrfToken() {
        const input = form.querySelector("[name=csrfmiddlewaretoken]");
        return input ? input.value : "";
    }

    function otherInputOptions(index) {
        return state.inputs
            .filter(function keepOther(_input, inputIndex) {
                return inputIndex !== index && state.inputs[inputIndex].key;
            })
            .map(function mapOption(input) {
                return {value: input.key, label: input.label || input.key};
            });
    }

    function readValue(target) {
        if (target.type === "checkbox") {
            return target.checked;
        }
        if (target.multiple) {
            return Array.from(target.selectedOptions).map(function selected(option) {
                return option.value;
            });
        }
        if (target.dataset.kind === "csv") {
            return target.value
                .split(",")
                .map(function trim(item) {
                    return item.trim();
                })
                .filter(Boolean);
        }
        return target.value;
    }

    function assign(root, path, value) {
        const parts = path.split(".");
        let cursor = root;
        for (let index = 0; index < parts.length - 1; index += 1) {
            cursor = cursor[partKey(parts[index])];
        }
        cursor[partKey(parts[parts.length - 1])] = value;
    }

    function lookup(root, path) {
        return path.split(".").reduce(function step(cursor, part) {
            return cursor ? cursor[partKey(part)] : undefined;
        }, root);
    }

    function partKey(part) {
        return /^\d+$/.test(part) ? Number(part) : part;
    }

    function moveItem(list, index, direction) {
        const target = index + direction;
        if (target < 0 || target >= list.length) {
            return;
        }
        const current = list[index];
        list[index] = list[target];
        list[target] = current;
    }

    function numberAttr(node, name) {
        return Number(node.dataset[name] || 0);
    }

    function field(label, control) {
        const wrap = el("div", {class: "mb-3"});
        wrap.append(el("label", {class: "form-label", text: label}), control);
        return wrap;
    }

    function checkbox(label, bind, checked, options) {
        const wrap = el("div", {class: "form-check mb-3"});
        const input = el("input", Object.assign({class: "form-check-input", type: "checkbox", "data-bind": bind}, options || {}));
        input.checked = Boolean(checked);
        if (bind.endsWith(".type") || bind === "source_repo" || bind === "schedule.enabled" || bind.indexOf(".type") !== -1) {
            input.dataset.rerender = "1";
        }
        wrap.append(input, el("label", {class: "form-check-label ms-2", text: label}));
        return wrap;
    }

    function textInput(bind, value, options) {
        const attrs = Object.assign({class: "form-control", type: "text", "data-bind": bind}, options || {});
        const input = el("input", attrs);
        input.value = value == null ? "" : String(value);
        return input;
    }

    function textArea(bind, value, rows, placeholder) {
        const area = el("textarea", {
            class: "form-control",
            rows: String(rows || 3),
            "data-bind": bind,
            placeholder: placeholder || "",
        });
        area.value = value == null ? "" : String(value);
        return area;
    }

    function selectInput(bind, options, current, rerender, multiple) {
        const select = el("select", {class: "form-control", "data-bind": bind});
        if (multiple) {
            select.multiple = true;
        }
        if (rerender) {
            select.dataset.rerender = "1";
        }
        const selected = Array.isArray(current) ? current.map(String) : [String(current == null ? "" : current)];
        options.forEach(function addOption(option) {
            const item = el("option", {value: option.value, text: option.label});
            if (selected.includes(String(option.value))) {
                item.selected = true;
            }
            select.append(item);
        });
        return select;
    }

    function actionButton(action, label, data) {
        const button = el("button", {type: "button", class: "btn btn-sm btn-outline-secondary", text: label, "data-action": action});
        Object.entries(data || {}).forEach(function setData(entry) {
            button.dataset[entry[0]] = String(entry[1]);
        });
        return button;
    }

    function buttonRow(items) {
        const row = el("div", {class: "btn-group btn-group-sm"});
        items.forEach(function addButton(item) {
            row.append(actionButton(item[0], item[1], item[2]));
        });
        return row;
    }

    function wrapCol(control, className) {
        const col = el("div", {class: className});
        col.append(control);
        return col;
    }

    function el(tag, attrs) {
        const node = document.createElement(tag);
        Object.entries(attrs || {}).forEach(function setAttr(entry) {
            const key = entry[0];
            const value = entry[1];
            if (value == null || value === false) {
                return;
            }
            if (key === "class") {
                node.className = value;
            } else if (key === "text") {
                node.textContent = value;
            } else if (key === "type") {
                node.type = value;
            } else {
                node.setAttribute(key, value);
            }
        });
        return node;
    }
})();
