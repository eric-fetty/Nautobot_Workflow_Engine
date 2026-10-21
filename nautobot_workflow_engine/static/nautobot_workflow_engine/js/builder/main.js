import { createApi } from "./api.js";
import { setJinjaServerError } from "./jinja.js";
import { createLabels } from "./schema.js";
import { errorText, renderRail, renderSection } from "./sections.js";
import {
    blankAction,
    blankInput,
    createStore,
    fingerprint,
    fromDefinition,
    referencesTo,
    setPath,
    slugify,
    toBindPath,
} from "./store.js";

const configNode = document.getElementById("workflow-builder-config");
const config = configNode ? JSON.parse(configNode.textContent) : {};
const app = document.getElementById("builder-app");
const api = createApi(config);
const state = createStore(config.payload || {});
if (!state.payload.schedule) {
    state.payload.schedule = { enabled: false };
}
if (!state.payload.actions) {
    state.payload.actions = { pre: [], main: [], post: [] };
}
state.payload.inputs = state.payload.inputs || [];
state.payload.source_repo = state.payload.source_repo || "";
let labels = createLabels(null);
let section = (location.hash || "#details").replace("#", "");
if (!["details", "inputs", "steps", "review"].includes(section)) {
    section = "details";
}
let savedFingerprint = fingerprint(state.payload);
let dirty = false;
let saveAttempted = false;
let touched = new Set();
let clientErrors = [];
let serverErrors = [];
let yamlText = "";
let undo = null;
let pendingLeave = null;
let validateTimer = null;

const readOnly = Boolean(config.read_only);

boot();

async function boot() {
    try {
        const schema = await api.send(config.schema_url);
        if (schema.ok) {
            labels = createLabels(schema.data);
        }
    } catch (error) {
        labels = createLabels(null);
    }
    render();
    bind();
    if (config.refresh_url) {
        refreshGit();
    }
    offerRecovery();
    window.setInterval(snapshot, 5000);
    scheduleValidate();
}

function bind() {
    app.addEventListener("input", onEdit);
    app.addEventListener("change", onEdit);
    app.addEventListener("click", onClick);
    app.addEventListener("keydown", onKey);
    document.getElementById("builder-save-draft").addEventListener("click", () => save("draft"));
    document.getElementById("builder-publish").addEventListener("click", () => save("publish"));
    document.getElementById("builder-discard").addEventListener("click", discard);
    document.getElementById("builder-retry-now").addEventListener("click", retryNow);
    document.getElementById("builder-stale-reload").addEventListener("click", () => location.reload());
    document.getElementById("builder-stale-download").addEventListener("click", downloadYaml);
    document.getElementById("builder-confirm-cancel").addEventListener("click", () => hide("builder-confirm-dialog"));
    document.getElementById("builder-unsaved-stay").addEventListener("click", () => {
        pendingLeave = null;
        hide("builder-unsaved-dialog");
    });
    document.getElementById("builder-unsaved-discard").addEventListener("click", () => {
        dirty = false;
        const href = pendingLeave;
        pendingLeave = null;
        hide("builder-unsaved-dialog");
        if (href) {
            location.href = href;
        }
    });
    document.getElementById("builder-unsaved-save").addEventListener("click", async () => {
        const href = pendingLeave;
        hide("builder-unsaved-dialog");
        const ok = await save("draft");
        if (ok && href) {
            location.href = href;
        }
    });
    document.getElementById("builder-undo").addEventListener("click", restoreUndo);
    const adopt = document.getElementById("builder-adopt");
    if (adopt) {
        adopt.addEventListener("click", adoptWorkflow);
    }
    window.addEventListener("hashchange", () => {
        section = (location.hash || "#details").replace("#", "");
        render();
    });
    window.addEventListener("beforeunload", (event) => {
        if (dirty && !readOnly) {
            event.preventDefault();
            event.returnValue = "";
        }
    });
    document.addEventListener("click", (event) => {
        const link = event.target.closest("a[href]");
        if (!link || !dirty || link.target === "_blank" || readOnly) {
            return;
        }
        const href = link.getAttribute("href");
        if (!href || href.startsWith("#")) {
            return;
        }
        event.preventDefault();
        pendingLeave = link.href;
        show("builder-unsaved-dialog");
    });
    document.addEventListener("keydown", (event) => {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
            event.preventDefault();
            save("draft");
        }
    });
}

function onEdit(event) {
    const target = event.target;
    if (target.dataset && target.dataset.action === "change-type") {
        changeType(Number(target.dataset.index), target.value);
        return;
    }
    if (!target.dataset || !target.dataset.bind) {
        return;
    }
    touched.add(target.dataset.bind);
    let value = target.type === "checkbox" ? target.checked : target.value;
    if (target.dataset.bind === "schedule.allowed_states" || target.dataset.bind === "schedule.start_fields" || target.dataset.bind === "schedule.end_fields") {
        setPath(state.payload, target.dataset.bind, value);
    } else {
        setPath(state.payload, target.dataset.bind, value);
    }
    if (target.dataset.bind === "name" && !state.keyTouched && !config.published_number) {
        state.payload.key = slugify(value);
        const keyInput = document.getElementById("field-key");
        if (keyInput && document.activeElement !== keyInput) {
            keyInput.value = state.payload.key;
        }
        const title = document.getElementById("builder-title");
        if (title) {
            title.textContent = value || "New workflow";
        }
    }
    if (target.dataset.bind === "key") {
        state.keyTouched = true;
    }
    if (target.dataset.bind === "schedule.enabled") {
        markDirty();
        scheduleValidate();
        render();
        return;
    }
    if (target.dataset.bind.endsWith(".label") && target.dataset.bind.startsWith("inputs.")) {
        const index = Number(target.dataset.bind.split(".")[1]);
        const input = state.payload.inputs[index];
        if (input && !input._keyTouched) {
            input.key = slugify(input.label);
            const summary = document.querySelector(`#input-row-${index} [data-action="toggle-input"]`);
            if (summary) {
                const typeLabels = (labels.input.type && labels.input.type.labels) || {};
                summary.textContent = [input.key || "new-field", input.label || "Untitled", typeLabels[input.type] || input.type, input.required ? "Required" : ""].filter(Boolean).join(" · ");
            }
        }
    }
    markDirty();
    scheduleValidate();
}

function onClick(event) {
    const button = event.target.closest("[data-action]");
    if (!button) {
        return;
    }
    const action = button.dataset.action;
    if (readOnly && !["goto", "toggle-input", "toggle-step"].includes(action)) {
        return;
    }
    if (action === "goto") {
        location.hash = button.dataset.section;
        return;
    }
    if (action === "toggle-input") {
        state.expandedInput = state.expandedInput === Number(button.dataset.index) ? -1 : Number(button.dataset.index);
        render();
        return;
    }
    if (action === "toggle-step") {
        state.expandedStep = { phase: button.dataset.phase, index: Number(button.dataset.index) };
        render();
        return;
    }
    if (action === "add-input") {
        const picker = document.getElementById("add-input-type");
        state.payload.inputs.push(blankInput(button.dataset.type || (picker && picker.value) || "string"));
        state.expandedInput = state.payload.inputs.length - 1;
        markDirty();
        render();
        return;
    }
    if (action === "remove-input") {
        removeInput(Number(button.dataset.index));
        return;
    }
    if (action === "move-input") {
        moveInput(Number(button.dataset.index), Number(button.dataset.dir));
        return;
    }
    if (action === "change-type") {
        changeType(Number(button.dataset.index), button.value);
        return;
    }
    if (action === "add-choice") {
        state.payload.inputs[Number(button.dataset.index)].choices.push({ value: "", label: "" });
        markDirty();
        render();
        return;
    }
    if (action === "remove-choice") {
        state.payload.inputs[Number(button.dataset.index)].choices.splice(Number(button.dataset.choice), 1);
        markDirty();
        render();
        return;
    }
    if (action === "add-validator") {
        state.payload.inputs[Number(button.dataset.index)].validators.push({ regex: "", message: "", flags: [] });
        markDirty();
        render();
        return;
    }
    if (action === "remove-validator") {
        state.payload.inputs[Number(button.dataset.index)].validators.splice(Number(button.dataset.rule), 1);
        markDirty();
        render();
        return;
    }
    if (action === "toggle-flag") {
        const rule = state.payload.inputs[Number(button.dataset.index)].validators[Number(button.dataset.rule)];
        rule.flags = rule.flags || [];
        if (button.checked) {
            rule.flags.push(button.dataset.flag);
        } else {
            rule.flags = rule.flags.filter((flag) => flag !== button.dataset.flag);
        }
        markDirty();
        return;
    }
    if (action === "toggle-depends") {
        const input = state.payload.inputs[Number(button.dataset.index)];
        input.depends_on = input.depends_on || [];
        if (button.checked) {
            input.depends_on.push(button.dataset.key);
        } else {
            input.depends_on = input.depends_on.filter((key) => key !== button.dataset.key);
        }
        markDirty();
        return;
    }
    if (action === "clear-depends") {
        state.payload.inputs[Number(button.dataset.index)].depends_on = [];
        markDirty();
        render();
        return;
    }
    if (action === "rename-input") {
        renameInput(Number(button.dataset.index));
        return;
    }
    if (action === "add-step") {
        state.payload.actions[button.dataset.phase].push(blankAction(button.dataset.type, button.dataset.phase));
        state.expandedStep = { phase: button.dataset.phase, index: state.payload.actions[button.dataset.phase].length - 1 };
        markDirty();
        render();
        return;
    }
    if (action === "remove-step") {
        removeStep(button.dataset.phase, Number(button.dataset.index));
        return;
    }
    if (action === "add-kv") {
        const box = document.getElementById(`kv-new-${button.dataset.phase}-${button.dataset.index}-${button.dataset.field}`);
        const key = slugify(box && box.value) || (box && box.value);
        if (!key) {
            return;
        }
        const actionRow = state.payload.actions[button.dataset.phase][Number(button.dataset.index)];
        actionRow[button.dataset.field][key] = "";
        markDirty();
        render();
        return;
    }
    if (action === "remove-kv") {
        delete state.payload.actions[button.dataset.phase][Number(button.dataset.index)][button.dataset.field][button.dataset.key];
        markDirty();
        render();
    }
}

function onKey(event) {
    if (!event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) {
        return;
    }
    const row = event.target.closest("[id^='input-row-']");
    if (!row) {
        return;
    }
    event.preventDefault();
    const index = Number(row.id.replace("input-row-", ""));
    moveInput(index, event.key === "ArrowUp" ? -1 : 1);
}

function moveInput(index, direction) {
    const next = index + direction;
    if (next < 0 || next >= state.payload.inputs.length) {
        return;
    }
    const [item] = state.payload.inputs.splice(index, 1);
    state.payload.inputs.splice(next, 0, item);
    state.expandedInput = next;
    markDirty();
    render();
}

function changeType(index, type) {
    const input = state.payload.inputs[index];
    const loses = (input.choices || []).some((choice) => choice.value) && type !== "choice" && type !== "multichoice";
    const apply = () => {
        const fresh = blankInput(type);
        fresh.key = input.key;
        fresh.label = input.label;
        fresh.required = input.required;
        fresh.help_text = input.help_text;
        state.payload.inputs[index] = fresh;
        markDirty();
        render();
    };
    if (!loses) {
        apply();
        return;
    }
    confirmDialog("Change field type?", "Changing this field type drops its options.", "Change type", apply);
}

function removeInput(index) {
    const input = state.payload.inputs[index];
    const hits = referencesTo(state.payload, input.key);
    const apply = () => {
        const removed = state.payload.inputs.splice(index, 1)[0];
        state.expandedInput = Math.max(0, index - 1);
        rememberUndo("Input removed.", () => {
            state.payload.inputs.splice(index, 0, removed);
            state.expandedInput = index;
            render();
        });
        markDirty();
        render();
    };
    if (hits.length) {
        confirmDialog("Remove this input?", `Other fields use it: ${hits.join("; ")}. Those references will become errors.`, "Remove anyway", apply);
        return;
    }
    apply();
}

function removeStep(phase, index) {
    const action = state.payload.actions[phase][index];
    const apply = () => {
        const removed = state.payload.actions[phase].splice(index, 1)[0];
        rememberUndo("Step removed.", () => {
            state.payload.actions[phase].splice(index, 0, removed);
            render();
        });
        markDirty();
        render();
    };
    if (action.name) {
        confirmDialog("Remove this step?", `Remove ${action.name}? References to its results will become errors.`, "Remove", apply);
        return;
    }
    apply();
}

async function renameInput(index) {
    const input = state.payload.inputs[index];
    const box = document.getElementById(`field-input-${index}-rename`);
    const next = slugify(box && box.value);
    if (!next || next === input.key) {
        return;
    }
    const response = await api.send(config.refactor_url, {
        method: "POST",
        body: { definition: canonical(state.payload), op: "rename_input", from: input.key, to: next },
    });
    if (!response.ok) {
        toast("Couldn't rename that variable.");
        return;
    }
    const changes = (response.data.changes || []).map((change) => `${change.before} → ${change.after}`);
    confirmDialog(
        `Rename ${input.key} to ${next}?`,
        changes.length ? `This updates ${changes.length} references: ${changes.join("; ")}` : "Nothing else uses this name yet.",
        "Rename",
        () => {
            state.payload = fromDefinition(response.data.definition, state.payload);
            state.payload.inputs[index]._keyTouched = true;
            markDirty();
            render();
        }
    );
}

function render() {
    const rail = document.getElementById("builder-rail");
    const host = document.getElementById("builder-section");
    const active = document.activeElement && document.activeElement.id;
    rail.replaceChildren(renderRail(labels, state.payload, section, visibleErrors()));
    host.replaceChildren(
        renderSection(section, {
            labels,
            payload: state.payload,
            config,
            state,
            readOnly,
            errorFor: (path) => messageFor(path),
        })
    );
    const description = document.getElementById("field-description");
    if (description && !description.value) {
        description.value = state.payload.description || "";
    }
    paintBadges();
    paintSaveState();
    paintGit();
    if (active) {
        const again = document.getElementById(active);
        if (again) {
            again.focus();
        }
    }
    if (section === "inputs") {
        loadPreview();
    }
    if (section === "review") {
        const copy = document.getElementById("copy-yaml");
        if (copy) {
            copy.addEventListener("click", () => navigator.clipboard.writeText(yamlText || ""));
        }
        paintYaml();
    }
    document.querySelectorAll("[data-pointer]").forEach((node) => {
        const message = serverMessage(node.getAttribute("data-pointer"));
        if (node.classList.contains("nwe-jinja")) {
            setJinjaServerError(node, saveAttempted || touched.has(node.getAttribute("data-pointer")) ? message : "");
        }
    });
}

function visibleErrors() {
    return clientErrors.concat(serverErrors);
}

function messageFor(path) {
    if (!saveAttempted && !touched.has(path)) {
        return "";
    }
    const found = visibleErrors().find((item) => item.path === path);
    return found ? found.message : "";
}

function serverMessage(pointer) {
    const found = serverErrors.find((item) => item.pointer === pointer || item.path === pointer);
    return found ? found.message : "";
}

function mapServerErrors(items) {
    return (items || []).map((item) => {
        const path = toBindPath(item);
        return {
            pointer: item.pointer || "",
            path,
            section: item.section || sectionFor(path || item.pointer || ""),
            message: errorText(item.message || item.detail || "This value needs attention."),
        };
    });
}

function paintFieldErrors() {
    document.querySelectorAll("[data-error-for]").forEach((node) => {
        const control = node.dataset.errorFor ? document.getElementById(node.dataset.errorFor) : null;
        const path = control && control.dataset.bind;
        const message = path ? messageFor(path) : "";
        node.textContent = message;
        node.classList.toggle("d-block", Boolean(message));
        if (control) {
            control.classList.toggle("is-invalid", Boolean(message));
        }
    });
}

function paintBadges() {
    const host = document.getElementById("builder-badges");
    host.replaceChildren();
    if (config.draft_number || dirty) {
        host.append(badge("text-bg-secondary", config.draft_number ? `Draft r${config.draft_number}` : "Draft"));
    }
    if (config.published_number) {
        host.append(badge("text-bg-primary", `Published r${config.published_number}`));
    } else {
        host.append(badge("text-bg-light", "Not published"));
    }
    host.append(badge(state.payload.enabled ? "text-bg-success" : "text-bg-warning", state.payload.enabled ? "Available" : "Not available"));
    host.append(badge("text-bg-info", config.managed_by === "git" ? "Managed in Git" : "Managed by Nautobot"));
    host.append(badge(config.git_status === "not_synced" ? "text-bg-danger" : "text-bg-secondary", gitLabel(config.git_status)));
}

function badge(kind, text) {
    return Object.assign(document.createElement("span"), { className: `badge ${kind}`, textContent: text });
}

function gitLabel(status) {
    return {
        in_sync: "In Git",
        not_synced: "Not synced to Git",
        pending: "Syncing to Git",
        git_invalid: "Git file is invalid",
        disabled_repo: "Git publishing is off",
        not_configured: "Not in Git",
    }[status] || "Not in Git";
}

function paintGit() {
    const banner = document.getElementById("builder-not-synced");
    const text = document.getElementById("builder-not-synced-text");
    if (config.git_status === "not_synced") {
        banner.hidden = false;
        text.textContent = `Not synced to Git. ${config.git_error || ""} ${config.git_next_retry_at ? `Next retry ${config.git_next_retry_at}.` : ""}`.trim();
    } else {
        banner.hidden = true;
    }
}

function paintSaveState() {
    const node = document.getElementById("builder-save-state");
    const problems = visibleErrors().length;
    if (dirty) {
        const problemText = problems === 1 ? "1 problem" : `${problems} problems`;
        node.textContent = problems ? `Unsaved changes · ${problemText}` : "Unsaved changes";
    } else if (problems && saveAttempted) {
        node.textContent = `${problems} problems`;
    } else if (!config.key) {
        node.textContent = "Not saved yet";
    } else {
        node.textContent = "All changes saved";
    }
}

function markDirty() {
    dirty = fingerprint(state.payload) !== savedFingerprint;
    paintSaveState();
    paintBadges();
}

function collectClientErrors() {
    if (!saveAttempted) {
        clientErrors = [];
        return;
    }
    const errors = [];
    if (!state.payload.name) {
        errors.push({ path: "name", section: "details", message: "Name is required." });
    }
    if (!state.payload.key) {
        errors.push({ path: "key", section: "details", message: "Workflow ID is required." });
    }
    state.payload.inputs.forEach((input, index) => {
        if (!input.label) {
            errors.push({ path: `inputs.${index}.label`, section: "inputs", message: "Label is required." });
        }
        if ((input.type === "choice" || input.type === "multichoice") && !(input.choices || []).some((choice) => choice.value) && !input.choices_template) {
            errors.push({ path: `inputs.${index}.label`, section: "inputs", message: "Add at least one option." });
        }
    });
    ["pre", "main", "post"].forEach((phase) => {
        (state.payload.actions[phase] || []).forEach((action, index) => {
            if (!action.name) {
                errors.push({ path: `actions.${phase}.${index}.name`, section: "steps", message: "Step name is required." });
            }
            if (action.type === "awx" && !action.template_id) {
                errors.push({ path: `actions.${phase}.${index}.template_id`, section: "steps", message: "Enter the AWX job template number." });
            }
            if (action.type === "nautobot" && !action.script) {
                errors.push({ path: `actions.${phase}.${index}.script`, section: "steps", message: "Choose a script." });
            }
        });
    });
    clientErrors = errors;
}

function scheduleValidate() {
    window.clearTimeout(validateTimer);
    validateTimer = window.setTimeout(runValidate, 400);
}

async function runValidate() {
    const result = await api.validate(outgoingPayload());
    if (!result) {
        return;
    }
    if (!result.ok && result.status === 0) {
        if (section === "review") {
            const errors = document.getElementById("yaml-errors");
            if (errors) {
                errors.textContent = result.data.detail || "Couldn't check this workflow.";
            }
        }
        return;
    }
    const data = result.data || {};
    if (typeof data.yaml === "string") {
        yamlText = data.yaml;
    }
    serverErrors = mapServerErrors(data.errors);
    paintYaml();
    paintFieldErrors();
    paintSaveState();
    const rail = document.getElementById("builder-rail");
    if (rail) {
        rail.replaceChildren(renderRail(labels, state.payload, section, visibleErrors()));
    }
    document.querySelectorAll(".nwe-jinja").forEach((node) => {
        const message = serverMessage(node.getAttribute("data-pointer"));
        setJinjaServerError(node, message);
    });
    if (section === "inputs") {
        loadPreview();
    }
    const summary = document.getElementById("review-problems");
    if (summary) {
        const problems = visibleErrors();
        summary.className = problems.length ? "alert alert-warning" : "alert alert-success";
        const count = problems.length;
        summary.textContent = count === 1 ? "1 problem. It is next to its field." : count ? `${count} problems. Each one is next to its field.` : "No problems found.";
    }
}

function paintYaml() {
    const preview = document.getElementById("yaml-preview");
    const errors = document.getElementById("yaml-errors");
    if (preview) {
        preview.textContent = yamlText || "";
    }
    if (errors) {
        errors.replaceChildren();
        visibleErrors().forEach((item) => {
            const row = document.createElement("div");
            row.textContent = item.message;
            errors.append(row);
        });
    }
}

function sectionFor(pointer) {
    if (pointer.includes("input")) {
        return "inputs";
    }
    if (pointer.includes("action")) {
        return "steps";
    }
    return "details";
}

function outgoingPayload() {
    const payload = JSON.parse(JSON.stringify(state.payload));
    ["allowed_states", "start_fields", "end_fields"].forEach((key) => {
        const value = payload.schedule && payload.schedule[key];
        if (typeof value === "string") {
            payload.schedule[key] = value.split(",").map((item) => item.trim()).filter(Boolean);
        }
    });
    payload.inputs.forEach((input) => {
        if (input.choices_template) {
            input.choices = input.choices_template;
        }
        if (typeof input.default === "string" && input.default === "") {
            input.default = null;
        }
    });
    ["pre", "main", "post"].forEach((phase) => {
        (payload.actions[phase] || []).forEach((action) => {
            if (action.template_id !== "" && action.template_id != null && Number.isInteger(Number(action.template_id))) {
                action.template_id = Number(action.template_id);
            }
        });
    });
    return payload;
}

async function save(mode) {
    if (readOnly) {
        return false;
    }
    saveAttempted = true;
    collectClientErrors();
    const blocking = mode === "publish" ? clientErrors : clientErrors.filter((item) => item.path === "key" || item.path === "name");
    if (blocking.length) {
        render();
        scrollToError();
        return false;
    }
    document.getElementById("builder-save-state").textContent = "Saving…";
    const body = {
        payload: outgoingPayload(),
        base_revision: config.base_revision,
        base_git_commit: config.base_git_commit || "",
        base_content_hash: config.base_content_hash || "",
        repository: state.payload.source_repo || null,
        strict: mode !== "draft",
        enable: document.getElementById("field-enable")
            ? document.getElementById("field-enable").checked
            : Boolean(state.payload.enabled),
        commit_summary: (document.getElementById("field-summary") || {}).value || "",
    };
    let response;
    if (!config.key) {
        response = await api.send(config.create_api_url, { method: "POST", body });
        if (response.ok) {
            config.key = response.data.workflow.key;
            config.base_revision = response.data.revision.number;
            config.draft_number = response.data.revision.number;
            config.draft_url = config.publish_url_template.replace("__KEY__", config.key).replace("/publish/", "/draft/");
            config.publish_url = config.publish_url_template.replace("__KEY__", config.key);
            if (mode === "publish") {
                response = await api.send(config.publish_url, { method: "POST", body: { ...body, base_revision: config.base_revision } });
            }
        }
    } else if (mode === "draft") {
        response = await api.send(config.draft_url, { method: "PUT", body });
    } else {
        response = await api.send(config.publish_url, { method: "POST", body });
    }
    if (response.status === 409) {
        showStale(response.data);
        return false;
    }
    if (!config.is_edit && config.key) {
        const editUrl = config.edit_url_template.replace("__KEY__", config.key);
        history.replaceState(null, "", editUrl);
        config.is_edit = true;
    }
    if (!response.ok) {
        serverErrors = mapServerErrors(
            response.data.errors || [{ message: response.data.detail || "Couldn't save this workflow.", section: "details" }]
        );
        render();
        scrollToError();
        return false;
    }
    const revision = response.data.revision || (response.data.workflow && response.data.revision);
    if (revision && revision.number) {
        config.base_revision = revision.number;
        if (mode === "draft") {
            config.draft_number = revision.number;
        } else {
            config.published_number = revision.number;
            config.draft_number = null;
        }
    }
    if (response.data.git && response.data.git.status) {
        config.git_status = response.data.git.status;
    }
    savedFingerprint = fingerprint(state.payload);
    dirty = false;
    localStorage.removeItem(storageKey());
    toast(mode === "draft" ? "Draft saved." : "Published.");
    if (mode === "publish" && config.key) {
        location.href = config.edit_url_template.replace("__KEY__", config.key);
        return true;
    }
    if (!config.is_edit && config.key) {
        location.href = config.edit_url_template.replace("__KEY__", config.key);
    }
    render();
    return true;
}

function scrollToError() {
    const first = visibleErrors()[0];
    if (first && first.section && first.section !== section && ["details", "inputs", "steps", "review"].includes(first.section)) {
        location.hash = first.section;
    }
    window.setTimeout(() => {
        const node = document.querySelector(".is-invalid, .invalid-feedback.d-block");
        if (node) {
            node.scrollIntoView({ block: "center" });
            const focusable = node.matches("input, select, textarea") ? node : node.parentElement && node.parentElement.querySelector("input, select, textarea");
            if (focusable && focusable.focus) {
                focusable.focus();
            }
        }
    }, 0);
}

async function adoptWorkflow() {
    if (!config.adopt_url) {
        return;
    }
    const first = await api.send(config.adopt_url, { method: "POST", body: { confirm: false } });
    if (!first.ok) {
        toast((first.data && (first.data.detail || first.data.message)) || "Couldn't adopt this workflow.");
        return;
    }
    if (first.data && first.data.adopted) {
        location.reload();
        return;
    }
    const includes = (first.data && first.data.includes) || [];
    const warning = (first.data && first.data.warning) || "It becomes editable in Nautobot. The next publish updates the Git file.";
    const body = includes.length ? `${warning} Included files: ${includes.join(", ")}` : warning;
    confirmDialog("Adopt this workflow?", body, "Adopt", async () => {
        const second = await api.send(config.adopt_url, { method: "POST", body: { confirm: true } });
        if (second.ok && second.data && second.data.adopted !== false) {
            location.reload();
            return;
        }
        toast((second.data && (second.data.detail || second.data.message)) || "Couldn't adopt this workflow.");
    });
}

function showStale(data) {
    const detail = document.getElementById("builder-stale-detail");
    detail.textContent = data.detail || "Updated in Git. Reload to continue.";
    show("builder-stale-dialog");
}

async function refreshGit() {
    const banner = document.getElementById("builder-git-check");
    banner.hidden = false;
    const response = await api.send(config.refresh_url, { method: "POST", body: {} });
    banner.hidden = true;
    if (!response.ok || (response.data && response.data.state === "unverified")) {
        const note = document.getElementById("builder-git-unverified");
        note.hidden = false;
        note.textContent = "Couldn't check Git for updates. Your save will still be checked.";
    }
}

async function retryNow() {
    if (!config.push_url) {
        return;
    }
    const response = await api.send(config.push_url, { method: "POST", body: {} });
    if (response.ok && response.data) {
        config.git_status = response.data.git_status || config.git_status;
        config.git_error = response.data.git_error || "";
        config.git_next_retry_at = response.data.git_next_retry_at || "";
        paintGit();
        paintBadges();
        return;
    }
    toast("Git retry failed. The workflow is still not synced.");
}

async function loadPreview() {
    const host = document.getElementById("launch-preview");
    if (!host || !config.launch_preview_url) {
        return;
    }
    const response = await fetch(config.launch_preview_url, {
        method: "POST",
        credentials: "same-origin",
        headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": config.csrf_token || "",
        },
        body: JSON.stringify({ payload: outgoingPayload(), strict: false }),
    });
    const html = await response.text();
    if (!response.ok || !document.getElementById("launch-preview")) {
        return;
    }
    const parsed = new DOMParser().parseFromString(html, "text/html");
    document.getElementById("launch-preview").replaceChildren(...parsed.body.childNodes);
}

function discard() {
    confirmDialog("Discard changes?", "This throws away edits that are not saved.", "Discard", () => {
        state.payload = JSON.parse(savedFingerprint);
        dirty = false;
        render();
    });
}

function downloadYaml() {
    const blob = new Blob([yamlText || ""], { type: "text/yaml" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${state.payload.key || "workflow"}.yaml`;
    link.click();
}

function snapshot() {
    if (!dirty) {
        return;
    }
    localStorage.setItem(storageKey(), JSON.stringify({ payload: state.payload, base_revision: config.base_revision, ts: Date.now() }));
}

function offerRecovery() {
    const raw = localStorage.getItem(storageKey());
    if (!raw) {
        return;
    }
    try {
        const stored = JSON.parse(raw);
        if (stored && stored.payload && stored.ts && fingerprint(stored.payload) !== savedFingerprint) {
            const when = new Date(stored.ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
            confirmDialog("Restore unsaved changes?", `A copy from ${when} is saved in this browser.`, "Restore", () => {
                state.payload = stored.payload;
                markDirty();
                render();
            });
        }
    } catch (error) {
        localStorage.removeItem(storageKey());
    }
}

function storageKey() {
    return `nwe-draft:${config.key || "new"}`;
}

function rememberUndo(text, action) {
    undo = action;
    const button = document.getElementById("builder-undo");
    button.hidden = false;
    toast(text);
    window.setTimeout(() => {
        undo = null;
        button.hidden = true;
    }, 30000);
}

function restoreUndo() {
    if (undo) {
        undo();
        undo = null;
        document.getElementById("builder-undo").hidden = true;
    }
}

function toast(text) {
    const node = document.getElementById("builder-toast");
    document.getElementById("builder-toast-text").textContent = text;
    if (window.bootstrap && window.bootstrap.Toast) {
        window.bootstrap.Toast.getOrCreateInstance(node, { delay: 30000 }).show();
    }
}

function confirmDialog(title, body, okLabel, onOk) {
    document.getElementById("builder-confirm-title").textContent = title;
    document.getElementById("builder-confirm-body").textContent = body;
    const ok = document.getElementById("builder-confirm-ok");
    ok.textContent = okLabel;
    ok.onclick = () => {
        hide("builder-confirm-dialog");
        onOk();
    };
    show("builder-confirm-dialog");
}

function show(id) {
    const node = document.getElementById(id);
    if (window.bootstrap && window.bootstrap.Modal) {
        window.bootstrap.Modal.getOrCreateInstance(node).show();
        return;
    }
    node.classList.add("show", "d-block");
}

function hide(id) {
    const node = document.getElementById(id);
    if (window.bootstrap && window.bootstrap.Modal) {
        const modal = window.bootstrap.Modal.getInstance(node);
        if (modal) {
            modal.hide();
        }
        return;
    }
    node.classList.remove("show", "d-block");
}

function canonical() {
    const copy = outgoingPayload();
    const definition = {
        key: copy.key,
        name: copy.name,
        description: copy.description,
        category: copy.category,
        approval_required: copy.approval_required,
        inputs: copy.inputs,
        pre_actions: copy.actions.pre,
        actions: copy.actions.main,
        post_actions: copy.actions.post,
    };
    if (copy.schedule && copy.schedule.enabled) {
        definition.schedule = { servicenow: { ...copy.schedule, enabled: undefined } };
    }
    return definition;
}
