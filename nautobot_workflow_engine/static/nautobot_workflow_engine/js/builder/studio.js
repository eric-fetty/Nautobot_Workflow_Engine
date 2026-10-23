/**Workflow builder: one shared draft, five steps, live preview. */

import { blankAction, blankInput, referencesTo, setPath, slugify } from "./store.js";
import { createField } from "../shared/fields.js";
import { parseWhen, STEP_TESTS, writeWhen as writeCondition } from "../shared/conditions.js";
import { draftSnapshot, mergeIssues, validationGate } from "../shared/issues.js";
import { jinjaEditor } from "../shared/jinja.js";
import { fixedText, fixedValue, inputRef, isTemplate, paramMode } from "../shared/template.js";

const configNode = document.getElementById("workflow-builder-config");
const config = configNode ? JSON.parse(configNode.textContent) : {};
const payload = config.payload || { inputs: [], actions: { pre: [], main: [], post: [] }, schedule: {} };
payload.inputs = payload.inputs || [];
payload.actions = payload.actions || { pre: [], main: [], post: [] };
payload.schedule = payload.schedule || { enabled: false, allowed_states: [], start_fields: [], end_fields: [] };
payload.inputs.forEach((input) => {
    input._keyTouched = true;
    input.filter_rows = input.filter_rows || [];
    input.filter_mode = input.filter_mode || "visual";
    if (input.csv_import === undefined) input.csv_import = true;
});
["pre", "main", "post"].forEach((phase) => {
    (payload.actions[phase] || []).forEach((action) => {
        if (!action.label) action.label = action.name || "";
        // The key keeps following the label until someone edits it by hand.
        action._origName = action.name || "";
        action._nameTouched = Boolean(action.name) && action.name !== keyFrom(action.label);
    });
});

const SECTIONS = [
    ["details", "Details"],
    ["inputs", "Inputs"],
    ["steps", "Steps"],
    ["schedule", "Schedule & approval"],
    ["review", "Review & publish"],
];
const PHASES = [
    ["pre", "Before", "Runs first, and again when a scheduled run is booked. If a step fails, the run stops here."],
    ["main", "Main", "Runs in order. If a step fails, it's recorded and the remaining main steps still run."],
    ["post", "Always run at the end", "Runs last, even after failures. Use it for cleanup and notes. A failure here doesn't fail the run."],
];
const TYPES = [
    ["string", "Text"],
    ["integer", "Whole number"],
    ["boolean", "Yes / No"],
    ["choice", "Pick one"],
    ["multichoice", "Pick many"],
    ["object", "Nautobot object"],
    ["multiobject", "Nautobot objects"],
    ["list", "List of text"],
    ["listdict", "Table"],
];
const OPS = ["is", "is not", "is one of", "contains", "starts with", "is empty"];
const TYPE_LABEL = Object.fromEntries(TYPES);

const state = {
    section: "details",
    yaml: false,
    menu: false,
    addType: false,
    openInput: 0,
    openStep: "",
    previewTab: "form",
    dirty: false,
    save: config.draft_at ? "saved" : "",
    savedAt: config.draft_at ? Date.parse(config.draft_at) : 0,
    yamlText: "",
    yamlError: "",
    serverErrors: [],
    note: "",
    enableOnPublish: true,
    gitStatus: config.git_status || "",
    gitError: config.git_error || "",
    stale: config.stale || null,
    compare: false,
    counts: {},
    preview: {},
    modelFields: {},
    modelOpen: -1,
    modelQuery: "",
    yamlLine: 0,
};

const app = document.getElementById("builder-app");
document.body.classList.add("nwe-studio-body");
// The sidebar is left exactly as Nautobot draws it, collapsed or not, so it always navigates.

function h(tag, attrs, kids) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
        if (value == null || value === false) return;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = String(value);
        else if (key === "checked") node.checked = Boolean(value);
        else if (key === "value") node.value = value == null ? "" : String(value);
        else if (key === "disabled") node.disabled = Boolean(value);
        else if (key === "multiple") node.multiple = Boolean(value);
        else if (key === "selected") {
            if (value) node.setAttribute("selected", "");
        } else node.setAttribute(key, value === true ? "" : String(value));
    });
    (kids || []).forEach((kid) => {
        if (kid == null || kid === false) return;
        node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    });
    return node;
}

function field(label, control, help) {
    return h("div", { class: "mb-3" }, [
        label ? h("label", { class: "form-label", text: label }) : null,
        control,
        help ? h("div", { class: "form-text", text: help }) : null,
    ]);
}

function keyFrom(text) {
    return String(text || "")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "")
        .slice(0, 50);
}

function inputLocked(input) {
    return (config.published_input_keys || []).includes(input.key) && input._keyTouched;
}

/** A step's key is fixed once a published version uses it: run history and later steps refer to it. */
function stepLocked(action) {
    return Boolean(action._origName) && (config.published_step_names || []).includes(action._origName);
}

/** A deep copy of an input or step, without the builder's own bookkeeping timers. */
function cloneRow(row) {
    return JSON.parse(JSON.stringify(row, (key, value) => (key.endsWith("Timer") ? undefined : value)));
}

/** `base`, or `base_2`, `base_3`… so it doesn't clash with `taken`. */
function uniqueKey(base, taken) {
    const root = base || "step";
    const used = new Set(taken.filter(Boolean));
    if (!used.has(root)) return root;
    let n = 2;
    while (used.has(`${root}_${n}`)) n += 1;
    return `${root}_${n}`;
}

/** Labels of the other steps whose parameters or condition read `results.<name>`. */
function stepReferences(name, self) {
    if (!name) return [];
    const pattern = new RegExp(`\\bresults\\.${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`);
    return allSteps()
        .filter((action) => action !== self && pattern.test(JSON.stringify([action.when || "", action.with || "", action.extra_vars || ""])))
        .map((action) => `“${action.label || action.name}”`);
}

/** Keep an unpublished step's key in step with its label, and point later references at the new key. */
function followLabel(phase, index) {
    const action = payload.actions[phase][index];
    if (!action || action._nameTouched || stepLocked(action)) return;
    const others = allSteps().filter((item) => item !== action).map((item) => item.name);
    const next = uniqueKey(keyFrom(action.label) || "step", others);
    if (next === action.name) return;
    renameStepRefs(action.name, next, action);
    action.name = next;
    paintKey(phase, index);
}

function paintKey(phase, index) {
    const action = payload.actions[phase][index];
    const id = `${phase}:${index}`;
    const box = app.querySelector(`[data-step-key="${id}"]`);
    if (box && box !== document.activeElement) box.value = action.name || "";
    const text = app.querySelector(`[data-step-key-text="${id}"]`);
    if (text) text.textContent = action.name || "—";
    const help = app.querySelector(`[data-step-key-help="${id}"]`);
    if (help) help.replaceChildren(...keyHelp(action, phase, index));
}

function renameStepRefs(from, to, self) {
    if (!from || from === to) return;
    const pattern = new RegExp(`\\bresults\\.${from}\\b`, "g");
    const swap = (value) => {
        if (typeof value === "string") return value.replace(pattern, `results.${to}`);
        if (Array.isArray(value)) return value.map(swap);
        if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, swap(item)]));
        return value;
    };
    allSteps().forEach((action) => {
        if (action === self) return;
        if (action.when) action.when = swap(action.when);
        if (action.with) action.with = swap(action.with);
        if (action.extra_vars) action.extra_vars = swap(action.extra_vars);
    });
}

/**
 * Switch a step between a Nautobot script and an AWX job. The label, key,
 * condition and phase (which sets what a failure does) stay; the script or
 * template and its parameters can't carry over, so ask before dropping them.
 */
function sentence(text) {
    return text ? text[0].toUpperCase() + text.slice(1) : text;
}

function changeStepType(phase, index, type) {
    const row = payload.actions[phase][index];
    if (!row || (row.type || "nautobot") === type) return;
    const before = cloneRow(row);
    const params = paramEntries(row).length;
    const raw = row.type === "awx" ? row.extra_vars : row.with;
    const lose = [];
    if (row.type === "awx" ? row.template_name || row.template_id : row.script) lose.push(row.type === "awx" ? "the job template" : "the script");
    if (params) lose.push(row.type === "awx" ? `${params} extra variable${params === 1 ? "" : "s"}` : `${params} parameter${params === 1 ? "" : "s"}`);
    else if (typeof raw === "string" && raw.trim()) lose.push("the parameter template");
    const target = type === "awx" ? "an AWX job" : "a Nautobot script";
    const label = row.label || row.name || "this step";
    if (lose.length && !window.confirm(`Change “${label}” to ${target}?\n\n${sentence(lose.join(" and "))} will be removed. The name, key, condition and phase stay.`)) {
        renderEditor();
        return;
    }
    row.type = type;
    row.script = "";
    row.with = {};
    row.template_name = "";
    row.template_id = null;
    row.extra_vars = {};
    row.monitor = type === "awx" ? true : null;
    delete row._modes;
    renderAll();
    touch();
    if (lose.length) {
        toast(`Changed to ${target}.`, () => {
            payload.actions[phase][index] = before;
            renderAll();
            touch();
        });
    }
}

function allSteps() {
    return PHASES.flatMap(([phase]) => payload.actions[phase] || []);
}

function localIssues() {
    const buckets = { details: [], inputs: [], steps: [], schedule: [], review: [] };
    if (!payload.name) buckets.details.push(["Name is required.", "name"]);
    if (!payload.key) buckets.details.push(["Workflow ID is required.", "key"]);
    const repoProblem = selectedRepo()?.problem;
    if (repoProblem) buckets.details.push([repoProblem, "source_repo"]);
    payload.inputs.forEach((input, index) => {
        const who = input.label || `Input ${index + 1}`;
        if (!input.label) buckets.inputs.push([`${who}: add a label.`, `inputs.${index}.label`]);
        if (!input.key) buckets.inputs.push([`${who}: add a variable name.`, `inputs.${index}.key`]);
        if ((input.type === "object" || input.type === "multiobject") && !input.model) {
            buckets.inputs.push([`${who}: choose an object type.`, `inputs.${index}.model`]);
        }
        if (input.type === "listdict" && !(input.columns || []).length) {
            buckets.inputs.push(`${who}: add a column.`);
        }
    });
    PHASES.forEach(([phase]) => {
        (payload.actions[phase] || []).forEach((action, index) => {
            const who = action.label || action.name || "Step";
            if (!action.name) buckets.steps.push([`${who}: add a step key.`, `steps.${phase}.${index}.name`]);
            if (action.type === "awx" && !action.template_name && !action.template_id) buckets.steps.push([`${who}: choose an AWX job template.`, `steps.${phase}.${index}.template_id`]);
        });
    });
    if (payload.schedule.enabled && !payload.schedule.integration_name) {
        buckets.schedule.push(["Choose a ServiceNow connection.", "schedule.integration_name"]);
    }
    const zone = payload.schedule.timezone;
    if (payload.schedule.enabled && zone && config.timezones?.length && zone.toUpperCase() !== "UTC" && !config.timezones.some((item) => item.name === zone)) {
        buckets.schedule.push([`“${zone}” isn’t a timezone. Choose one from the list.`, "schedule.timezone"]);
    }
    return mergeIssues(buckets, state.serverErrors);
}

function selectedRepo() {
    const id = String(payload.source_repo || "");
    return id ? (config.repositories || []).find((repo) => String(repo.id) === id) : null;
}

function issueCount(buckets) {
    return Object.values(buckets).reduce((sum, items) => sum + items.length, 0);
}

function differs() {
    if (state.yamlText && config.published_yaml && state.yamlText.trim() === String(config.published_yaml).trim()) {
        return false;
    }
    if (!config.published_number) return Boolean(payload.name || payload.key || payload.inputs.length || allSteps().length);
    return Boolean(state.dirty || config.has_draft_changes);
}

function publishBlock() {
    if (state.stale) return { message: "Newer version in Git", section: "review" };
    const buckets = localIssues();
    const hit = SECTIONS.map(([id]) => id).find((section) => buckets[section].length);
    if (hit) {
        const total = Object.values(buckets).reduce((sum, list) => sum + list.length, 0);
        return { message: `${total} issue${total === 1 ? "" : "s"} to fix`, section: hit, issues: total };
    }
    if (!differs()) return { message: "No changes to publish yet", section: "review" };
    return null;
}

const STATUS_HELP = {
    new: "Only editors can see this draft. Publish it so people can run it.",
    draft: "People run the published workflow. Your changes save to a draft that only editors see until you publish them.",
    live: "People run what you see here. Changes you make save to a draft until you publish them.",
};

/** The header pill: no version numbers here, those live in Versions. */
function statusText() {
    if (!config.published_number) return ["Draft", "new"];
    if (differs() || state.dirty) return ["Published · unpublished changes", "draft"];
    return ["Published", "ok"];
}

function statusHelp(kind) {
    const help = config.status_help || STATUS_HELP;
    return help[kind === "ok" ? "live" : kind] || "";
}

function publishText() {
    return config.published_number ? "Publish changes" : "Publish";
}

async function api(url, body, method) {
    let response;
    try {
        response = await fetch(url, {
            method: method || "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": config.csrf_token || "",
            },
            body: method === "GET" || method === "DELETE" ? undefined : JSON.stringify(body || {}),
        });
    } catch (error) {
        return { ok: false, status: 0, data: {} };
    }
    const text = await response.text();
    let data = {};
    if (text) {
        try {
            data = JSON.parse(text);
        } catch (error) {
            // An HTML error page is not a message for people. Callers fall back to plain words.
            data = {};
        }
    }
    return { ok: response.ok, status: response.status, data };
}

function toast(message, undo) {
    const node = document.getElementById("builder-toast-text");
    const toastNode = document.getElementById("builder-toast");
    if (!node || !toastNode || !window.bootstrap) return;
    const instance = window.bootstrap.Toast.getOrCreateInstance(toastNode, { delay: undo ? 7000 : 4000 });
    node.replaceChildren(h("span", { text: message }));
    if (undo) {
        node.append(h("button", {
            type: "button",
            class: "nwe-link ms-2",
            onclick: () => {
                instance.hide();
                undo();
            },
        }, [icon("undo"), "Undo"]));
    }
    instance.show();
}
let saveTimer = 0;
const gate = validationGate();
let lastSnapshot = draftSnapshot(payload);

/**
 * Every change to the draft goes through here: it repaints the issue markers
 * at once (local checks) and schedules a save, which reruns server validation.
 */
function touch() {
    if (config.read_only) return;
    lastSnapshot = draftSnapshot(payload);
    state.dirty = true;
    state.save = "dirty";
    paintChrome();
    clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => {
        saveTimer = 0;
        saveDraft();
    }, 800);
}

/** Catch changes made by handlers that forgot to call touch() (reorder, delete, undo, …). */
function noticeChanges() {
    if (config.read_only) return;
    if (draftSnapshot(payload) !== lastSnapshot) touch();
}

async function saveDraft() {
    if (config.read_only) return;
    if (!payload.key && payload.name) payload.key = slugify(payload.name);
    if (!payload.key || !payload.name) {
        // Nothing can be saved without a name, but the checks still have to follow the draft.
        state.save = "unsaved";
        paintSave();
        refreshYaml();
        return;
    }
    const ticket = gate.send(draftSnapshot(payload));
    state.save = "saving";
    paintSave();
    const body = {
        payload,
        strict: false,
        base_revision: config.base_revision,
        base_git_commit: config.base_git_commit || "",
        base_content_hash: config.base_content_hash || "",
    };
    let result;
    if (!config.key) {
        result = await api(config.create_api_url, body);
        if (result.ok && result.data.workflow) {
            const next = config.edit_url_template.replace("__KEY__", result.data.workflow.key);
            window.location.assign(next);
            return;
        }
    } else {
        result = await api(config.draft_url, body, "PUT");
    }
    if (!result || !result.ok) {
        state.save = "error";
        if (result && result.status === 400 && gate.accept(ticket, draftSnapshot(payload))) {
            state.serverErrors = (result.data && result.data.errors) || state.serverErrors;
            paintChrome();
        }
        paintSave();
        if (result && result.status === 409) toast(result.data.detail || "Reload to continue.");
        return;
    }
    if (result.data.revision && result.data.revision.number != null) {
        config.base_revision = result.data.revision.number;
        config.draft_by = config.username;
    }
    if (gate.accept(ticket, draftSnapshot(payload))) state.serverErrors = result.data.errors || [];
    state.save = state.save === "saving" ? "saved" : state.save;
    state.savedAt = Date.now();
    config.has_draft_changes = true;
    paintChrome();
    refreshYaml();
}

async function refreshYaml() {
    const ticket = gate.send(draftSnapshot(payload));
    const result = await api(config.validate_url, { payload, strict: false, repository: payload.source_repo || null });
    if (!result.ok || !gate.accept(ticket, draftSnapshot(payload))) return;
    state.yamlText = result.data.yaml || "";
    state.serverErrors = result.data.errors || [];
    paintChrome();
}

function paintSave() {
    const node = document.getElementById("nwe-save");
    if (!node) return;
    node.replaceChildren();
    if (state.save === "saving") {
        node.append(h("span", { text: "Saving…" }));
        return;
    }
    if (state.save === "unsaved") {
        node.append(h("span", { class: "text-danger", text: "Not saved: give it a name first" }));
        return;
    }
    if (state.save === "error") {
        node.append(h("span", { class: "text-danger", text: "Couldn’t save. Your next change tries again." }));
        return;
    }
    if (config.draft_by && config.draft_by !== config.username && state.save !== "dirty") {
        const minutes = Math.max(1, Math.round((Date.now() - Date.parse(config.draft_at || Date.now())) / 60000));
        node.append(h("span", { text: `${config.draft_by} edited this draft ${minutes} min ago` }));
        return;
    }
    if (state.save === "saved" || config.draft_at) {
        const recent = state.savedAt && Date.now() - state.savedAt < 8000;
        node.append(h("span", { text: `Draft saved · ${recent ? "just now" : savedAgo()}` }));
    }
}

function savedAgo() {
    const then = state.savedAt || Date.parse(config.draft_at || "") || Date.now();
    const minutes = Math.max(0, Math.round((Date.now() - then) / 60000));
    if (minutes < 1) return "just now";
    if (minutes === 1) return "1 min ago";
    return `${minutes} min ago`;
}

function paintChrome() {
    renderHeader();
    renderStepper();
    renderPreview();
}

function renderAll() {
    renderHeader();
    renderBanner();
    renderStepper();
    renderEditor();
    renderPreview();
}

function scriptName(path) {
    const found = (config.scripts || []).find((item) => item.path === path);
    if (found) return found.name;
    if (!path || !String(path).endsWith(".py")) return "";
    const stem = String(path).split("/").pop().replace(/\.py$/, "").replace(/[_-]+/g, " ");
    return stem.charAt(0).toUpperCase() + stem.slice(1);
}

function icon(name, extra) {
    return h("i", { class: `mdi mdi-${name}${extra ? ` ${extra}` : ""}`, "aria-hidden": "true" });
}

const TYPE_ICONS = {
    string: "form-textbox",
    integer: "numeric",
    boolean: "toggle-switch-outline",
    choice: "radiobox-marked",
    multichoice: "checkbox-marked-outline",
    object: "cube-outline",
    multiobject: "cube-outline",
    list: "format-list-bulleted",
    listdict: "table",
    nautobot: "script-text-outline",
    awx: "ansible",
};

function tile(type, small) {
    return h("span", { class: `nwe-tile t-${type}${small ? " sm" : ""}`, "aria-hidden": "true" }, [icon(TYPE_ICONS[type] || "circle-small")]);
}

/** A checkbox chip, as on the launch form. */
function checkChip(attrs, on, text) {
    return h("label", { class: `nwe-ck${on ? " on" : ""}` }, [
        h("input", { type: "checkbox", ...attrs, checked: on }),
        h("span", { class: "bx", "aria-hidden": "true" }, [on ? icon("check-bold") : null]),
        h("span", { text }),
    ]);
}

/** The app's switch: accent when on, in both themes. */
function switchRow(attrs, title, help) {
    return h("label", { class: "nwe-switch" }, [
        h("input", { type: "checkbox", role: "switch", ...attrs }),
        h("span", { class: "nwe-switch-ui", "aria-hidden": "true" }),
        h("span", { class: "nwe-switch-copy" }, [h("b", { text: title }), help ? h("small", { text: help }) : null]),
    ]);
}

function modelMeta(model) {
    return (state.modelFields || {})[model] || [];
}

function fieldMeta(model, key) {
    return modelMeta(model).find((item) => item.key === key) || null;
}

function ensureFields(model) {
    if (!model || state.modelFields[model] || state.fieldLoads?.[model]) return;
    state.fieldLoads = state.fieldLoads || {};
    state.fieldLoads[model] = true;
    api(config.filter_fields_url, { model }).then((result) => {
        state.modelFields[model] = result.ok ? result.data.fields || [] : [];
        state.modelExamples = state.modelExamples || {};
        state.modelExamples[model] = result.ok ? result.data.examples || [] : [];
        renderEditor();
        renderPreview();
    });
}

function inputByKey(key) {
    return payload.inputs.find((input) => input.key === key);
}

function renderHeader() {
    const header = document.getElementById("studio-header");
    const [text, kind] = statusText();
    const block = publishBlock();
    const git = gitIndicator();
    header.replaceChildren(
        h("a", { class: "nwe-iconbtn nwe-back", href: config.list_url, title: "Back to workflows", "aria-label": "Back to workflows" }, [icon("arrow-left")]),
        h("div", { class: "nwe-ident" }, [
            h("span", { class: "nwe-tile t-workflow", "aria-hidden": "true" }, [icon("transit-connection-variant")]),
            h("div", {}, [
                h("h1", { class: "nwe-title", id: "nwe-title", text: payload.name || "Untitled workflow" }),
                h("div", { class: "nwe-state" }, [
                    h("span", { class: `nwe-pill nwe-status ${kind === "ok" ? "nwe-pill-ok" : kind === "new" ? "nwe-pill-new" : "nwe-pill-draft"}`, tabindex: "0", "aria-describedby": "nwe-status-tip" }, [
                        h("span", { class: "dot" }),
                        text,
                    ]),
                    h("span", { class: "nwe-tip", id: "nwe-status-tip", role: "tooltip" }, [
                        statusHelp(kind),
                        h("span", { class: "nwe-tip-sub", text: kind === "new" ? "Once it’s published, every version is kept in Versions (⋯ menu)." : "Every published version is kept in Versions (⋯ menu)." }),
                    ]),
                    h("span", { class: "nwe-saved", id: "nwe-save" }),
                ]),
            ]),
        ]),
        h("div", { class: "nwe-tools" }, [
            git.bad
                ? h("span", { class: "nwe-git-wrap" }, [
                    h("button", { type: "button", class: "btn btn-sm nwe-btn nwe-git-bad", "data-action": "retry", "aria-describedby": "nwe-git-tip" }, [icon("alert-circle-outline"), git.text]),
                    h("span", { class: "nwe-tip nwe-git-tip", id: "nwe-git-tip", role: "tooltip" }, [
                        h("span", { text: git.reason }),
                        h("span", { class: "nwe-tip-sub", text: "It’s published in Nautobot and people can run it. Only the copy in Git is behind." }),
                    ]),
                ])
                : h("span", { class: "nwe-synced" }, [icon(git.icon || "cloud-check-outline"), git.text]),
            switchRow({ "data-action": "enabled", checked: payload.enabled, disabled: !config.published_number || config.read_only }, "Enabled"),
            h("div", { class: "nwe-seg", role: "group", "aria-label": "Editor" }, [
                h("button", { type: "button", class: state.yaml ? "" : "on", "data-action": "mode", "data-mode": "visual" }, [icon("view-dashboard-outline"), "Visual"]),
                h("button", { type: "button", class: state.yaml ? "on" : "", "data-action": "mode", "data-mode": "yaml" }, [icon("code-tags"), "YAML"]),
            ]),
            h("div", { class: "position-relative" }, [
                h("button", { type: "button", class: "nwe-iconbtn", "data-action": "menu", title: "More", "aria-label": "More actions", "aria-expanded": state.menu ? "true" : "false" }, [icon("dots-horizontal")]),
                state.menu ? menu() : null,
            ]),
            block && block.issues
                ? h("button", {
                    type: "button",
                    class: "nwe-issues",
                    "data-action": "goto",
                    "data-section": block.section,
                    title: `${block.message}. Go to the first one.`,
                    "aria-label": `${block.message}. Go to the first one.`,
                }, [icon("alert-circle-outline"), h("span", { text: String(block.issues) })])
                : null,
            h("button", {
                type: "button",
                class: "btn nwe-btn-accent nwe-publish",
                "data-action": "publish",
                title: block && !block.issues ? block.message : null,
                disabled: Boolean(block) || config.read_only,
            }, [icon("rocket-launch-outline"), config.can_publish ? publishText() : "Request publish"]),
        ]),
    );
    paintSave();
}

function gitIndicator() {
    if (state.gitStatus === "not_synced" || state.gitStatus === "git_invalid" || state.gitStatus === "disabled_repo") {
        return { text: "Not synced · Retry", bad: true, reason: state.gitError || "The last publish couldn’t be pushed. Retry to try again." };
    }
    if (!config.published_number) return { text: "Not in Git yet", bad: false, icon: "cloud-outline" };
    if (state.gitStatus === "pending") return { text: "Syncing…", bad: false, icon: "cloud-sync-outline" };
    return { text: "Synced", bad: false };
}

function menu() {
    return h("div", { class: "nwe-pop nwe-menu", role: "menu" }, [
        item("versions", "Versions", "history"),
        item("test", "Preview run", "play-circle-outline"),
        item("download", "Download draft YAML", "download"),
        item("duplicate", "Duplicate", "content-copy"),
        h("hr"),
        config.published_number
            ? item("discard", "Discard draft…", "trash-can-outline", true)
            : item("discard", "Delete this draft…", "trash-can-outline", true),
    ]);
}

function item(action, text, glyph, danger) {
    return h("button", { type: "button", role: "menuitem", class: `nwe-pop-item${danger ? " danger" : ""}`, "data-action": action }, [glyph ? icon(glyph) : null, text]);
}

function renderBanner() {
    const host = document.getElementById("studio-banner");
    if (!state.stale) {
        host.replaceChildren();
        return;
    }
    const stale = state.stale;
    host.replaceChildren(
        h("div", { class: "nwe-banner alert alert-warning", id: "nwe-stale" }, [
            h("div", {}, [
                h("div", { class: "fw-semibold", text: `Updated in Git by ${stale.author} at ${stale.time}` }),
                h("div", { class: "small", text: "That's now the live version. Your draft started earlier, so publishing it would undo that change. Publish is paused until you choose. Loading their version keeps your draft in Versions." }),
            ]),
            h("div", { class: "ms-auto d-flex gap-2" }, [
                h("button", { type: "button", class: "btn btn-sm nwe-btn", "data-action": "compare", text: "Compare" }),
                h("button", { type: "button", class: "btn btn-sm nwe-btn-accent", "data-action": "load-git", text: "Load their version" }),
            ]),
        ]),
    );
    if (state.compare) host.append(compareCard());
}

function compareCard() {
    const diff = lineDiff(String(config.published_yaml || ""), state.yamlText || "");
    return h("div", { class: "nwe-card" }, [
        h("div", { class: "fw-semibold", text: "Compare" }),
        h("p", { class: "small text-secondary", text: "Lines marked − are in the Git version. Lines marked + are only in your draft." }),
        diffView(diff),
    ]);
}

function renderStepper() {
    const host = document.getElementById("studio-stepper");
    const buckets = localIssues();
    host.replaceChildren(
        h("div", { class: "nwe-stepper" }, [
            h("div", { class: "nwe-kicker nwe-stepper-title", text: "Build your workflow" }),
            ...SECTIONS.map(([id, label], index) => {
                const count = buckets[id].length;
                const on = state.section === id && !state.yaml;
                const cls = ["nwe-nav-item", on ? "on" : "", count ? "err" : ""].filter(Boolean).join(" ");
                return h("button", { type: "button", class: cls, "data-action": "goto", "data-section": id, "aria-current": on ? "step" : null }, [
                    h("span", { class: "nwe-num" }, [count ? icon("exclamation-thick") : String(index + 1)]),
                    h("span", { class: "nwe-nav-copy" }, [
                        h("b", { text: label }),
                        h("small", { class: count ? "bad" : "", text: count ? `${count} issue${count === 1 ? "" : "s"} to fix` : summary(id) }),
                    ]),
                ]);
            }),
            h("div", { class: "nwe-stepper-tip" }, [
                icon("lightbulb-outline"),
                h("span", {}, [
                    "Prefer code? ",
                    h("button", { type: "button", class: "nwe-link", "data-action": "mode", "data-mode": "yaml", text: "Edit as YAML" }),
                    ". Both views show the same draft.",
                ]),
            ]),
        ]),
    );
}

function summary(id) {
    if (id === "details") return payload.category || "Name and description";
    if (id === "inputs") return `${payload.inputs.length} input${payload.inputs.length === 1 ? "" : "s"}`;
    if (id === "steps") return `${allSteps().length} step${allSteps().length === 1 ? "" : "s"}`;
    if (id === "schedule") {
        const bits = [payload.approval_required ? "Approval" : "", payload.schedule.enabled ? "Change window" : ""].filter(Boolean);
        return bits.join(" · ") || "Optional";
    }
    const block = publishBlock();
    if (!block) return "Ready to publish";
    if (block.issues) return issuesElsewhere(block) ? "Not ready yet" : block.message;
    return block.message;
}

/** True when the blocking issues sit in another section (the nav already shows them there). */
function issuesElsewhere(block) {
    return block.section !== "review";
}

function renderEditor() {
    const host = document.getElementById("studio-editor");
    const index = SECTIONS.findIndex((item) => item[0] === state.section);
    host.replaceChildren(
        h("div", { class: "nwe-editor" }, [
            state.yaml ? yamlView() : sectionView(),
            h("div", { class: "nwe-editor-nav" }, [
                index > 0 ? h("button", { type: "button", class: "btn nwe-btn", "data-action": "goto", "data-section": SECTIONS[index - 1][0] }, [icon("arrow-left"), `Back: ${SECTIONS[index - 1][1]}`]) : h("span"),
                index < SECTIONS.length - 1 ? h("button", { type: "button", class: "btn nwe-btn-accent", "data-action": "goto", "data-section": SECTIONS[index + 1][0] }, [`Next: ${SECTIONS[index + 1][1]}`, icon("arrow-right")]) : h("span"),
            ]),
        ]),
    );
    const area = document.getElementById("nwe-yaml");
    const gutter = document.getElementById("nwe-gutter");
    if (area && gutter) area.addEventListener("scroll", () => { gutter.scrollTop = area.scrollTop; });
}

function sectionView() {
    const id = state.section;
    if (id === "inputs") return inputsView();
    if (id === "steps") return stepsView();
    if (id === "schedule") return scheduleView();
    if (id === "review") return reviewView();
    return detailsView();
}

function gridField(label, control, help, wide) {
    return h("div", { class: wide ? "nwe-field nwe-span" : "nwe-field" }, [
        label ? h("label", { class: "form-label", text: label }) : null,
        control,
        help ? h("div", { class: "form-text", text: help }) : null,
    ]);
}

function detailsView() {
    const manual = Number(payload.manual_duration_minutes || 0);
    const hours = manual ? Math.round((48 * Math.max(0, manual - 4)) / 60) : 0;
    return h("div", { class: "nwe-section" }, [
        h("div", { class: "nwe-kicker", text: "Step 1 of 5" }),
        h("h2", { text: "Details" }),
        h("p", { class: "nwe-sub", text: "How this workflow shows up for the people who run it." }),
        h("div", { class: "nwe-card" }, [
            h("div", { class: "nwe-grid" }, [
                gridField("Name", h("input", { class: "form-control", "data-bind": "name", value: payload.name || "" }), "Start with a verb. Shown on the Workflows list and the launch form.", true),
                gridField("Description", h("textarea", { class: "form-control", rows: "3", "data-bind": "description", value: payload.description || "" }), "Shown at the top of the launch form.", true),
                gridField("Category · optional", h("input", { class: "form-control", "data-bind": "category", value: payload.category || "" }), "Groups related workflows on the list."),
                gridField(
                    "Manual effort per run (minutes) · optional",
                    h("div", { class: "input-group" }, [
                        h("input", { class: "form-control", type: "number", min: "0", "data-bind": "manual_duration_minutes", value: payload.manual_duration_minutes ?? "" }),
                        h("span", { class: "input-group-text", text: "minutes" }),
                    ]),
                    "How long this takes by hand. The dashboard calculates time saved from it.",
                ),
                gridField(
                    "Repository",
                    h("select", { class: "form-select", "data-bind": "source_repo" }, [
                        h("option", { value: "", text: "Choose a repository…" }),
                        ...(config.repositories || []).map((repo) => h("option", { value: repo.id, text: repo.name, selected: String(payload.source_repo || "") === String(repo.id) })),
                    ]),
                    "Published YAML is committed here. Drafts stay in Nautobot.",
                    true,
                ),
                selectedRepo()?.problem
                    ? h("div", { class: "nwe-span nwe-repo-problem", role: "status" }, [icon("alert-circle-outline"), h("span", { text: selectedRepo().problem })])
                    : null,
            ]),
            hours ? h("div", { class: "nwe-effort", text: `Example: 48 runs × ${manual} min by hand, minus about 4 min per automated run ≈ ${hours} hours saved a month on the dashboard.` }) : null,
            h("details", { class: "nwe-advanced" }, [
                h("summary", { text: "Advanced · workflow ID" }),
                gridField(
                    "Workflow ID",
                    h("input", { class: "form-control", "data-bind": "key", value: payload.key || "", disabled: Boolean(config.key) }),
                    config.key ? "Locked after the first publish, because run history and approvals refer to it." : "Made from the name. It locks after the first publish.",
                ),
            ]),
        ]),
    ]);
}

function inputsView() {
    return h("div", { class: "nwe-section" }, [
        h("div", { class: "d-flex align-items-start" }, [
            h("div", { class: "me-auto" }, [
                h("div", { class: "nwe-kicker", text: "Step 2 of 5" }),
                h("h2", { text: "Inputs" }),
                h("p", { class: "nwe-sub", text: "The fields people fill in when they run the workflow, in this order." }),
            ]),
            h("button", { type: "button", class: "btn nwe-btn", "data-action": "add-type" }, [icon("plus"), "Add input"]),
        ]),
        state.addType ? typePicker() : null,
        h("div", { class: "nwe-in-list" }, payload.inputs.map((input, index) => inputRow(input, index))),
        h("button", { type: "button", class: "nwe-add-row", "data-action": "add-type" }, [icon("plus"), "Add input"]),
        h("div", { class: "nwe-list-hint", text: "Drag to reorder · click a row to edit" }),
    ]);
}

function typePicker() {
    return h("div", { class: "nwe-card nwe-typegrid" }, [
        h("div", { class: "nwe-kicker", text: "What should people enter?" }),
        h("div", { class: "nwe-typegrid-items" }, TYPES.map(([type, label]) => h("button", { type: "button", class: "nwe-type-item", "data-action": "add-input", "data-type": type }, [
            tile(type, true),
            h("span", {}, [h("b", { text: label })]),
        ]))),
    ]);
}

function inputPills(input) {
    const pills = [];
    if (input.required) pills.push(["Required", ""]);
    if ((input.filter_rows || []).length || (input.filter_mode === "jinja" && input.filter_query)) pills.push(["Filtered", "blue"]);
    const waits = (input.depends_on || []).map((key) => inputByKey(key)?.label || key);
    if (waits.length) pills.push([`Waits for ${waits.join(", ")}`, "amber"]);
    if (input.show_if && (typeof input.show_if === "string" ? input.show_if.trim() : Object.keys(input.show_if).length)) pills.push(["Conditional", ""]);
    if ((input.validators || []).length) pills.push(["Validated", ""]);
    return pills;
}

function inputSubtitle(input) {
    const type = TYPE_LABEL[input.type] || input.type;
    if ((input.type === "object" || input.type === "multiobject") && input.model) {
        const model = (config.content_types || []).find((item) => item.value === input.model);
        return `${type} · ${model ? model.label : input.model}`;
    }
    if (input.type === "choice" || input.type === "multichoice") return `${type} · ${(input.choices || []).length} options`;
    return type;
}

function inputRow(input, index) {
    const open = state.openInput === index;
    return h("div", { class: `nwe-in ${open ? "open" : ""}`, draggable: "true", "data-drag": "input", "data-index": String(index) }, [
        h("div", { class: "nwe-in-head", "data-action": "open-input", "data-index": String(index), "aria-expanded": open ? "true" : "false" }, [
            h("span", { class: "nwe-grip", title: "Drag to reorder", "aria-hidden": "true" }, [icon("drag-vertical")]),
            tile(input.type),
            h("div", { class: "nwe-in-copy" }, [
                h("div", { class: "nwe-in-name", "data-row-label": String(index), text: input.label || "Untitled" }),
                h("div", { class: "nwe-in-meta", text: inputSubtitle(input) }),
            ]),
            h("div", { class: "nwe-pills" }, inputPills(input).map(([text, tone]) => h("span", { class: `nwe-tag ${tone}`, text }))),
            rowActions("input", index, payload.inputs.length, input.label || "input"),
            h("span", { class: "nwe-chev", "aria-hidden": "true" }, [icon(open ? "chevron-up" : "chevron-down")]),
        ]),
        open ? inputEditor(input, index) : null,
    ]);
}

function inputEditor(input, index) {
    const base = `inputs.${index}`;
    const object = input.type === "object" || input.type === "multiobject";
    if (object && input.model) ensureFields(input.model);
    return h("div", { class: "nwe-in-body" }, [
        h("div", { class: "nwe-grid" }, [
            gridField("Label", h("input", { class: "form-control", "data-bind": `${base}.label`, value: input.label || "" })),
            gridField("Field type", h("select", { class: "form-select", "data-bind": `${base}.type` }, TYPES.map(([type, label]) => h("option", { value: type, text: label, selected: input.type === type })))),
            object ? gridField("Which kind of object?", modelPicker(input, index), input.type === "multiobject" ? "People can pick several. Steps get a list." : "") : null,
            gridField("Help text · optional", h("input", { class: "form-control", "data-bind": `${base}.help_text`, value: input.help_text || "" }), "Shown under the field."),
            input.type === "string" || input.type === "integer" || input.type === "list"
                ? gridField("Example shown in the empty field · optional", h("input", { class: "form-control", "data-bind": `${base}.placeholder`, value: input.placeholder || "" }))
                : null,
            input.type === "integer" ? gridField("Smallest allowed · optional", h("input", { class: "form-control", type: "number", "data-bind": `${base}.min_value`, value: input.min_value ?? "" })) : null,
            input.type === "integer" ? gridField("Largest allowed · optional", h("input", { class: "form-control", type: "number", "data-bind": `${base}.max_value`, value: input.max_value ?? "" })) : null,
            input.type === "list" ? gridField("At least · lines", h("input", { class: "form-control", type: "number", min: "0", "data-bind": `${base}.min_items`, value: input.min_items ?? "" })) : null,
            input.type === "list" ? gridField("At most · lines", h("input", { class: "form-control", type: "number", min: "0", "data-bind": `${base}.max_items`, value: input.max_items ?? "" })) : null,
            input.type === "choice" || input.type === "multichoice" ? h("div", { class: "nwe-span" }, [choicesEditor(input, index)]) : null,
            object ? h("div", { class: "nwe-span" }, [filterEditor(input, index)]) : null,
            input.type === "listdict" ? h("div", { class: "nwe-span" }, [tableEditor(input, index)]) : null,
            h("div", { class: "nwe-span nwe-behav" }, [
                switchRow({ "data-bind": `${base}.required`, checked: input.required }, "Required", object ? "People must pick one to run it" : "People must fill it in to run it"),
                visibilityEditor(input, index),
            ]),
        ]),
        h("details", { class: "nwe-advanced" }, [
            h("summary", { text: "Advanced · variable name, wait for fields" }),
            gridField(
                "Variable name",
                h("input", { class: "form-control", "data-bind": `${base}.key`, value: input.key || "", disabled: inputLocked(input) }),
                inputLocked(input) ? "Locked because a published version uses it." : `Referenced as {{ inputs.${input.key || "name"} }}. Made from the label until you edit it.`,
            ),
        ]),
    ]);
}

function modelPicker(input, index) {
    const current = (config.content_types || []).find((item) => item.value === input.model);
    const query = state.modelOpen === index ? state.modelQuery.toLowerCase() : "";
    const groups = new Map();
    (config.content_types || []).forEach((item) => {
        const hay = `${item.label} ${item.app} ${item.value}`.toLowerCase();
        if (query && !hay.includes(query)) return;
        if (!groups.has(item.app)) groups.set(item.app, []);
        groups.get(item.app).push(item);
    });
    return h("div", { class: "nwe-model" }, [
        h("button", { type: "button", class: "form-select text-start", "data-action": "model-open", "data-index": String(index), "aria-expanded": state.modelOpen === index ? "true" : "false", text: current ? current.label : "Choose…" }),
        state.modelOpen === index ? h("div", { class: "nwe-pop nwe-model-pop" }, [
            h("div", { class: "nwe-picker-search" }, [
                icon("magnify"),
                h("input", { class: "form-control form-control-sm", placeholder: "Search objects…", "data-model-search": String(index), value: state.modelQuery || "" }),
            ]),
            ...[...groups.entries()].map(([app, rows]) => h("div", {}, [
                h("div", { class: "nwe-pop-sec", text: app }),
                ...rows.map((item) => h("button", { type: "button", class: `nwe-pop-item${current && current.value === item.value ? " hl" : ""}`, "data-action": "pick-model", "data-index": String(index), "data-model": item.value, text: item.label })),
            ])),
            !groups.size ? h("div", { class: "nwe-picker-empty", text: "No object types match." }) : null,
        ]) : null,
    ]);
}

function choicesEditor(input, index) {
    input.choices = input.choices || [];
    return h("div", { class: "mb-3" }, [
        h("div", { class: "form-label", text: "Options" }),
        ...input.choices.map((choice, choiceIndex) => h("div", { class: "d-flex gap-2 mb-1" }, [
            h("input", { class: "form-control", placeholder: "Value", "data-bind": `inputs.${index}.choices.${choiceIndex}.value`, value: choice.value || "" }),
            h("input", { class: "form-control", placeholder: "Label", "data-bind": `inputs.${index}.choices.${choiceIndex}.label`, value: choice.label || "" }),
            h("button", { type: "button", class: "nwe-iconbtn danger", "data-action": "remove-choice", "data-index": String(index), "data-choice": String(choiceIndex), title: "Remove option", "aria-label": "Remove option" }, [icon("close")]),
        ])),
        h("button", { type: "button", class: "nwe-link", "data-action": "add-choice", "data-index": String(index) }, [icon("plus"), "Add option"]),
    ]);
}

function filterEditor(input, index) {
    if (!input.model) return h("div", { class: "form-text", text: "Choose an object type to filter what people can pick." });
    const model = (config.content_types || []).find((item) => item.value === input.model);
    const noun = (model ? model.label : "object").toLowerCase();
    scheduleCount(index);
    const waits = (input.filter_rows || []).filter((row) => row.source === "input" && row.input).map((row) => inputByKey(row.input)?.label || row.input);
    return h("div", { class: "nwe-filter" }, [
        h("div", { class: "d-flex align-items-center mb-2" }, [
            h("div", { class: "form-label m-0", text: `Only offer ${noun}s where…` }),
            h("button", { type: "button", class: "nwe-link muted ms-auto", "data-action": "filter-mode", "data-index": String(index) }, [icon(input.filter_mode === "jinja" ? "filter-variant" : "code-braces"), input.filter_mode === "jinja" ? "Back to the filter builder" : "Switch to advanced (Jinja)"]),
        ]),
        input.filter_mode === "jinja"
            ? templateEditor({
                value: input.filter_query || "",
                phase: null,
                index: 0,
                rows: 4,
                preview: false,
                placeholder: "location: \"{{ inputs.location.id }}\"",
                label: "Filter query",
                onChange: (next) => {
                    input.filter_query = next;
                    touch();
                    scheduleCount(index);
                },
            })
            : h("div", {}, [
                ...(input.filter_rows || []).map((row, rowIndex) => filterRow(input, index, row, rowIndex)),
                !(input.filter_rows || []).length ? h("div", { class: "form-text", text: `No conditions: every ${noun} is offered.` }) : null,
                h("button", { type: "button", class: "nwe-link", "data-action": "add-filter", "data-index": String(index) }, [icon("plus"), "Add condition"]),
                h("div", { class: "nwe-count", id: `nwe-count-${index}`, text: state.counts[index] || "The count updates as you edit the filter." }),
            ]),
        waits.length ? h("div", { class: "nwe-dep" }, [icon("information-outline"), h("span", { text: `${input.label || "This field"} appears on the form after ${waits.join(", ")} is filled in, because this filter uses it. This was set up for you.` })]) : null,
    ]);
}

function filterRow(input, index, row, rowIndex) {
    const base = `inputs.${index}.filter_rows.${rowIndex}`;
    const fields = modelMeta(input.model);
    const meta = fieldMeta(input.model, row.field);
    const operators = meta?.operators || ["is", "is not", "contains", "starts with"];
    const others = payload.inputs.filter((item, itemIndex) => itemIndex !== index && item.key);
    return h("div", { class: "nwe-frow" }, [
        h("span", { class: "nwe-where", text: rowIndex === 0 ? "WHERE" : "AND" }),
        fields.length
            ? h("select", { class: "form-select form-select-sm", "data-bind": `${base}.field` }, [
                h("option", { value: "", text: "Field" }),
                ...fields.map((item) => h("option", { value: item.key, text: item.label, selected: row.field === item.key })),
            ])
            : h("input", { class: "form-control form-control-sm", placeholder: "Loading fields…", "data-bind": `${base}.field`, value: row.field || "" }),
        h("select", { class: "form-select form-select-sm", "data-bind": `${base}.op` }, operators.map((op) => h("option", { value: op, text: op, selected: (row.op || "is") === op }))),
        row.op === "is empty" ? h("span", { class: "form-text m-0", text: "No value needed" }) : h("select", { class: "form-select form-select-sm", "data-bind": `${base}.source` }, [
            h("option", { value: "value", text: "Fixed value", selected: row.source !== "input" }),
            h("option", { value: "input", text: "From another input", selected: row.source === "input" }),
        ]),
        filterValueControl(input, row, base, meta, others),
        h("button", { type: "button", class: "nwe-iconbtn danger", "data-action": "remove-filter", "data-index": String(index), "data-row": String(rowIndex), title: "Remove condition", "aria-label": "Remove condition" }, [icon("close")]),
    ]);
}

function filterValueControl(input, row, base, meta, others) {
    if (row.op === "is empty") return null;
    if (row.source === "input") {
        return h("select", { class: "form-select form-select-sm", "data-bind": `${base}.input` }, [
            h("option", { value: "", text: "Choose an input…" }),
            ...others.map((item) => h("option", { value: item.key, text: item.label || item.key, selected: row.input === item.key })),
        ]);
    }
    const choices = meta?.choices || [];
    if ((meta?.kind === "choice" || meta?.kind === "object") && (row.op === "is one of" || row.op === "is not one of")) {
        const selected = new Set(String(row.value || "").split(",").map((part) => part.trim()).filter(Boolean));
        return h("div", { class: "nwe-checks" }, choices.map((name) => checkChip({ "data-chip": `${base}.value`, value: name }, selected.has(name), name)));
    }
    if (meta?.kind === "choice" || meta?.kind === "object") {
        return h("select", { class: "form-select form-select-sm", "data-bind": `${base}.value` }, [
            h("option", { value: "", text: meta.kind === "object" ? "Choose an object…" : "Choose…" }),
            ...choices.map((name) => h("option", { value: name, text: name, selected: String(row.value) === name })),
        ]);
    }
    if (meta?.kind === "number") {
        return h("input", { class: "form-control form-control-sm", type: "number", "data-bind": `${base}.value`, value: row.value ?? "" });
    }
    return h("input", { class: "form-control form-control-sm", "data-bind": `${base}.value`, value: row.value ?? "", placeholder: "Value" });
}

function writeWhen(row) {
    return writeCondition(row);
}

function varBucket(action) {
    if (action.type === "awx") {
        if (!action.extra_vars || typeof action.extra_vars !== "object" || Array.isArray(action.extra_vars)) action.extra_vars = {};
        return action.extra_vars;
    }
    if (!action.with || typeof action.with !== "object" || Array.isArray(action.with)) action.with = {};
    return action.with;
}

function applyShowPart(index, part, value, rerender) {
    const input = payload.inputs[index];
    if (!input) return;
    const row = parseShow(input.show_if) || { input: "", op: "is", value: "" };
    row[part] = value;
    input._showMode = "visual";
    input.show_if = writeShow(row);
    if (rerender) renderAll();
    touch();
}

function applyWhenPart(phase, index, part, value, rerender) {
    const actionRow = payload.actions[phase] && payload.actions[phase][index];
    if (!actionRow) return;
    let row = parseWhen(actionRow.when) || { kind: "input", input: "", op: "is", value: "" };
    if (part === "subject") {
        const [kind, key] = String(value).split(/:(.*)/s);
        if (kind === "step") {
            row = row.kind === "step" ? { ...row, step: key } : { kind: "step", step: key, test: "succeeded", field: "", value: "" };
        } else {
            const input = inputByKey(key);
            row = { kind: "input", input: key, op: "is", value: input && input.type === "boolean" ? true : "" };
        }
    } else {
        row[part] = value;
        if (part === "test" && !String(value).startsWith("output")) {
            row.field = "";
            row.value = "";
        }
    }
    actionRow._whenMode = "visual";
    actionRow.when = writeWhen(row);
    if (rerender) renderAll();
    touch();
}

function parseShow(value) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
        const entry = Object.entries(value)[0];
        if (!entry) return null;
        return { input: entry[0], op: "is", value: entry[1] };
    }
    if (typeof value !== "string") return null;
    const bare = value.trim().match(/^\{\{\s*inputs\.([A-Za-z0-9_]+)\s*\}\}$/);
    if (bare) return { input: bare[1], op: "is", value: true };
    const match = value.trim().match(/^\{\{\s*inputs\.([A-Za-z0-9_]+)\s*(==|!=)\s*(.+?)\s*\}\}$/);
    if (!match) return null;
    let raw = match[3].trim();
    if ((raw.startsWith('"') && raw.endsWith('"')) || (raw.startsWith("'") && raw.endsWith("'"))) raw = raw.slice(1, -1);
    if (raw === "true") raw = true;
    if (raw === "false") raw = false;
    return { input: match[1], op: match[2] === "!=" ? "is not" : "is", value: raw };
}

function writeShow(row) {
    if (!row || !row.input) return {};
    if (row.op === "is not") {
        const literal = typeof row.value === "boolean" ? String(row.value) : JSON.stringify(String(row.value ?? ""));
        return `{{ inputs.${row.input} != ${literal} }}`;
    }
    let stored = row.value;
    if (stored === "true") stored = true;
    if (stored === "false") stored = false;
    return { [row.input]: stored };
}

function visibilityEditor(input, index) {
    const advanced = input._showMode === "jinja" || (typeof input.show_if === "string" && input.show_if.trim() && !parseShow(input.show_if));
    if (advanced) {
        return h("div", {}, [
            h("label", { class: "form-label", text: "Visibility" }),
            templateEditor({
                value: typeof input.show_if === "string" ? input.show_if : "",
                phase: null,
                index: 0,
                placeholder: "{{ inputs.mode == 'existing' }}",
                label: "Show this field when",
                onChange: (next) => {
                    input.show_if = next;
                    touch();
                },
            }),
            h("button", { type: "button", class: "nwe-link muted", "data-action": "show-visual", "data-index": String(index) }, [icon("filter-variant"), "Back to the condition builder"]),
        ]);
    }
    const row = parseShow(input.show_if);
    if (!row) {
        return h("div", {}, [
            h("label", { class: "form-label", text: "Visibility" }),
            h("div", { class: "nwe-inline" }, [
                h("button", { type: "button", class: "nwe-link", "data-action": "add-show", "data-index": String(index) }, [icon("plus"), "Only show this field when…"]),
                h("span", { class: "nwe-muted", text: "Always shown" }),
            ]),
        ]);
    }
    const others = payload.inputs.filter((item, itemIndex) => itemIndex !== index && item.key);
    const source = inputByKey(row.input);
    return h("div", {}, [
        h("label", { class: "form-label", text: "Visibility" }),
        h("div", { class: "nwe-cond" }, [
            h("span", { class: "nwe-where", text: "Show only when" }),
            h("select", { class: "form-select form-select-sm", "data-show": "input", "data-index": String(index) }, [
                ...others.map((item) => h("option", { value: item.key, text: item.label || item.key, selected: row.input === item.key })),
            ]),
            h("select", { class: "form-select form-select-sm", "data-show": "op", "data-index": String(index) }, ["is", "is not"].map((op) => h("option", { value: op, text: op, selected: row.op === op }))),
            showValue(source, row, index),
            h("button", { type: "button", class: "nwe-link muted", "data-action": "show-jinja", "data-index": String(index) }, [icon("code-braces"), "Advanced (Jinja)"]),
            h("button", { type: "button", class: "nwe-iconbtn danger", "data-action": "clear-show", "data-index": String(index), title: "Always show", "aria-label": "Remove the condition" }, [icon("close")]),
        ]),
    ]);
}

function whenValue(source, row, phase, index) {
    const attrs = { class: "form-select form-select-sm", "data-when": "value", "data-phase": phase, "data-index": String(index) };
    if (source && source.type === "boolean") {
        return h("select", attrs, [
            h("option", { value: "true", text: "Yes", selected: row.value === true || row.value === "true" }),
            h("option", { value: "false", text: "No", selected: row.value === false || row.value === "false" }),
        ]);
    }
    if (source && (source.choices || []).length) {
        return h("select", attrs, source.choices.map((choice) => h("option", { value: choice.value, text: choice.label || choice.value, selected: String(row.value) === String(choice.value) })));
    }
    return h("input", { class: "form-control form-control-sm", "data-when": "value", "data-phase": phase, "data-index": String(index), value: row.value ?? "" });
}

function showValue(source, row, index) {
    if (source && source.type === "boolean") {
        return h("select", { class: "form-select form-select-sm", "data-show": "value", "data-index": String(index) }, [
            h("option", { value: "true", text: "Yes", selected: row.value === true || row.value === "true" }),
            h("option", { value: "false", text: "No", selected: row.value === false || row.value === "false" }),
        ]);
    }
    if (source && (source.choices || []).length) {
        return h("select", { class: "form-select form-select-sm", "data-show": "value", "data-index": String(index) }, source.choices.map((choice) => h("option", { value: choice.value, text: choice.label || choice.value, selected: String(row.value) === String(choice.value) })));
    }
    return h("input", { class: "form-control form-control-sm", "data-show": "value", "data-index": String(index), value: row.value ?? "" });
}

function tableEditor(input, index) {
    input.columns = input.columns || [];
    return h("div", { class: "mb-3" }, [
        h("div", { class: "form-label", text: "Columns" }),
        ...input.columns.map((column, columnIndex) => h("div", { class: "nwe-colcard" }, [
            h("div", { class: "row g-2" }, [
                h("div", { class: "col" }, [h("input", { class: "form-control", placeholder: "Label", "data-bind": `inputs.${index}.columns.${columnIndex}.label`, value: column.label || "" })]),
                h("div", { class: "col" }, [h("input", { class: "form-control", placeholder: "Key", "data-bind": `inputs.${index}.columns.${columnIndex}.key`, value: column.key || "" })]),
                h("div", { class: "col" }, [h("select", { class: "form-select", "data-bind": `inputs.${index}.columns.${columnIndex}.type` }, ["string", "integer", "number", "boolean", "choice"].map((type) => h("option", { value: type, text: type, selected: (column.type || "string") === type })))]),
            ]),
            h("div", { class: "row g-2 mt-1" }, [
                h("div", { class: "col" }, [h("input", { class: "form-control", type: "number", placeholder: "Min", "data-bind": `inputs.${index}.columns.${columnIndex}.min_value`, value: column.min_value ?? "" })]),
                h("div", { class: "col" }, [h("input", { class: "form-control", type: "number", placeholder: "Max", "data-bind": `inputs.${index}.columns.${columnIndex}.max_value`, value: column.max_value ?? "" })]),
                h("div", { class: "col-auto" }, [h("button", { type: "button", class: "btn nwe-btn-ghost", "data-action": "remove-column", "data-index": String(index), "data-column": String(columnIndex) }, [icon("trash-can-outline"), "Remove column"])]),
            ]),
        ])),
        h("button", { type: "button", class: "nwe-link", "data-action": "add-column", "data-index": String(index) }, [icon("plus"), "Add column"]),
        h("div", { class: "row mt-2" }, [
            h("div", { class: "col" }, [field("At least rows", h("input", { class: "form-control", type: "number", min: "0", "data-bind": `inputs.${index}.min_rows`, value: input.min_rows ?? "" }))]),
            h("div", { class: "col" }, [field("At most rows", h("input", { class: "form-control", type: "number", min: "0", "data-bind": `inputs.${index}.max_rows`, value: input.max_rows ?? "" }))]),
        ]),
        switchRow({ "data-bind": `inputs.${index}.csv_import`, checked: input.csv_import !== false }, "Allow CSV import", "Import CSV reads the file in the browser. It is not uploaded or stored."),
    ]);
}

const PHASE_META = {
    pre: { label: "Before", icon: "shield-check-outline", help: "Runs first, and again when a scheduled run is booked. If a step fails, the run stops here." },
    main: { label: "Main", icon: "play", help: "Runs in order. If a step fails, it’s recorded and the remaining main steps still run." },
    post: { label: "Always run at the end", icon: "flag-checkered", help: "Runs last, even after failures. Use it for cleanup and notes. A failure here doesn’t fail the run." },
};

function stepsView() {
    return h("div", { class: "nwe-section" }, [
        h("div", { class: "nwe-kicker", text: "Step 3 of 5" }),
        h("h2", { text: "Steps" }),
        h("p", { class: "nwe-sub", text: "What happens when someone runs this workflow, from top to bottom." }),
        h("div", { class: "nwe-pipe" }, PHASES.map(([phase]) => phaseBlock(phase))),
    ]);
}

function phaseBlock(phase) {
    const meta = PHASE_META[phase];
    const list = payload.actions[phase] || [];
    return h("section", { class: `nwe-phase ph-${phase}`, "aria-label": meta.label }, [
        h("div", { class: "nwe-phase-head" }, [
            h("span", { class: "nwe-node nwe-phase-ico", "aria-hidden": "true" }, [icon(meta.icon)]),
            h("div", { class: "nwe-phase-copy" }, [
                h("div", { class: "nwe-phase-title" }, [meta.label, h("span", { class: "nwe-phase-count", text: list.length === 1 ? "1 step" : `${list.length} steps` })]),
                h("div", { class: "nwe-phase-help", text: meta.help }),
            ]),
        ]),
        insertPoint(phase, 0, list.length === 0),
        ...list.flatMap((action, index) => [stepCard(phase, action, index), insertPoint(phase, index + 1, false)]),
    ]);
}

function insertPoint(phase, index, empty) {
    const where = empty ? `Add a step to ${PHASE_META[phase].label}` : "Add a step here";
    return h("div", { class: `nwe-insert${empty ? " empty" : ""}` }, [
        h("button", { type: "button", class: "nwe-node nwe-insert-btn", "data-action": "add-step", "data-phase": phase, "data-at": String(index), title: where, "aria-label": where }, [icon("plus")]),
        empty ? h("button", { type: "button", class: "nwe-insert-label", "data-action": "add-step", "data-phase": phase, "data-at": String(index), text: "Add a step" }) : null,
    ]);
}

function paramEntries(action) {
    const raw = action.type === "awx" ? action.extra_vars : action.with;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
    return Object.entries(raw);
}

const INPUT_ICONS = {
    string: "form-textbox",
    integer: "numeric",
    boolean: "toggle-switch-outline",
    choice: "radiobox-marked",
    multichoice: "checkbox-marked-outline",
    object: "cube-outline",
    multiobject: "cube-outline",
    list: "format-list-bulleted",
    listdict: "table",
};

/** How one parameter value reads in a sentence: an input chip, a literal, or an expression chip. */
function valueChip(value) {
    const read = paramMode(value);
    if (read.mode === "input") {
        const input = inputByKey(read.input);
        if (!input) return h("span", { class: "nwe-var bad", title: "This input doesn’t exist" }, [icon("alert-circle-outline"), read.input]);
        const label = input.label || input.key;
        return h("span", { class: "nwe-var" }, [icon(INPUT_ICONS[input.type] || "form-textbox"), read.attr ? `${label} › ${read.attr}` : label]);
    }
    if (read.mode === "expr") {
        const text = String(value).replace(/\s+/g, " ").trim();
        return h("span", { class: "nwe-var expr", title: text }, [icon("code-braces"), text.length > 34 ? `${text.slice(0, 33)}…` : text]);
    }
    if (value === "" || value == null) return h("span", { class: "nwe-muted", text: "empty" });
    if (typeof value === "boolean") return h("span", { class: "nwe-lit", text: value ? "Yes" : "No" });
    return h("span", { class: "nwe-lit", text: fixedText(value) });
}

function scriptMeta(action) {
    if (action.type === "awx" || !action.script) return null;
    return (config.scripts || []).find((item) => item.path === action.script) || null;
}

function missingParams(action) {
    const meta = scriptMeta(action);
    if (!meta) return [];
    const raw = action.with;
    if (typeof raw === "string") return [];
    const map = raw && typeof raw === "object" ? raw : {};
    return (meta.params || []).filter((param) => param.required && (map[param.name] === undefined || map[param.name] === ""));
}

function stepSummary(action) {
    const bits = [];
    const raw = action.type === "awx" ? action.extra_vars : action.with;
    if (typeof raw === "string" && raw.trim()) bits.push(h("span", { class: "nwe-var expr" }, [icon("code-braces"), "a parameter template"]));
    paramEntries(action).forEach(([key, value], index) => {
        if (index) bits.push(", ");
        bits.push(h("span", { class: "nwe-pkey", text: key }));
        bits.push(" = ");
        bits.push(valueChip(value));
    });
    const missing = missingParams(action);
    const tail = missing.length ? [h("span", { class: "nwe-missing ms-1" }, [icon("alert-circle-outline"), `Needs ${missing.map((param) => param.name).join(", ")}`])] : [];
    if (action.type === "awx") {
        if (!action.template_id) return h("span", { class: "nwe-missing" }, [icon("alert-circle-outline"), "Enter the AWX job template ID"]);
        return h("span", {}, ["Launch AWX template ", h("b", { text: action.template_name || `#${action.template_id}` }), bits.length ? " with " : "", ...bits]);
    }
    const name = scriptName(action.script);
    if (!name) return h("span", { class: "nwe-missing" }, [icon("alert-circle-outline"), "Choose which script to run"]);
    return h("span", {}, ["Run script ", h("b", { text: name }), bits.length ? " with " : "", ...bits, ...tail]);
}

function paintStepSummary(phase, index) {
    const node = app.querySelector(`[data-step-sum="${phase}:${index}"]`);
    const action = (payload.actions[phase] || [])[index];
    if (node && action) node.replaceChildren(stepSummary(action));
}

const PHASE_SHORT = { pre: "Before", main: "Main", post: "At the end" };

function phaseOf(action) {
    return PHASES.map(([name]) => name).find((name) => (payload.actions[name] || []).includes(action)) || "";
}

function stepLabel(key) {
    const found = allSteps().find((item) => item.name === key);
    return found ? found.label || found.name : key;
}

function whenPlain(action) {
    const row = parseWhen(action.when);
    if (!row) return action.when ? "a custom condition is true" : "";
    if (row.kind === "step") {
        const name = `“${stepLabel(row.step)}”`;
        if (row.test === "succeeded") return `${name} succeeded`;
        if (row.test === "failed") return `${name} failed`;
        if (row.test === "skipped") return `${name} was skipped`;
        const what = row.field ? `${name} output ${row.field}` : `${name} output`;
        return `${what} ${row.test === "output_not" ? "is not" : "is"} ${row.value === true ? "true" : row.value === false ? "false" : row.value}`;
    }
    const input = inputByKey(row.input);
    const label = input ? input.label || input.key : row.input;
    const shown = row.value === true || row.value === "true" ? "Yes" : row.value === false || row.value === "false" ? "No" : row.value;
    return `“${label}” ${row.op} ${shown}`;
}

/**
 * Reorder, duplicate and delete buttons for a row header. Inputs and steps use the
 * same group in the same place, so the two lists behave alike.
 */
function rowActions(kind, index, count, name, phase) {
    const base = { "data-index": String(index), "data-phase": phase || null };
    const noun = kind === "step" ? "step" : "input";
    return h("div", { class: "nwe-row-acts", role: "group", "aria-label": `${name} actions` }, [
        h("button", { type: "button", class: "nwe-iconbtn", "data-action": `move-${kind}`, "data-dir": "-1", ...base, disabled: index === 0, title: "Move up", "aria-label": `Move ${name} up` }, [icon("arrow-up")]),
        h("button", { type: "button", class: "nwe-iconbtn", "data-action": `move-${kind}`, "data-dir": "1", ...base, disabled: index >= count - 1, title: "Move down", "aria-label": `Move ${name} down` }, [icon("arrow-down")]),
        h("button", { type: "button", class: "nwe-iconbtn", "data-action": `duplicate-${kind}`, ...base, title: `Duplicate ${noun}`, "aria-label": `Duplicate ${name}` }, [icon("content-copy")]),
        h("button", { type: "button", class: "nwe-iconbtn danger", "data-action": `remove-${kind}`, ...base, title: `Delete ${noun}`, "aria-label": `Delete ${name}` }, [icon("trash-can-outline")]),
    ]);
}

function stepCard(phase, action, index) {
    const open = state.openStep === `${phase}:${index}`;
    const missing = action.type === "awx" ? !action.template_id : !scriptName(action.script);
    const list = payload.actions[phase] || [];
    const name = action.label || action.name || "Untitled step";
    const flags = [];
    if (action.when) flags.push(["call-split", `Only if ${whenPlain(action)}`]);
    if (action.type === "awx" && action.monitor !== false) flags.push(["timer-sand", "Waits for the job to finish"]);
    return h("div", { class: `nwe-stepcard ${open ? "open" : ""} ${missing ? "bad" : ""}`, draggable: "true", "data-drag": "step", "data-phase": phase, "data-index": String(index) }, [
        h("div", { class: "nwe-step-head", "data-action": "open-step", "data-phase": phase, "data-index": String(index), "aria-expanded": open ? "true" : "false" }, [
            h("span", { class: "nwe-grip", title: "Drag to reorder", "aria-hidden": "true" }, [icon("drag-vertical")]),
            tile(action.type === "awx" ? "awx" : "nautobot"),
            h("div", { class: "nwe-step-main" }, [
                h("div", { class: "nwe-step-title" }, [
                    h("span", { "data-step-title": `${phase}:${index}`, text: name }),
                    h("span", { class: "nwe-tag", text: action.type === "awx" ? "AWX job" : "Nautobot script" }),
                ]),
                h("div", { class: "nwe-step-sum", "data-step-sum": `${phase}:${index}` }, [stepSummary(action)]),
                flags.length ? h("div", { class: "nwe-step-flags" }, flags.map(([glyph, text]) => h("span", { class: "nwe-tag blue" }, [icon(glyph), text]))) : null,
            ]),
            rowActions("step", index, list.length, name, phase),
            h("span", { class: "nwe-chev", "aria-hidden": "true" }, [icon(open ? "chevron-up" : "chevron-down")]),
        ]),
        open ? stepEditor(phase, action, index) : null,
    ]);
}

const STEP_TYPES = [
    ["nautobot", "Nautobot script", "script-text-outline"],
    ["awx", "AWX job", "ansible"],
];
const PARAM_MODES = [
    ["input", "From an input", "form-textbox"],
    ["fixed", "Fixed value", "pencil-outline"],
    ["expr", "Expression", "code-braces"],
];
const PARAM_TYPE_LABEL = { string: "text", integer: "number", boolean: "yes / no", object: "Nautobot object", multiobject: "Nautobot objects", list: "list", choice: "choice", dict: "mapping" };

const NEW_SCRIPT = "__new_script__";

/** Parameters for a new script, from what the step already passes it. */
function newScriptParams(action) {
    const raw = action.with && typeof action.with === "object" && !Array.isArray(action.with) ? action.with : {};
    return Object.entries(raw).map(([name, value]) => {
        const read = paramMode(value);
        const input = read.mode === "input" ? inputByKey(read.input) : null;
        const row = { name, required: true, type: "string" };
        if (input && (!read.attr || (read.attr === "id" && input.type === "object"))) {
            const map = { object: "object", multiobject: "multiobject", integer: "integer", boolean: "boolean", list: "list", multichoice: "list", choice: "choice", listdict: "list" };
            row.type = map[input.type] || "string";
            if (input.model) row.model = input.model;
            row.help = input.label || "";
        } else if (typeof value === "boolean") {
            row.type = "boolean";
        } else if (typeof value === "number") {
            row.type = "integer";
        }
        return row;
    });
}

function freeScriptName(base) {
    const slug = (text) => text.toLowerCase().replace(/[^0-9a-z]+/g, "_").replace(/^_+|_+$/g, "");
    const taken = new Set((config.scripts || []).map((item) => item.path));
    const dir = config.scripts_dir || "scripts";
    let name = base.toLowerCase();
    for (let n = 2; taken.has(`${dir}/${slug(name)}.py`) && n < 50; n += 1) name = `${base.toLowerCase()} ${n}`;
    return name;
}

function newScriptRequest(action) {
    const draft = state.newScript;
    return {
        name: draft.name,
        title: action.label || draft.name,
        parameters: newScriptParams(action),
        body: config.can_write_scripts ? draft.body : undefined,
        repository: payload.source_repo || null,
    };
}

let scriptPreviewTimer = 0;
function refreshScriptPreview(action) {
    clearTimeout(scriptPreviewTimer);
    scriptPreviewTimer = window.setTimeout(async () => {
        const draft = state.newScript;
        if (!draft || !config.script_preview_url) return;
        const result = await api(config.script_preview_url, newScriptRequest(action));
        if (state.newScript !== draft) return;
        draft.code = result.ok ? result.data.content : "";
        draft.path = result.ok ? result.data.path : "";
        draft.problems = result.ok ? result.data.problems || [] : [{ message: publishFailure(result) }];
        paintScriptPreview();
    }, 300);
}

function paintScriptPreview() {
    const draft = state.newScript;
    const code = document.getElementById("nwe-ns-code");
    const where = document.getElementById("nwe-ns-where");
    const problems = document.getElementById("nwe-ns-problems");
    const create = document.getElementById("nwe-ns-create");
    if (!draft) return;
    if (code) code.textContent = draft.code || "Loading…";
    if (where) where.textContent = draft.path ? `Saved as ${draft.path}${selectedRepo() ? ` in ${selectedRepo().name}` : ""}.` : "Give it a name.";
    if (problems) problems.replaceChildren(...(draft.problems || []).map((item) => h("div", { class: "nwe-ns-problem" }, [icon("alert-circle-outline"), item.message])));
    if (create) create.disabled = Boolean(draft.busy || (draft.problems || []).length || !draft.path || !payload.source_repo);
}

function newScriptPanel(phase, action, index) {
    const draft = state.newScript;
    const params = newScriptParams(action);
    const canWrite = Boolean(config.can_write_scripts);
    const repoMissing = !payload.source_repo;
    const panel = h("div", { class: "nwe-newscript", "data-ns-id": `${phase}:${index}` }, [
        h("div", { class: "nwe-newscript-head" }, [icon("script-text-outline"), h("b", { text: "New script" })]),
        h("div", { class: "nwe-grid" }, [
            h("div", { class: "nwe-field nwe-span" }, [
                h("label", { class: "form-label", for: "nwe-ns-name", text: "Script name" }),
                h("input", { id: "nwe-ns-name", class: "form-control", "data-ns": "name", value: draft.name || "", spellcheck: "false", readonly: canWrite ? null : "readonly" }),
                h("div", { class: "form-text", id: "nwe-ns-where" }),
            ]),
        ]),
        h("div", { class: "nwe-ns-params" }, [
            h("span", { class: "nwe-muted", text: params.length ? "It will ask for:" : "No parameters yet. Add them under Parameters and they show up here." }),
            ...params.map((row) => h("span", { class: "nwe-tag" }, [row.name, h("small", { text: ` ${PARAM_TYPE_LABEL[row.type] || row.type}` })])),
        ]),
        canWrite
            ? h("div", { class: "nwe-field" }, [
                h("label", { class: "form-label", for: "nwe-ns-body", text: "What it does · optional" }),
                h("textarea", { id: "nwe-ns-body", class: "form-control nwe-code nwe-ns-body", rows: "6", "data-ns": "body", spellcheck: "false", placeholder: "Python for the inside of run(params). Leave it empty for a placeholder that does nothing yet.", value: draft.body || "" }),
                h("div", { class: "form-text", text: "Checked as you type. It isn’t run here." }),
            ])
            : h("p", { class: "nwe-muted small mb-2", text: "You can’t create scripts. Copy the code below and send it to an admin, or ask for the “Write scripts” permission." }),
        h("div", { class: "nwe-ns-problems", id: "nwe-ns-problems" }),
        h("div", { class: "nwe-ns-codehead" }, [
            h("span", { class: "nwe-muted small", text: canWrite ? "The file it saves" : "The file it would save (read-only)" }),
            h("button", { type: "button", class: "nwe-link", "data-action": "ns-copy" }, [icon("content-copy"), "Copy code"]),
        ]),
        h("pre", { class: "nwe-ns-code", id: "nwe-ns-code", tabindex: "0", "aria-label": "Script code, read-only" }),
        h("div", { class: "nwe-ns-note" }, [icon("shield-lock-outline"), h("span", { text: "Nothing runs in the builder. The script is saved to the repository switched off. An admin needs to review it and enable it before this step can run." })]),
        repoMissing ? h("div", { class: "nwe-ns-problem" }, [icon("alert-circle-outline"), "Choose the repository on Details first."]) : null,
        h("div", { class: "d-flex gap-2 mt-2" }, [
            canWrite ? h("button", { type: "button", class: "btn btn-sm nwe-btn-accent", id: "nwe-ns-create", "data-action": "ns-create", "data-phase": phase, "data-index": String(index) }, [icon("plus"), draft.busy ? "Creating…" : "Create script"]) : null,
            h("button", { type: "button", class: "btn btn-sm nwe-btn", "data-action": "ns-cancel" }, ["Cancel"]),
        ]),
    ]);
    queueMicrotask(() => {
        paintScriptPreview();
        refreshScriptPreview(action);
    });
    return panel;
}

async function createScript(phase, index) {
    const action = payload.actions[phase][index];
    const draft = state.newScript;
    if (!draft || draft.busy) return;
    draft.busy = true;
    paintScriptPreview();
    const result = await api(config.script_create_url, newScriptRequest(action));
    draft.busy = false;
    if (!result.ok) {
        draft.problems = [{ message: publishFailure(result) }];
        paintScriptPreview();
        toast(publishFailure(result));
        return;
    }
    config.scripts = [...(config.scripts || []), result.data.script];
    action.script = result.data.script.path;
    state.newScript = null;
    renderEditor();
    touch();
    toast(result.data.detail || "Script created.");
}

function stepEditor(phase, action, index) {
    const base = `actions.${phase}.${index}`;
    const scripts = (config.scripts || []).filter((item) => !payload.source_repo || item.repo === String(payload.source_repo));
    const choices = scripts.length ? scripts : (config.scripts || []);
    const meta = scriptMeta(action);
    const locked = stepLocked(action);
    const key = action.name || "";
    const drafting = action.type !== "awx" && state.newScript && state.newScript.id === `${phase}:${index}`;
    return h("div", { class: "nwe-step-body" }, [
        h("div", { class: "nwe-step-type" }, [
            h("span", { class: "nwe-step-type-label", id: `nwe-type-${phase}-${index}`, text: "What it runs" }),
            h("div", { class: "nwe-seg", role: "radiogroup", "aria-labelledby": `nwe-type-${phase}-${index}` }, STEP_TYPES.map(([type, label, glyph]) => {
                const on = (action.type || "nautobot") === type;
                return h("button", { type: "button", role: "radio", "aria-checked": on ? "true" : "false", class: on ? "on" : "", "data-action": "step-type", "data-type": type, "data-phase": phase, "data-index": String(index) }, [icon(glyph), label]);
            })),
        ]),
        h("div", { class: "nwe-grid" }, [
            gridField("Step name", h("input", { class: "form-control", "data-bind": `${base}.label`, value: action.label || "", placeholder: action.type === "awx" ? "Reboot the device" : "Check the device is reachable" }), "Shown on this card, in run history and in What happens."),
            action.type === "awx"
                ? gridField("Job template", h("div", { class: "nwe-template-pick" }, [
                    h("input", { class: `form-control nwe-template-id ${action.template_id ? "" : "is-invalid"}`, type: "number", min: "1", "data-bind": `${base}.template_id`, value: action.template_id ?? "", placeholder: "ID", "aria-label": "Job template ID" }),
                    h("input", { class: "form-control", "data-bind": `${base}.template_name`, value: action.template_name || "", placeholder: "Network · Reboot device", "aria-label": "Job template name" }),
                ]), "Its ID in AWX, and the name people know it by (shown on this card).")
                : gridField("Script", h("select", { class: `form-select ${scriptName(action.script) || drafting ? "" : "is-invalid"}`, "data-script-pick": `${phase}:${index}`, "aria-label": "Script" }, [
                    h("option", { value: "", text: choices.length ? "Choose a script…" : "No scripts in this repository yet", selected: !action.script && !drafting }),
                    ...choices.map((item) => h("option", { value: item.path, text: item.enabled === false ? `${item.name} (switched off)` : item.name, selected: action.script === item.path && !drafting })),
                    h("option", { value: NEW_SCRIPT, text: "New script…", selected: drafting }),
                ]), drafting ? "Describe it below. It’s saved to the repository when you create it." : meta ? (meta.params.length ? `${action.script} · asks for ${meta.params.length} parameter${meta.params.length === 1 ? "" : "s"}` : action.script) : "Choose which script to run, or make a new one."),
            meta && meta.enabled === false && !drafting
                ? h("div", { class: "nwe-span nwe-script-off", role: "status" }, [icon("power-plug-off-outline"), h("span", { text: "Switched off: an admin needs to review this script and enable it before this step can run. You can still build and publish the workflow." })])
                : null,
            drafting ? h("div", { class: "nwe-span" }, [newScriptPanel(phase, action, index)]) : null,
            h("div", { class: "nwe-span" }, [paramsEditor(phase, action, index)]),
            action.type === "awx" ? h("div", { class: "nwe-span" }, [
                switchRow({ "data-bind": `${base}.monitor`, checked: action.monitor !== false }, "Wait for the AWX job to finish", "The run waits until AWX reports success or failure, so later steps can rely on it."),
            ]) : null,
            h("div", { class: "nwe-span" }, [whenEditor(phase, action, index)]),
        ]),
        h("details", { class: "nwe-advanced", open: state.openAdvanced === `${phase}:${index}` ? true : null, "data-adv": `${phase}:${index}` }, [
            h("summary", {}, ["Advanced", h("span", { class: "nwe-adv-key" }, [" · step key ", h("code", { "data-step-key-text": `${phase}:${index}`, text: key || "—" })])]),
            h("div", { class: "nwe-grid" }, [
                gridField(
                    "Step key",
                    h("div", { class: `nwe-keybox${locked ? " locked" : ""}` }, [
                        h("input", { class: "form-control nwe-code", "data-bind": `${base}.name`, "data-step-key": `${phase}:${index}`, value: key, readonly: locked ? "readonly" : null, "aria-readonly": locked ? "true" : null, spellcheck: "false" }),
                        locked ? h("span", { class: "nwe-keylock", title: "Locked", "aria-hidden": "true" }, [icon("lock-outline")]) : null,
                    ]),
                    null,
                ),
                h("div", { class: "nwe-span nwe-key-help", "data-step-key-help": `${phase}:${index}` }, keyHelp(action, phase, index)),
            ]),
        ]),
    ]);
}

function keyHelp(action, phase, index) {
    const key = action.name || "key";
    const ref = h("code", { class: "nwe-code", text: `{{ results.${key} }}` });
    if (stepLocked(action)) return [icon("lock-outline"), h("span", {}, ["Locked because the published version uses it: run history and later steps (", ref, ") refer to it."])];
    if (action._nameTouched) {
        return [h("span", {}, ["Later steps read this step’s result as ", ref, ". "]), h("button", { type: "button", class: "nwe-link", "data-action": "key-from-label", "data-phase": phase, "data-index": String(index) }, [icon("refresh"), "Make it from the step name again"])];
    }
    return [h("span", {}, ["Made from the step name as you type. Later steps read its result as ", ref, ". It’s fixed once you publish."])];
}

/** Everything a template at this step can use, for autocomplete. */
function referencesFor(phase, index) {
    const items = [];
    payload.inputs.forEach((input) => {
        if (!input.key) return;
        const label = input.label || input.key;
        items.push({ path: `inputs.${input.key}`, detail: label, group: "Inputs" });
        if (input.type === "object") {
            items.push({ path: `inputs.${input.key}.id`, detail: `${label} › id`, group: "Inputs" });
            items.push({ path: `inputs.${input.key}.name`, detail: `${label} › name`, group: "Inputs" });
        }
    });
    earlierSteps(phase, index).forEach((action) => {
        items.push({ path: `results.${action.name}`, detail: action.label || action.name, group: "Earlier steps" });
        items.push({ path: `results.${action.name}.status`, detail: "succeeded / failed", group: "Earlier steps" });
    });
    [["user.username", "who started the run"], ["user.email", "their email"]].forEach(([path, detail]) => items.push({ path, detail, group: "Run" }));
    return items;
}

function earlierSteps(phase, index) {
    const order = PHASES.map(([name]) => name);
    const stop = order.indexOf(phase);
    const found = [];
    order.forEach((name, phaseIndex) => {
        (payload.actions[name] || []).forEach((action, actionIndex) => {
            if (!action.name) return;
            if (phaseIndex < stop || (phaseIndex === stop && actionIndex < index)) found.push(action);
        });
    });
    return found;
}

/** Sample values for the live preview: what's in the Launch form preview, else a plausible example. */
function sampleInputs() {
    const out = {};
    payload.inputs.forEach((input, inputIndex) => {
        if (!input.key) return;
        const filled = Object.prototype.hasOwnProperty.call(state.preview, input.key) ? state.preview[input.key] : previewDefault(input);
        const empty = filled === undefined || filled === null || filled === "" || (Array.isArray(filled) && !filled.length);
        const examples = (((state.match || {})[inputIndex] || {}).examples || (state.modelExamples || {})[input.model] || []);
        const objectFor = (id) => {
            const hit = examples.find((item) => String(item.id) === String(id));
            return { id: String(id), name: hit ? hit.label : String(id) };
        };
        const firstObject = () => (examples[0] ? { id: String(examples[0].id), name: examples[0].label } : { id: "00000000-0000-0000-0000-000000000000", name: `example ${modelNouns(input.model)[0]}` });
        const firstChoice = ((input.choices || [])[0] || {}).value ?? "";
        switch (input.type) {
            case "object": out[input.key] = empty ? firstObject() : objectFor(filled); break;
            case "multiobject": out[input.key] = empty ? [firstObject()] : filled.map(objectFor); break;
            case "boolean": out[input.key] = empty ? false : filled === true || filled === "true"; break;
            case "integer": out[input.key] = empty ? Number(input.min_value || 1) : Number(filled); break;
            case "list": out[input.key] = empty ? ["example"] : filled; break;
            case "multichoice": out[input.key] = empty ? (firstChoice !== "" ? [firstChoice] : []) : filled; break;
            case "choice": out[input.key] = empty ? firstChoice : filled; break;
            case "listdict": out[input.key] = empty ? [Object.fromEntries((input.columns || []).map((column) => [column.key, column.type === "integer" ? 1 : column.type === "boolean" ? false : "example"]))] : filled; break;
            default: out[input.key] = empty ? input.placeholder || "example" : filled;
        }
    });
    return out;
}

function previewFilled() {
    return Object.values(state.preview || {}).some((value) => value !== "" && value != null && !(Array.isArray(value) && !value.length));
}

async function renderTemplate(template, phase, index) {
    if (!config.render_url) return { error: "" };
    const results = Object.fromEntries(earlierSteps(phase, index).map((action) => [action.name, { status: "successful", output: "", result: {} }]));
    const result = await api(config.render_url, { template, inputs: sampleInputs(), results });
    if (!result.ok) return { error: (result.data && result.data.detail) || "The preview couldn’t be rendered." };
    const error = result.data.error || "";
    if (error && /results/.test(template) && /undefined/i.test(error)) return { error: "Uses a result field the preview can’t know yet. It’s filled in when the step runs." };
    return { rendered: result.data.rendered, error, note: previewFilled() ? "with your Launch form preview values" : "with sample values" };
}

function templateEditor({ value, onChange, phase, index, rows, placeholder, label, preview }) {
    return jinjaEditor({
        value,
        rows: rows || 1,
        placeholder: placeholder || "{{ inputs.device.name }}",
        label,
        references: () => referencesFor(phase, index),
        known: () => ({ inputs: payload.inputs.map((input) => input.key).filter(Boolean), results: earlierSteps(phase, index).map((action) => action.name) }),
        render: preview === false ? null : (template) => renderTemplate(template, phase, index),
        onChange,
    }).el;
}

function paramsEditor(phase, action, index) {
    const awx = action.type === "awx";
    const field = awx ? "extra_vars" : "with";
    const raw = action[field];
    const title = awx ? "Extra variables" : "Parameters";
    if (typeof raw === "string" && raw.trim()) {
        return h("div", { class: "nwe-params" }, [
            h("div", { class: "nwe-params-head" }, [h("label", { class: "form-label mb-0", text: title })]),
            h("p", { class: "nwe-muted small mb-2", text: "These come from one template that renders to a mapping of names to values." }),
            templateEditor({
                value: raw,
                rows: 3,
                phase,
                index,
                label: title,
                onChange: (value) => {
                    action[field] = value;
                    touch();
                    paintStepSummary(phase, index);
                },
            }),
        ]);
    }
    const meta = awx ? null : scriptMeta(action);
    const declared = meta ? meta.params || [] : [];
    const map = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
    const order = [...declared.map((param) => param.name).filter((name) => name in map), ...Object.keys(map).filter((name) => !declared.some((param) => param.name === name))];
    const asks = declared.length ? h("div", { class: "nwe-asks" }, [
        h("span", { class: "nwe-muted", text: meta.declared ? (awx ? "This template asks for:" : "This script asks for:") : "This script reads:" }),
        ...declared.map((param) => {
            const set = param.name in map;
            if (set) return h("span", { class: "nwe-tag green" }, [icon("check"), param.name]);
            return h("button", { type: "button", class: `nwe-tag ${param.required ? "red" : ""} nwe-tag-btn`, "data-action": "add-param", "data-key": param.name, "data-phase": phase, "data-index": String(index), title: param.required ? "Required. Click to add it." : "Optional. Click to add it." }, [icon(param.required ? "alert-circle-outline" : "plus"), param.name]);
        }),
    ]) : null;
    return h("div", { class: "nwe-params" }, [
        h("div", { class: "nwe-params-head" }, [
            h("label", { class: "form-label mb-0", text: title }),
            !meta && !awx && action.script ? h("span", { class: "nwe-muted small", text: "This script doesn’t list its parameters." }) : null,
        ]),
        asks,
        order.length ? h("div", { class: "nwe-param-list" }, order.map((key) => paramRow(phase, action, index, key, map[key], declared.find((param) => param.name === key)))) : h("p", { class: "nwe-muted small mb-1", text: awx ? "No extra variables. The template runs with its own defaults." : "No parameters yet." }),
        h("button", { type: "button", class: "nwe-link", "data-action": "add-var", "data-phase": phase, "data-index": String(index) }, [icon("plus"), awx ? "Add variable" : "Add parameter"]),
    ]);
}

function paramRow(phase, action, index, key, value, decl) {
    const id = `${phase}:${index}`;
    const mode = (action._modes || {})[key] || paramMode(value).mode;
    const rowId = `nwe-p-${phase}-${index}-${key}`;
    const name = decl
        ? h("div", { class: "nwe-param-name" }, [
            h("code", { text: key }),
            decl.required ? h("span", { class: "nwe-req", title: "Required", text: "*" }) : null,
            decl.type ? h("small", { text: PARAM_TYPE_LABEL[decl.type] || decl.type }) : null,
        ])
        : h("input", { class: "form-control form-control-sm nwe-code nwe-param-key", "data-var-key": id, "data-old": key, value: key, "aria-label": "Parameter name", spellcheck: "false" });
    return h("div", { class: `nwe-param m-${mode}`, "data-param": key }, [
        h("div", { class: "nwe-param-top" }, [
            name,
            h("div", { class: "nwe-seg nwe-seg-sm", role: "radiogroup", "aria-label": `Where ${key} comes from` }, PARAM_MODES.map(([value_, label, glyph]) => h("button", {
                type: "button",
                role: "radio",
                "aria-checked": mode === value_ ? "true" : "false",
                class: mode === value_ ? "on" : "",
                "data-action": "param-mode",
                "data-mode": value_,
                "data-key": key,
                "data-phase": phase,
                "data-index": String(index),
            }, [icon(glyph), label]))),
            h("button", { type: "button", class: "nwe-iconbtn danger", "data-action": "rm-var", "data-phase": phase, "data-index": String(index), "data-key": key, title: "Remove", "aria-label": `Remove ${key}` }, [icon("close")]),
        ]),
        h("div", { class: "nwe-param-value", id: rowId }, [paramValue(phase, action, index, key, value, decl, mode)]),
        decl && decl.help ? h("div", { class: "nwe-param-help", text: decl.help }) : null,
    ]);
}

function paramValue(phase, action, index, key, value, decl, mode) {
    const data = { "data-param-key": key, "data-phase": phase, "data-index": String(index) };
    if (mode === "input") {
        const read = paramMode(value);
        const current = read.mode === "input" ? read.input : "";
        const input = inputByKey(current);
        const usable = payload.inputs.filter((item) => item.key);
        const select = h("select", { class: `form-select form-select-sm ${current && !input ? "is-invalid" : ""}`, "data-param-input": "1", ...data, "aria-label": `Input for ${key}` }, [
            !current ? h("option", { value: "", text: "Choose an input…", selected: true }) : null,
            current && !input ? h("option", { value: current, text: `Missing: ${current}`, selected: true }) : null,
            ...usable.map((item) => h("option", { value: item.key, text: `${item.label || item.key} · ${TYPE_LABEL[item.type] || item.type}`, selected: item.key === current })),
        ]);
        const attrs = input && input.type === "object"
            ? h("select", { class: "form-select form-select-sm nwe-param-attr", "data-param-attr": "1", ...data, "aria-label": `Which part of ${input.label || input.key}` }, [
                ["id", "its ID"],
                ["name", "its name"],
                ["", "the whole object"],
            ].map(([attr, label]) => h("option", { value: attr, text: label, selected: read.attr === attr })))
            : null;
        return h("div", { class: "nwe-param-input" }, [select, attrs]);
    }
    if (mode === "expr") {
        return templateEditor({
            value: typeof value === "string" ? value : fixedText(value),
            phase,
            index,
            label: `Expression for ${key}`,
            onChange: (next) => {
                varBucket(action)[key] = next;
                touch();
                paintStepSummary(phase, index);
            },
        });
    }
    const kind = (decl && decl.type) || (typeof value === "boolean" ? "boolean" : typeof value === "number" ? "integer" : "");
    if (kind === "boolean") {
        return h("select", { class: "form-select form-select-sm nwe-param-fixed", "data-param-fixed": "1", ...data, "aria-label": `Value for ${key}` }, [
            ["true", "Yes"],
            ["false", "No"],
        ].map(([raw, label]) => h("option", { value: raw, text: label, selected: String(value === true || value === "true") === raw })));
    }
    const hint = decl && decl.default !== undefined ? `Default: ${decl.default}` : "";
    return h("input", {
        class: "form-control form-control-sm nwe-param-fixed",
        type: kind === "integer" ? "number" : "text",
        "data-param-fixed": "1",
        ...data,
        value: fixedText(value),
        placeholder: hint,
        "aria-label": `Value for ${key}`,
    });
}

/** Best guess when a parameter switches to "From an input": same key, then same model, then the first input. */
function guessInput(key, decl) {
    const byKey = inputByKey(key);
    if (byKey) return byKey;
    if (decl && decl.model) {
        const byModel = payload.inputs.find((item) => item.model && item.model.toLowerCase() === decl.model.toLowerCase());
        if (byModel) return byModel;
    }
    return payload.inputs.find((item) => item.key) || null;
}

function refFor(input, decl) {
    if (!input) return "";
    const wantsId = input.type === "object" && (!decl || !decl.type || decl.type === "object" || decl.type === "string");
    return inputRef(input.key, wantsId ? "id" : "");
}

function declFor(action, key) {
    const meta = scriptMeta(action);
    return meta ? (meta.params || []).find((param) => param.name === key) || null : null;
}

function setParamMode(phase, index, key, mode) {
    const action = payload.actions[phase][index];
    const map = varBucket(action);
    const decl = declFor(action, key);
    const value = map[key];
    const from = (action._modes || {})[key] || paramMode(value).mode;
    if (from === mode) return;
    if (mode === "input") map[key] = refFor(guessInput(key, decl), decl);
    else if (mode === "fixed") map[key] = from === "expr" && !isTemplate(value) ? value : decl && decl.default !== undefined ? decl.default : "";
    else map[key] = value === undefined || value === null ? "" : typeof value === "string" ? value : fixedText(value);
    action._modes = { ...(action._modes || {}), [key]: mode };
    renderEditor();
    paintStepSummary(phase, index);
    touch();
    const host = document.getElementById(`nwe-p-${phase}-${index}-${key}`);
    const target = host && host.querySelector("select, input, textarea");
    if (target) target.focus();
}

function whenEditor(phase, action, index) {
    const advanced = action._whenMode === "jinja" || (action.when && !parseWhen(action.when));
    if (advanced) {
        return h("div", {}, [
            h("div", { class: "nwe-params-head" }, [
                h("label", { class: "form-label mb-0", text: "Run only if" }),
                h("button", { type: "button", class: "nwe-link muted", "data-action": "when-visual", "data-phase": phase, "data-index": String(index) }, [icon("filter-variant"), "Back to the condition builder"]),
            ]),
            templateEditor({
                value: action.when || "",
                phase,
                index,
                placeholder: "{{ inputs.wait_for_device }}",
                label: "Condition",
                onChange: (next) => {
                    action.when = next;
                    touch();
                },
            }),
            h("div", { class: "nwe-param-help", text: "The step runs when this renders to true, yes, on, y or 1. Anything else skips it." }),
        ]);
    }
    const row = parseWhen(action.when);
    if (!row) {
        return h("div", {}, [
            h("label", { class: "form-label", text: "Condition" }),
            h("div", { class: "nwe-inline" }, [
                h("button", { type: "button", class: "nwe-link", "data-action": "add-when", "data-phase": phase, "data-index": String(index) }, [icon("plus"), "Run only if…"]),
                h("span", { class: "nwe-muted", text: "Always runs" }),
            ]),
        ]);
    }
    const inputs = payload.inputs.filter((item) => ["boolean", "choice", "string", "integer", "number"].includes(item.type) && item.key);
    const steps = earlierSteps(phase, index);
    const attrs = (part) => ({ class: "form-select form-select-sm", "data-when": part, "data-phase": phase, "data-index": String(index) });
    const subject = row.kind === "step" ? `step:${row.step}` : `input:${row.input}`;
    const subjects = h("select", { ...attrs("subject"), "aria-label": "What to check" }, [
        h("optgroup", { label: "Launch form" }, inputs.map((item) => h("option", { value: `input:${item.key}`, text: item.label || item.key, selected: subject === `input:${item.key}` }))),
        steps.length
            ? h("optgroup", { label: "Earlier steps" }, steps.map((item) => h("option", { value: `step:${item.name}`, text: `${item.label || item.name} · ${PHASE_SHORT[phaseOf(item)] || ""}`.replace(/ · $/, ""), selected: subject === `step:${item.name}` })))
            : null,
    ]);
    let rest;
    let second = null;
    if (row.kind === "step") {
        const output = String(row.test).startsWith("output");
        rest = [h("select", { ...attrs("test"), class: "form-select form-select-sm nwe-cond-test", "aria-label": "Outcome" }, STEP_TESTS.map(([value, text]) => h("option", { value, text, selected: row.test === value })))];
        if (output) {
            second = h("div", { class: "nwe-cond nwe-cond-sub" }, [
                h("span", { class: "nwe-where", text: "Field" }),
                h("input", { class: "form-control form-control-sm nwe-cond-field", "data-when": "field", "data-phase": phase, "data-index": String(index), value: row.field || "", placeholder: "whole output", "aria-label": "Output field, optional" }),
                h("span", { class: "nwe-where", text: row.test === "output_not" ? "is not" : "is" }),
                h("input", { class: "form-control form-control-sm", "data-when": "value", "data-phase": phase, "data-index": String(index), value: row.value === true ? "true" : row.value === false ? "false" : row.value ?? "", placeholder: "true, 3 or some text", "aria-label": "Value" }),
            ]);
        }
    } else {
        const source = inputByKey(row.input);
        rest = [
            h("select", { ...attrs("op"), "aria-label": "Comparison" }, ["is", "is not"].map((op) => h("option", { value: op, text: op, selected: row.op === op }))),
            whenValue(source, row, phase, index),
        ];
    }
    const missing = row.kind === "step" && !steps.some((item) => item.name === row.step);
    return h("div", {}, [
        h("label", { class: "form-label", text: "Condition" }),
        h("div", { class: "nwe-cond" }, [
            h("span", { class: "nwe-where", text: "Run only if" }),
            subjects,
            ...rest,
            h("span", { class: "nwe-cond-tools" }, [
                h("button", { type: "button", class: "nwe-link muted", "data-action": "when-jinja", "data-phase": phase, "data-index": String(index) }, [icon("code-braces"), "Advanced (Jinja)"]),
                h("button", { type: "button", class: "nwe-iconbtn danger", "data-action": "clear-when", "data-phase": phase, "data-index": String(index), title: "Always run", "aria-label": "Remove the condition" }, [icon("close")]),
            ]),
        ]),
        second,
        second ? h("div", { class: "nwe-param-help", text: "Leave Field empty to compare the whole output. Use dots for nested fields, for example facts.os." }) : null,
        missing
            ? h("div", { class: "nwe-param-help text-danger", text: `“${stepLabel(row.step)}” doesn’t run before this step any more. Pick an earlier step.` })
            : row.kind === "step"
                ? h("div", { class: "nwe-param-help", text: "If that step never ran, the condition counts as not matching." })
                : null,
    ]);
}

const FALLBACK_ZONES = [{ name: "UTC", offset: "UTC+00:00", common: true }];

function zoneLabel(name) {
    return String(name || "").replace(/_/g, " ");
}

/**
 * Searchable timezone dropdown (combobox). Common zones first, then every zone
 * Python's zoneinfo knows. Empty in the YAML means UTC.
 */
function timezonePicker(current) {
    const zones = config.timezones && config.timezones.length ? config.timezones : FALLBACK_ZONES;
    const known = new Map(zones.map((zone) => [zone.name, zone]));
    let selected = current && known.has(current) ? current : current ? current : "UTC";
    let open = false;
    let rows = [];
    let active = -1;
    const listId = "nwe-tz-list";
    const input = h("input", {
        class: "form-control nwe-tz-input",
        id: "nwe-tz",
        role: "combobox",
        autocomplete: "off",
        spellcheck: "false",
        "aria-autocomplete": "list",
        "aria-label": "Change window timezone",
        "aria-expanded": "false",
        "aria-controls": listId,
        value: zoneLabel(selected),
    });
    const offset = h("span", { class: "nwe-tz-off", "aria-hidden": "true", text: known.get(selected)?.offset || "" });
    const chevron = h("span", { class: "nwe-tz-chev", "aria-hidden": "true" }, [icon("chevron-down")]);
    const list = h("div", { class: "nwe-pop nwe-ac nwe-tz-list", id: listId, role: "listbox", hidden: "hidden" });
    const wrap = h("div", { class: "nwe-tz" }, [input, offset, chevron, list]);

    function matches(query) {
        const needle = query.trim().toLowerCase().replace(/[\s_]+/g, " ");
        if (!needle) return zones;
        return zones.filter((zone) => `${zoneLabel(zone.name)} ${zone.offset}`.toLowerCase().includes(needle));
    }

    function paint(query) {
        rows = matches(query);
        const nodes = [];
        const shown = rows.slice(0, 300);
        let section = null;
        shown.forEach((zone, index) => {
            const group = query.trim() ? "Matches" : zone.common ? "Common" : "All timezones";
            if (group !== section && !query.trim()) {
                nodes.push(h("div", { class: "nwe-pop-sec", role: "presentation", text: group }));
            }
            section = group;
            nodes.push(h("button", {
                type: "button",
                tabindex: "-1",
                class: `nwe-pop-item${index === active ? " hl" : ""}${zone.name === selected ? " on" : ""}`,
                role: "option",
                id: `nwe-tz-opt-${index}`,
                "aria-selected": zone.name === selected ? "true" : "false",
                "data-zone": zone.name,
            }, [
                h("span", { class: "nwe-tz-check", "aria-hidden": "true" }, zone.name === selected ? [icon("check")] : []),
                h("span", { class: "nwe-tz-name", text: zoneLabel(zone.name) }),
                h("small", { text: zone.offset }),
            ]));
        });
        if (!shown.length) nodes.push(h("div", { class: "nwe-tz-empty", text: `No timezone matches “${query.trim()}”.` }));
        if (rows.length > shown.length) nodes.push(h("div", { class: "nwe-ac-foot", text: `${rows.length - shown.length} more. Keep typing to narrow it down.` }));
        list.replaceChildren(...nodes);
        input.setAttribute("aria-activedescendant", active >= 0 ? `nwe-tz-opt-${active}` : "");
        list.querySelector(".hl")?.scrollIntoView({ block: "nearest" });
    }

    function show(query) {
        open = true;
        list.hidden = false;
        input.setAttribute("aria-expanded", "true");
        active = Math.max(0, matches(query).findIndex((zone) => zone.name === selected));
        paint(query);
        if (!query) list.querySelector(".on")?.scrollIntoView({ block: "center" });
    }

    function hide() {
        open = false;
        list.hidden = true;
        input.setAttribute("aria-expanded", "false");
        input.removeAttribute("aria-activedescendant");
    }

    function choose(name) {
        selected = name;
        input.value = zoneLabel(name);
        offset.textContent = known.get(name)?.offset || "";
        hide();
        assign("schedule.timezone", name);
        touch();
        const summary = wrap.closest("details")?.querySelector("summary");
        if (summary) summary.textContent = `Advanced · timezone (${zoneLabel(name)}), ServiceNow field names, ticket pattern`;
    }

    input.addEventListener("focus", () => {
        input.select();
        show("");
    });
    input.addEventListener("click", () => {
        if (!open) show("");
    });
    input.addEventListener("input", () => {
        offset.textContent = "";
        if (!open) show(input.value);
        active = 0;
        paint(input.value);
    });
    input.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!open) return show("");
            const count = Math.min(rows.length, 300);
            if (!count) return;
            active = (active + (event.key === "ArrowDown" ? 1 : -1) + count) % count;
            paint(input.value === zoneLabel(selected) ? "" : input.value);
        } else if (event.key === "Enter") {
            if (!open) return;
            event.preventDefault();
            const zone = rows[active];
            if (zone) choose(zone.name);
        } else if (event.key === "Escape" && open) {
            event.preventDefault();
            event.stopPropagation();
            input.value = zoneLabel(selected);
            offset.textContent = known.get(selected)?.offset || "";
            hide();
        }
    });
    input.addEventListener("blur", () => {
        window.setTimeout(() => {
            if (wrap.contains(document.activeElement)) return;
            input.value = zoneLabel(selected);
            offset.textContent = known.get(selected)?.offset || "";
            hide();
        }, 120);
    });
    list.addEventListener("mousedown", (event) => event.preventDefault());
    list.addEventListener("click", (event) => {
        const item = event.target.closest("[data-zone]");
        if (item) choose(item.dataset.zone);
    });
    return wrap;
}

function scheduleView() {
    const schedule = payload.schedule;
    const textInputs = payload.inputs.filter((input) => input.type === "string" && input.key);
    return h("div", {}, [
        h("div", { class: "nwe-kicker", text: "Step 4 of 5" }),
        h("h2", { text: "Schedule & approval" }),
        h("p", { class: "text-secondary", text: "Optional guard rails: who has to approve a run, and when it’s allowed to run." }),
        h("div", { class: "nwe-card" }, [
            switchRow({ id: "nwe-approval", "data-bind": "approval_required", "data-check": "1", checked: payload.approval_required }, "Require approval before it runs", "Uses Nautobot Approval Workflows. Each run waits until it’s approved."),
        ]),
        h("div", { class: "nwe-card" }, [
            switchRow({ id: "nwe-window", "data-bind": "schedule.enabled", "data-check": "1", checked: schedule.enabled }, "Only run inside an approved ServiceNow change window", "Runs wait for the change window and are skipped if the change isn’t approved."),
            schedule.enabled ? h("div", { class: "mt-3" }, [
                field("ServiceNow connection", h("select", { class: "form-select", "data-bind": "schedule.integration_name" }, [
                    h("option", { value: "", text: "Choose a connection…" }),
                    ...(config.integrations || []).map((item) => h("option", { value: item.name, text: item.url ? `${item.name} · ${item.url}` : item.name, selected: schedule.integration_name === item.name })),
                ]), "Pick one of Nautobot’s External Integrations."),
                field("Which input holds the change number?", h("select", { class: "form-select", "data-bind": "schedule.change_input_key" }, [
                    h("option", { value: "", text: "Choose an input…" }),
                    ...textInputs.map((input) => h("option", { value: input.key, text: input.label || input.key, selected: schedule.change_input_key === input.key })),
                ])),
                gridField("Allowed change states", h("div", { class: "nwe-checks" }, (config.snow_states || []).map((name) => checkChip({ "data-action": "snow-state", "data-state": name }, (schedule.allowed_states || []).includes(name), name))), "The run is only allowed while the change is in one of these states."),
                h("details", { class: "nwe-advanced", "data-adv": "schedule", open: state.openAdvanced === "schedule" ? true : null }, [
                    h("summary", { text: `Advanced · timezone (${zoneLabel(schedule.timezone || "UTC")}), ServiceNow field names, ticket pattern` }),
                    field("Change window timezone", timezonePicker(schedule.timezone || ""), "Used to read change window times that ServiceNow sends without a timezone. Defaults to UTC."),
                    field("Window start fields", h("input", { class: "form-control", "data-bind": "schedule.start_fields", value: (schedule.start_fields || []).join(", ") })),
                    field("Window end fields", h("input", { class: "form-control", "data-bind": "schedule.end_fields", value: (schedule.end_fields || []).join(", ") })),
                    field("Ticket pattern", h("input", { class: "form-control", "data-bind": "schedule.ticket_regex", value: schedule.ticket_regex || "" })),
                ]),
            ]) : null,
        ]),
    ]);
}

function reviewView() {
    const block = publishBlock();
    const changes = changeList();
    return h("div", {}, [
        h("div", { class: "nwe-kicker", text: "Step 5 of 5" }),
        h("h2", { text: "Review & publish" }),
        h("p", { class: "text-secondary", text: config.published_number ? "Until you publish, people keep running the published workflow." : "Check it, then publish it for the first time. Until then nobody can run it." }),
        checks(),
        h("div", { class: "nwe-card", id: "nwe-review-diff" }, [
            h("div", { class: "fw-semibold", text: "What changes for people running it" }),
            h("div", { class: "small text-secondary mb-2", text: config.published_number ? "Compared with the published workflow, which runs today." : "Nothing is published yet, so everything is new." }),
            ...changes.map((change) => h("div", { class: "nwe-change" }, [h("span", { class: `nwe-change-mark ${change[0] === "+" ? "add" : change[0] === "−" ? "del" : "mod"}`, text: change[0] }), change[1]])),
            h("button", { type: "button", class: "nwe-link", "data-action": "yaml-diff" }, [icon(state.yamlDiff ? "chevron-up" : "chevron-down"), state.yamlDiff ? "Hide the YAML diff" : "Show the YAML diff (what Git gets)"]),
            state.yamlDiff ? diffView(lineDiff(String(config.published_yaml || ""), state.yamlText || "")) : null,
        ]),
        h("div", { class: "nwe-card", id: "nwe-publish-card" }, [
            config.can_publish
                ? h("div", {}, [
                    h("div", { class: "fw-semibold", text: publishText() }),
                    h("ol", {}, [
                        h("li", { text: "Adds it to Versions with your name, the time and your note." }),
                        h("li", { text: `Commits ${payload.key || "workflow"}.yaml${config.repository_name ? ` to ${config.repository_name}` : ""} and pushes it.` }),
                        h("li", { text: "Makes it the version everyone runs, right away. Runs already in progress finish on their version." }),
                    ]),
                    field("Note · optional", h("input", { class: "form-control", id: "nwe-note", "data-note": "1", value: state.note, placeholder: "Shown in Versions and used as the Git commit message." })),
                    !config.published_number
                        ? switchRow({ id: "nwe-enable-pub", "data-action": "enable-on-publish", checked: state.enableOnPublish }, "Turn it on when it’s published", "Off means it’s published but nobody can run it yet.")
                        : null,
                    h("div", { class: "d-flex align-items-center gap-2 mt-3" }, [
                        block
                            ? h("span", { class: block.issues ? "text-danger small" : "text-secondary small", text: block.issues ? "Not ready: see the checks above." : block.message })
                            : h("span", { class: "text-success small", text: "Ready to publish" }),
                        h("button", { type: "button", class: "btn nwe-btn ms-auto", "data-action": "test" }, [icon("play-circle-outline"), "Preview run"]),
                        h("button", { type: "button", class: "btn nwe-publish", "data-action": "publish", disabled: Boolean(block), text: publishText() }),
                    ]),
                ])
                : h("div", {}, [
                    h("div", { class: "fw-semibold", text: "Request publish" }),
                    h("p", { class: "text-secondary", text: "You can edit, and your draft keeps saving automatically. Publishing needs the Publish workflows permission." }),
                    h("div", { class: "input-group" }, [
                        h("input", { class: "form-control", readonly: "readonly", value: window.location.href }),
                        h("button", { type: "button", class: "btn btn-primary", "data-action": "copy-link", text: "Copy link" }),
                    ]),
                ]),
        ]),
    ]);
}

function checks() {
    const buckets = localIssues();
    const rows = [
        ["details", "Details", payload.name || "No name yet"],
        ["inputs", "Inputs", `${payload.inputs.length} fields on the launch form`],
        ["steps", "Steps", `${(payload.actions.pre || []).length} before · ${(payload.actions.main || []).length} main · ${(payload.actions.post || []).length} at the end`],
        ["schedule", "Schedule & approval", [payload.approval_required ? "Needs approval" : "No approval", payload.schedule.enabled ? "ServiceNow change window" : "Runs any time"].join(" · ")],
    ];
    return h("div", { class: "nwe-card" }, [
        h("div", { class: "nwe-card-title", text: "Checks" }),
        ...rows.map(([id, label, detail]) => h("div", { class: `nwe-check-row${buckets[id].length ? " bad" : ""}` }, [
            h("span", { class: "nwe-check-ico" }, [icon(buckets[id].length ? "alert-circle-outline" : "check-circle-outline")]),
            h("div", { class: "flex-grow-1" }, [
                h("b", { text: label }),
                h("small", { text: detail }),
                ...buckets[id].slice(0, 3).map((message) => h("small", { class: "bad", text: message })),
            ]),
            h("button", { type: "button", class: "nwe-link", "data-action": "goto", "data-section": id }, [icon("pencil-outline"), "Edit"]),
        ])),
    ]);
}

function changeList() {
    const published = config.published_payload;
    if (!published) {
        return [
            ["+", `A launch form with ${payload.inputs.length} fields`],
            ["+", `${allSteps().length} steps`],
        ];
    }
    const out = [];
    const oldInputs = new Map((published.inputs || []).map((input) => [input.key, input]));
    const newInputs = new Map(payload.inputs.map((input) => [input.key, input]));
    payload.inputs.forEach((input) => {
        if (!oldInputs.has(input.key)) out.push(["+", `New field ${input.label || input.key}`]);
    });
    (published.inputs || []).forEach((input) => {
        if (!newInputs.has(input.key)) out.push(["−", `Removed field ${input.label || input.key}`]);
    });
    const oldNames = new Set(PHASES.flatMap(([phase]) => ((published.actions || {})[phase] || []).map((action) => action.name)));
    allSteps().forEach((action) => {
        if (action.name && !oldNames.has(action.name)) out.push(["+", `New step ${action.label || action.name}`]);
    });
    if ((published.description || "") !== (payload.description || "")) {
        out.push(["~", "The description changed"]);
    }
    if ((published.manual_duration_minutes || "") !== (payload.manual_duration_minutes || "")) {
        out.push(["~", `Manual effort per run: ${published.manual_duration_minutes ?? "—"} → ${payload.manual_duration_minutes ?? "—"} minutes`]);
    }
    PHASES.forEach(([phase]) => {
        const previous = new Map(((published.actions || {})[phase] || []).map((action) => [action.name, action]));
        (payload.actions[phase] || []).forEach((action) => {
            const old = previous.get(action.name);
            if (!old) return;
            if ((old.script || "") !== (action.script || "")) {
                out.push(["~", `${action.label || action.name}: the script changed`]);
            }
            if ((old.template_id || "") !== (action.template_id || "") && action.type === "awx") {
                out.push(["~", `${action.label || action.name}: the AWX template changed`]);
            }
        });
    });
    if (!out.length) out.push(["~", "No changes people would notice, or only details inside a field."]);
    return out;
}

function lineDiff(before, after) {
    const left = String(before || "").split("\n");
    const right = String(after || "").split("\n");
    const lines = [];
    const length = Math.max(left.length, right.length);
    for (let index = 0; index < length; index += 1) {
        if (left[index] === right[index]) lines.push([" ", left[index] ?? ""]);
        else {
            if (left[index] != null) lines.push(["-", left[index]]);
            if (right[index] != null) lines.push(["+", right[index]]);
        }
    }
    return lines.filter((line, index) => line[0] !== " " || lines.slice(Math.max(0, index - 1), index + 2).some((item) => item[0] !== " "));
}

function diffView(lines) {
    return h("div", { class: "nwe-diff" }, lines.map((line) => h("div", { class: line[0] === "+" ? "add" : line[0] === "-" ? "del" : "", text: `${line[0]} ${line[1]}` })));
}

function yamlView() {
    const locked = !config.can_author_code;
    const lines = String(state.yamlText || "").split("\n");
    const bad = new Set(state.yamlLine ? [state.yamlLine] : []);
    return h("div", { class: "nwe-section" }, [
        h("div", { class: "nwe-kicker", text: "Edit as YAML" }),
        h("h2", { text: `${payload.key || "workflow"}.yaml` }),
        h("p", { class: "nwe-sub", text: locked ? "You need the Author templates and scripts permission to edit YAML." : "The draft, as YAML. Edits apply to the draft. Git gets this file when you publish." }),
        h("div", { class: "nwe-code-editor" }, [
            h("div", { class: "nwe-gutter", id: "nwe-gutter" }, lines.map((line, index) => h("div", { class: bad.has(index + 1) ? "bad" : "", text: String(index + 1) }))),
            h("textarea", { class: "nwe-yaml", id: "nwe-yaml", "data-yaml": "1", disabled: locked, text: state.yamlText || "" }),
        ]),
        state.yamlError ? h("div", { class: "text-danger small mt-2", text: state.yamlError }) : h("div", { class: "text-success small mt-2", text: state.yamlText ? "Valid · edits apply to the draft as you type" : "" }),
    ]);
}

function renderPreview() {
    const host = document.getElementById("studio-preview");
    const tab = state.previewTab;
    host.replaceChildren(
        h("div", { class: "nwe-preview-card" }, [
            h("div", { class: "nwe-preview-bar" }, [
                h("span", { class: "nwe-kicker", text: "Preview" }),
                h("div", { class: "nwe-seg" }, [
                    h("button", { type: "button", class: tab === "form" ? "on" : "", "data-action": "preview-tab", "data-tab": "form", text: "Launch form" }),
                    h("button", { type: "button", class: tab === "plan" ? "on" : "", "data-action": "preview-tab", "data-tab": "plan", text: "What runs" }),
                ]),
            ]),
            h("div", { class: "nwe-preview-body" }, [
                tab === "form" ? h("div", { class: "nwe-preview-head" }, [
                    h("div", { class: "nwe-preview-title", text: payload.name || "Untitled workflow" }),
                    payload.description ? h("div", { class: "nwe-sub", text: payload.description }) : null,
                ]) : null,
                tab === "form" ? previewForm() : previewPlan(),
            ]),
            h("div", { class: "nwe-preview-foot", text: tab === "form" ? "The draft’s launch form. People running it see the published version until you publish." : "The order things happen when someone runs it." }),
        ]),
    );
}

// Icons for the object types people pick most often (matches launch_ui.MODEL_ICONS).
const MODEL_ICONS = {
    "dcim.device": "server",
    "dcim.location": "map-marker-outline",
    "dcim.interface": "ethernet",
    "dcim.rack": "server-network",
    "ipam.vlan": "lan",
    "ipam.prefix": "ip-network-outline",
    "ipam.ipaddress": "ip-outline",
    "extras.tag": "tag-outline",
    "tenancy.tenant": "account-outline",
};

function modelNouns(model) {
    const name = String(model || "record").split(".").pop().toLowerCase();
    const known = { ipaddress: ["IP address", "IP addresses"], vlan: ["VLAN", "VLANs"], devicetype: ["device type", "device types"], vlangroup: ["VLAN group", "VLAN groups"] };
    if (known[name]) return known[name];
    return [name, /(s|x|ch|sh)$/.test(name) ? `${name}es` : /[^aeiou]y$/.test(name) ? `${name.slice(0, -1)}ies` : `${name}s`];
}

/** The builder's input, in the shape the launch page's field components take. */
function previewSpec(input) {
    const spec = {
        key: input.key || `input-${payload.inputs.indexOf(input)}`,
        label: input.label || "Untitled",
        type: input.type || "string",
        required: Boolean(input.required),
        help: input.help_text || "",
        placeholder: input.placeholder || "",
        choices: (input.choices || []).filter((choice) => choice && (choice.value || choice.label)).map((choice) => ({ value: String(choice.value ?? choice.label), label: choice.label || String(choice.value) })),
        min_value: input.min_value,
        max_value: input.max_value,
        min_items: input.min_items,
        max_items: input.max_items,
        columns: input.columns || [],
        min_rows: input.min_rows,
        max_rows: input.max_rows,
        csv_import: input.csv_import !== false,
    };
    if (spec.type === "integer" && (input.min_value !== "" && input.min_value != null || input.max_value !== "" && input.max_value != null)) {
        const low = input.min_value !== "" && input.min_value != null ? input.min_value : null;
        const high = input.max_value !== "" && input.max_value != null ? input.max_value : null;
        const range = low != null && high != null ? `Between ${low} and ${high}.` : low != null ? `At least ${low}.` : `Up to ${high}.`;
        spec.help = spec.help ? `${spec.help} ${range}` : range;
    }
    if (input.model) {
        const [noun, nouns] = modelNouns(input.model);
        Object.assign(spec, { model: input.model, icon: MODEL_ICONS[String(input.model).toLowerCase()] || "cube-outline", noun, nouns });
    }
    return spec;
}

function previewOptions(input) {
    const index = payload.inputs.indexOf(input);
    const match = (state.match || {})[index];
    if (input.model && !(state.modelExamples || {})[input.model]) ensureFields(input.model);
    const examples = (match && match.examples && match.examples.length ? match.examples : (state.modelExamples || {})[input.model]) || [];
    const total = match && match.count != null ? match.count : examples.length;
    return { options: examples.map((item) => ({ value: String(item.id), label: item.label })), total, hasMore: false };
}

function previewDefault(input) {
    const raw = input.default;
    if (raw === undefined || raw === null || raw === "") return undefined;
    if (input.type === "boolean") return raw === true || String(raw).toLowerCase() === "true";
    if (["multichoice", "multiobject", "list"].includes(input.type)) {
        if (Array.isArray(raw)) return raw.map(String);
        return String(raw).split(input.type === "list" ? /\n/ : /[,\n]/).map((part) => part.trim()).filter(Boolean);
    }
    if (input.type === "listdict") return Array.isArray(raw) ? raw : undefined;
    return String(raw);
}

function previewForm() {
    if (!payload.inputs.length) return h("p", { class: "text-secondary", text: "Add an input to see it here." });
    return h("div", { class: "nwe-fields" }, payload.inputs.map((input, index) => {
        const editing = state.section === "inputs" && state.openInput === index;
        const spec = previewSpec(input);
        const field = createField(spec, {
            value: Object.prototype.hasOwnProperty.call(state.preview, input.key) ? state.preview[input.key] : previewDefault(input),
            onChange: (key, value) => {
                state.preview[input.key] = value;
                payload.inputs.forEach((other, otherIndex) => {
                    if ((other.filter_rows || []).some((row) => row.source === "input" && row.input === input.key)) scheduleCount(otherIndex);
                });
            },
            loadOptions: async () => previewOptions(input),
        });
        if (input.model) field.reload();
        return h("div", { class: `nwe-prev-field ${editing ? "editing" : ""}` }, [
            editing ? h("span", { class: "nwe-editing", text: "Editing" }) : null,
            field.el,
        ]);
    }));
}

function stepEffect(phase, action) {
    if (phase === "pre") return "stops the run if it fails";
    if (phase === "post") return "runs even after a failure";
    if (action.type === "awx" && action.monitor !== false) return "waits for the job to finish";
    return "continues on failure";
}

/** The same rows as the launch page's "What happens" panel, built from the draft. */
function planRows() {
    const rows = [{ icon: "form-textbox", title: "Someone fills in the form", sub: payload.inputs.length === 1 ? "1 field" : `${payload.inputs.length} fields` }];
    if (payload.approval_required) rows.push({ icon: "account-check-outline", title: "Waits for approval", sub: "Each run waits until it’s approved" });
    const schedule = payload.schedule || {};
    if (schedule.enabled) {
        const states = schedule.allowed_states || [];
        rows.push({ icon: "calendar-clock", title: "Waits for the change window", sub: states.length ? `Change must be ${states.join(" or ")}` : "Runs inside the change’s window" });
    }
    PHASES.forEach(([phase]) => {
        const list = payload.actions[phase] || [];
        if (!list.length) return;
        rows.push({ phase: PHASE_META[phase].label });
        list.forEach((action) => {
            const when = action.when ? `Only if ${whenPlain(action)}` : "";
            const effect = stepEffect(phase, action);
            rows.push({ icon: action.type === "awx" ? "ansible" : "script-text-outline", title: action.label || action.name || "Untitled step", sub: when ? `${when} · ${effect}` : effect });
        });
    });
    const minutes = Number(payload.manual_duration_minutes) || 0;
    rows.push({ icon: "check", title: "Done", sub: minutes ? `Dashboard records ~${minutes} min of manual effort, minus the run time` : "Run history records who ran it and what happened", done: true });
    return rows;
}

function previewPlan() {
    return h("div", {}, [
        h("ol", { class: "nwe-wh" }, planRows().map((row) => row.phase
            ? h("li", { class: "phase", text: row.phase })
            : h("li", { class: `step${row.done ? " done" : ""}` }, [
                h("span", { class: "ic", "aria-hidden": "true" }, [icon(row.icon)]),
                h("span", { class: "tx" }, [h("b", { text: row.title }), row.sub ? h("small", { text: row.sub }) : null]),
            ]))),
        h("button", { type: "button", class: "btn nwe-btn w-100 mt-3", "data-action": "test" }, [icon("play-circle-outline"), "Preview run"]),
        h("p", { class: "nwe-plan-note", text: "Uses the values in the Launch form tab. Nothing is executed." }),
    ]);
}

function assign(path, value) {
    if (path.endsWith(".allowed_states") || path.endsWith(".start_fields") || path.endsWith(".end_fields")) {
        setPath(payload, path, String(value).split(",").map((part) => part.trim()).filter(Boolean));
        return;
    }
    if (path === "manual_duration_minutes" || path.endsWith(".min_value") || path.endsWith(".max_value") || path.endsWith(".min_items") || path.endsWith(".max_items") || path.endsWith(".min_rows") || path.endsWith(".max_rows") || path.endsWith(".template_id")) {
        setPath(payload, path, value === "" ? "" : Number(value));
        return;
    }
    setPath(payload, path, value);
    if (path.endsWith(".label") && path.startsWith("inputs.")) {
        const input = payload.inputs[Number(path.split(".")[1])];
        if (input && !input._keyTouched && !inputLocked(input)) input.key = keyFrom(input.label);
        const node = document.querySelector(`[data-row-label="${path.split(".")[1]}"]`);
        if (node) node.textContent = value || "Untitled";
        const title = document.getElementById("nwe-title");
        if (title && path === "name") title.textContent = value || "Untitled workflow";
    }
    if (path === "name" && !config.key && !payload._keyTouched) payload.key = slugify(value);
    if (path === "key") payload._keyTouched = true;
    if (path.endsWith(".key") && path.startsWith("inputs.")) payload.inputs[Number(path.split(".")[1])]._keyTouched = true;
    if (path.endsWith(".label") && path.startsWith("actions.")) {
        const [, phase, index] = path.split(".");
        followLabel(phase, Number(index));
        const title = app.querySelector(`[data-step-title="${phase}:${index}"]`);
        if (title) title.textContent = value || "Untitled step";
    }
    if (path.endsWith(".name") && path.startsWith("actions.")) {
        const [, phase, index] = path.split(".");
        const action = payload.actions[phase][Number(index)];
        action._nameTouched = true;
        paintKey(phase, Number(index));
    }
    if (path === "name") {
        const title = document.getElementById("nwe-title");
        if (title) title.textContent = value || "Untitled workflow";
    }
    if (path.endsWith(".field") && path.includes(".filter_rows.")) {
        const parts = path.split(".");
        const input = payload.inputs[Number(parts[1])];
        const row = input && input.filter_rows && input.filter_rows[Number(parts[3])];
        const meta = input && row ? fieldMeta(input.model, row.field) : null;
        if (row && meta && !(meta.operators || []).includes(row.op)) row.op = meta.operators[0] || "is";
    }
    if (path.includes(".filter_rows.")) scheduleCount(Number(path.split(".")[1]));
}

function countQuery(input) {
    const query = {};
    const samples = [];
    (input.filter_rows || []).forEach((row) => {
        if (!row.field) return;
        const op = row.op || "is";
        const meta = fieldMeta(input.model, row.field);
        const named = Boolean(
            meta && meta.lookup === "name" && row.source !== "input"
            && !["contains", "starts with", "is empty", "greater than", "less than"].includes(op),
        );
        let key = named ? `${row.field}__name` : row.field;
        let value = row.value;
        if (row.source === "input" && row.input) {
            const picked = state.preview[row.input];
            const sample = meta && Array.isArray(meta.examples) ? meta.examples[0] : null;
            if (picked) value = picked;
            else if (sample) {
                value = sample.id;
                samples.push(`${meta.label || row.field} = ${sample.label}`);
            } else value = `{{ inputs.${row.input}.id }}`;
        } else if (op === "is one of" || op === "is not one of") {
            value = String(row.value || "").split(",").map((part) => part.trim()).filter(Boolean);
        } else if (op === "is empty") value = true;
        if (row.source !== "input" && op !== "is empty" && (value === "" || value == null || (Array.isArray(value) && !value.length))) return;
        if (op === "is not" || op === "is not one of") key = `${key}__n`;
        else if (op === "contains") key = `${row.field}__ic`;
        else if (op === "starts with") key = `${row.field}__isw`;
        else if (op === "is empty") key = `${row.field}__isnull`;
        else if (op === "greater than") key = `${row.field}__gt`;
        else if (op === "less than") key = `${row.field}__lt`;
        query[key] = value;
    });
    return { query, samples };
}

function scheduleCount(index) {
    const input = payload.inputs[index];
    if (!input || !input.model || !config.filter_count_url) return;
    clearTimeout(input._countTimer);
    input._countTimer = window.setTimeout(async () => {
        const { query, samples } = countQuery(input);
        const result = await api(config.filter_count_url, { model: input.model, query, values: state.preview });
        const node = document.getElementById(`nwe-count-${index}`);
        if (!node) return;
        if (!result.ok || result.data.count == null) {
            node.textContent = (result.data && (result.data.detail || result.data.error)) || "The count could not be loaded.";
            return;
        }
        const names = (result.data.names || []).slice(0, 3).join(", ");
        const count = result.data.count;
        const modelName = String(input.model || "record").split(".").pop().toLowerCase();
        const noun = count === 1 ? modelName : `${modelName}s`;
        const verb = count === 1 ? "matches" : "match";
        const sampleNote = samples.length ? ` with ${samples.join(", ")}` : "";
        node.textContent = `${count} ${noun} ${verb}${sampleNote}${names ? ` · ${names}` : ""}`;
        state.counts[index] = node.textContent;
        state.match = state.match || {};
        state.match[index] = { count, examples: result.data.examples || [] };
        renderPreview();
    }, 400);
}

function paintChip(input) {
    const label = input.closest(".nwe-ck");
    if (!label) return;
    label.classList.toggle("on", input.checked);
    const box = label.querySelector(".bx");
    if (box) box.replaceChildren(...(input.checked ? [icon("check-bold")] : []));
}

function valueOf(target) {
    if (target.type === "checkbox") return target.checked;
    return target.value;
}

app.addEventListener("input", (event) => {
    const target = event.target;
    if (target.dataset.ns && state.newScript) {
        state.newScript[target.dataset.ns] = target.value;
        const [phase, index] = state.newScript.id.split(":");
        refreshScriptPreview(payload.actions[phase][Number(index)]);
        return;
    }
    if (target.dataset.modelSearch != null) {
        state.modelQuery = target.value;
        const index = target.dataset.modelSearch;
        renderEditor();
        const again = document.querySelector(`[data-model-search="${index}"]`);
        if (again) {
            again.focus();
            again.setSelectionRange(again.value.length, again.value.length);
        }
        return;
    }
    if (target.dataset.show) {
        applyShowPart(Number(target.dataset.index), target.dataset.show, target.value, target.tagName === "SELECT");
        return;
    }
    if (target.dataset.when) {
        applyWhenPart(target.dataset.phase, Number(target.dataset.index), target.dataset.when, target.value, target.tagName === "SELECT");
        return;
    }
    if (target.dataset.paramFixed && target.tagName === "INPUT") {
        const action = payload.actions[target.dataset.phase][Number(target.dataset.index)];
        const key = target.dataset.paramKey;
        const map = varBucket(action);
        const decl = declFor(action, key);
        map[key] = fixedValue(target.value, decl && decl.type, map[key]);
        action._modes = { ...(action._modes || {}), [key]: "fixed" };
        touch();
        paintStepSummary(target.dataset.phase, Number(target.dataset.index));
        return;
    }
    if (target.dataset.varVal) {
        const [phase, index] = target.dataset.varVal.split(":");
        varBucket(payload.actions[phase][Number(index)])[target.dataset.old] = target.value;
        touch();
        return;
    }
    if (target.dataset.yaml != null) {
        state.yamlText = target.value;
        clearTimeout(state.yamlTimer);
        state.yamlTimer = window.setTimeout(applyYaml, 500);
        return;
    }
    if (target.dataset.note != null) {
        state.note = target.value;
        return;
    }
    if (!target.dataset.bind) return;
    assign(target.dataset.bind, valueOf(target));
    touch();
});

app.addEventListener("change", (event) => {
    const target = event.target;
    if (target.dataset.ns) return;
    if (target.dataset.scriptPick) {
        const [phase, index] = target.dataset.scriptPick.split(":");
        const action = payload.actions[phase][Number(index)];
        if (target.value === NEW_SCRIPT) {
            state.newScript = { id: target.dataset.scriptPick, name: freeScriptName(action.label || action.name || "new script"), body: "", code: "", path: "", problems: [] };
        } else {
            if (state.newScript && state.newScript.id === target.dataset.scriptPick) state.newScript = null;
            assign(`actions.${phase}.${index}.script`, target.value);
            touch();
        }
        renderAll();
        return;
    }
    if (target.dataset.action === "enabled") {
        setEnabled(target.checked);
        return;
    }
    if (target.dataset.action === "snow-state") {
        const list = payload.schedule.allowed_states || [];
        payload.schedule.allowed_states = target.checked ? [...list, target.dataset.state] : list.filter((name) => name !== target.dataset.state);
        renderEditor();
        touch();
        return;
    }
    if (target.dataset.chip) {
        const boxes = document.querySelectorAll(`[data-chip="${CSS.escape(target.dataset.chip)}"]`);
        assign(target.dataset.chip, Array.from(boxes).filter((box) => box.checked).map((box) => box.value).join(", "));
        paintChip(target);
        touch();
        return;
    }
    if (target.dataset.multi) {
        assign(target.dataset.multi, Array.from(target.selectedOptions).map((option) => option.value).join(", "));
        touch();
        return;
    }
    if (target.dataset.show) {
        applyShowPart(Number(target.dataset.index), target.dataset.show, target.value, true);
        return;
    }
    if (target.dataset.when) {
        applyWhenPart(target.dataset.phase, Number(target.dataset.index), target.dataset.when, target.value, true);
        return;
    }
    if (target.dataset.paramInput || target.dataset.paramAttr || (target.dataset.paramFixed && target.tagName === "SELECT")) {
        const phase = target.dataset.phase;
        const index = Number(target.dataset.index);
        const action = payload.actions[phase][index];
        const key = target.dataset.paramKey;
        const map = varBucket(action);
        if (target.dataset.paramFixed) map[key] = target.value === "true";
        else if (target.dataset.paramInput) map[key] = refFor(inputByKey(target.value), declFor(action, key));
        else {
            const read = paramMode(map[key]);
            map[key] = inputRef(read.input, target.value);
        }
        renderEditor();
        paintStepSummary(phase, index);
        touch();
        return;
    }
    if (target.dataset.varKey) {
        const [phase, index] = target.dataset.varKey.split(":");
        const action = payload.actions[phase][Number(index)];
        const map = varBucket(action);
        const value = map[target.dataset.old];
        const next = target.value.trim();
        if (next && next !== target.dataset.old && next in map) {
            target.value = target.dataset.old;
            toast(`There’s already a parameter called “${next}”.`);
            return;
        }
        delete map[target.dataset.old];
        if (next) map[next] = value ?? "";
        if (action._modes && target.dataset.old in action._modes) {
            if (next) action._modes[next] = action._modes[target.dataset.old];
            delete action._modes[target.dataset.old];
        }
        renderEditor();
        touch();
        return;
    }
    if (target.dataset.action === "enable-on-publish") {
        state.enableOnPublish = target.checked;
        return;
    }
    if (!target.dataset.bind) return;
    assign(target.dataset.bind, valueOf(target));
    if (target.tagName === "SELECT" || target.type === "checkbox") renderAll();
    // A text field already saved on input; repainting again on blur would swallow the click that caused it.
    if (draftSnapshot(payload) !== lastSnapshot) touch();
});

app.addEventListener("click", (event) => {
    const node = event.target.closest("[data-action]");
    if (!node || !app.contains(node)) return;
    const action = node.dataset.action;
    if (action === "goto") {
        event.preventDefault();
        state.section = node.dataset.section;
        state.yaml = false;
        state.menu = false;
        renderAll();
    } else if (action === "menu") {
        state.menu = !state.menu;
        renderHeader();
    } else if (action === "mode") {
        state.yaml = node.dataset.mode === "yaml";
        state.menu = false;
        if (state.yaml && !state.yamlText) refreshYaml();
        renderAll();
    } else if (action === "add-type") {
        state.addType = !state.addType;
        state.section = "inputs";
        renderAll();
    } else if (action === "add-input") {
        const input = blankInput(node.dataset.type);
        input._keyTouched = false;
        input.filter_rows = [];
        input.filter_mode = "visual";
        input.csv_import = true;
        payload.inputs.push(input);
        state.openInput = payload.inputs.length - 1;
        state.addType = false;
        renderAll();
        touch();
    } else if (action === "open-input") {
        const index = Number(node.dataset.index);
        state.openInput = state.openInput === index ? -1 : index;
        renderAll();
    } else if (action === "move-input" || action === "move-step") {
        const index = Number(node.dataset.index);
        const to = index + Number(node.dataset.dir);
        const list = action === "move-input" ? payload.inputs : payload.actions[node.dataset.phase];
        if (to < 0 || to >= list.length) return;
        const [row] = list.splice(index, 1);
        list.splice(to, 0, row);
        if (action === "move-input") state.openInput = state.openInput === index ? to : state.openInput === to ? index : state.openInput;
        else state.openStep = `${node.dataset.phase}:${to}`;
        renderAll();
        touch();
        // Keep the keyboard on the moved row: same button, or the other arrow at the end of the list.
        const scope = `[data-action="${action}"][data-index="${to}"]${node.dataset.phase ? `[data-phase="${node.dataset.phase}"]` : ""}`;
        const again = [...app.querySelectorAll(scope)].sort((a) => (a.dataset.dir === node.dataset.dir ? -1 : 1)).find((button) => !button.disabled);
        if (again) again.focus();
    } else if (action === "duplicate-input") {
        const index = Number(node.dataset.index);
        const copy = cloneRow(payload.inputs[index]);
        copy.label = `${copy.label || "Untitled"} (copy)`;
        copy.key = uniqueKey(keyFrom(copy.label), payload.inputs.map((item) => item.key));
        copy._keyTouched = false;
        payload.inputs.splice(index + 1, 0, copy);
        state.openInput = index + 1;
        renderAll();
        touch();
        toast(`Duplicated as “${copy.label}”.`);
    } else if (action === "duplicate-step") {
        const phase = node.dataset.phase;
        const index = Number(node.dataset.index);
        const copy = cloneRow(payload.actions[phase][index]);
        copy.label = `${copy.label || copy.name || "Untitled step"} (copy)`;
        copy.name = uniqueKey(keyFrom(copy.label), allSteps().map((item) => item.name));
        copy._nameTouched = false;
        delete copy._origName;
        payload.actions[phase].splice(index + 1, 0, copy);
        state.openStep = `${phase}:${index + 1}`;
        renderAll();
        touch();
        toast(`Duplicated as “${copy.label}”.`);
    } else if (action === "remove-input") {
        const index = Number(node.dataset.index);
        const input = payload.inputs[index];
        const hits = referencesTo(payload, input.key);
        if (hits.length && !window.confirm(`Delete “${input.label || input.key}”? It is used by ${hits.join(", ")}.`)) return;
        payload.inputs.splice(index, 1);
        state.openInput = -1;
        renderAll();
        touch();
        toast(`Deleted “${input.label || input.key || "input"}”.`, () => {
            payload.inputs.splice(index, 0, input);
            state.openInput = index;
            renderAll();
            touch();
        });
    } else if (action === "add-choice") {
        payload.inputs[Number(node.dataset.index)].choices.push({ value: "", label: "" });
        renderAll();
    } else if (action === "remove-choice") {
        payload.inputs[Number(node.dataset.index)].choices.splice(Number(node.dataset.choice), 1);
        renderAll();
        touch();
    } else if (action === "add-filter") {
        const input = payload.inputs[Number(node.dataset.index)];
        input.filter_rows.push({ field: "", op: "is", source: "value", input: "", value: "" });
        renderAll();
    } else if (action === "remove-filter") {
        payload.inputs[Number(node.dataset.index)].filter_rows.splice(Number(node.dataset.row), 1);
        renderAll();
        touch();
    } else if (action === "filter-mode") {
        const input = payload.inputs[Number(node.dataset.index)];
        input.filter_mode = input.filter_mode === "jinja" ? "visual" : "jinja";
        renderAll();
    } else if (action === "add-column") {
        payload.inputs[Number(node.dataset.index)].columns.push({ key: "", label: "", type: "string" });
        renderAll();
    } else if (action === "remove-column") {
        payload.inputs[Number(node.dataset.index)].columns.splice(Number(node.dataset.column), 1);
        renderAll();
        touch();
    } else if (action === "add-step") {
        const actionRow = blankAction("nautobot", node.dataset.phase);
        actionRow.script = "";
        actionRow.label = "";
        actionRow.template_name = "";
        actionRow.extra_vars = {};
        actionRow.with = {};
        actionRow._nameTouched = false;
        const list = payload.actions[node.dataset.phase];
        const at = node.dataset.at != null ? Number(node.dataset.at) : list.length;
        list.splice(at, 0, actionRow);
        state.openStep = `${node.dataset.phase}:${at}`;
        renderAll();
        touch();
    } else if (action === "model-open") {
        const index = Number(node.dataset.index);
        state.modelOpen = state.modelOpen === index ? -1 : index;
        state.modelQuery = "";
        renderEditor();
    } else if (action === "pick-model") {
        const index = Number(node.dataset.index);
        payload.inputs[index].model = node.dataset.model;
        state.modelOpen = -1;
        state.modelQuery = "";
        ensureFields(node.dataset.model);
        renderAll();
        touch();
    } else if (action === "add-show") {
        const index = Number(node.dataset.index);
        const other = payload.inputs.find((item, itemIndex) => itemIndex !== index && item.key);
        payload.inputs[index]._showMode = "visual";
        payload.inputs[index].show_if = other ? { [other.key]: other.type === "boolean" ? true : "" } : {};
        renderAll();
        touch();
    } else if (action === "clear-show") {
        payload.inputs[Number(node.dataset.index)].show_if = {};
        payload.inputs[Number(node.dataset.index)]._showMode = "visual";
        renderAll();
        touch();
    } else if (action === "show-jinja") {
        const input = payload.inputs[Number(node.dataset.index)];
        input._showMode = "jinja";
        if (typeof input.show_if !== "string") input.show_if = "";
        renderAll();
    } else if (action === "show-visual") {
        const input = payload.inputs[Number(node.dataset.index)];
        input._showMode = "visual";
        if (!parseShow(input.show_if)) input.show_if = {};
        renderAll();
    } else if (action === "add-when") {
        const actionRow = payload.actions[node.dataset.phase][Number(node.dataset.index)];
        const other = payload.inputs.find((item) => item.key);
        const before = earlierSteps(node.dataset.phase, Number(node.dataset.index))[0];
        actionRow._whenMode = "visual";
        actionRow.when = other
            ? writeWhen({ kind: "input", input: other.key, op: "is", value: other.type === "boolean" ? true : "" })
            : before
                ? writeWhen({ kind: "step", step: before.name, test: "succeeded" })
                : "";
        renderAll();
        touch();
    } else if (action === "clear-when") {
        payload.actions[node.dataset.phase][Number(node.dataset.index)].when = "";
        payload.actions[node.dataset.phase][Number(node.dataset.index)]._whenMode = "visual";
        renderAll();
        touch();
    } else if (action === "when-jinja") {
        const actionRow = payload.actions[node.dataset.phase][Number(node.dataset.index)];
        actionRow._whenMode = "jinja";
        renderAll();
    } else if (action === "when-visual") {
        const actionRow = payload.actions[node.dataset.phase][Number(node.dataset.index)];
        actionRow._whenMode = "visual";
        if (!parseWhen(actionRow.when)) actionRow.when = "";
        renderAll();
    } else if (action === "add-var") {
        const actionRow = payload.actions[node.dataset.phase][Number(node.dataset.index)];
        const map = varBucket(actionRow);
        const missing = ((scriptMeta(actionRow) || {}).params || []).find((param) => !(param.name in map));
        const key = missing ? missing.name : uniqueKey(actionRow.type === "awx" ? "variable" : "param", Object.keys(map));
        const input = missing ? inputByKey(missing.name) : null;
        map[key] = input ? refFor(input, missing) : missing && missing.default !== undefined ? missing.default : "";
        renderAll();
        touch();
        const added = app.querySelector(missing ? `#nwe-p-${node.dataset.phase}-${node.dataset.index}-${key} select, #nwe-p-${node.dataset.phase}-${node.dataset.index}-${key} input` : `[data-var-key="${node.dataset.phase}:${node.dataset.index}"][data-old="${key}"]`);
        if (added) {
            added.focus();
            if (added.select) added.select();
        }
    } else if (action === "add-param") {
        const actionRow = payload.actions[node.dataset.phase][Number(node.dataset.index)];
        const key = node.dataset.key;
        const decl = declFor(actionRow, key);
        const input = inputByKey(key);
        varBucket(actionRow)[key] = input ? refFor(input, decl) : decl && decl.default !== undefined ? decl.default : "";
        renderAll();
        touch();
        const added = app.querySelector(`#nwe-p-${node.dataset.phase}-${node.dataset.index}-${key} select, #nwe-p-${node.dataset.phase}-${node.dataset.index}-${key} input, #nwe-p-${node.dataset.phase}-${node.dataset.index}-${key} textarea`);
        if (added) added.focus();
    } else if (action === "param-mode") {
        setParamMode(node.dataset.phase, Number(node.dataset.index), node.dataset.key, node.dataset.mode);
    } else if (action === "step-type") {
        changeStepType(node.dataset.phase, Number(node.dataset.index), node.dataset.type);
    } else if (action === "key-from-label") {
        const row = payload.actions[node.dataset.phase][Number(node.dataset.index)];
        row._nameTouched = false;
        followLabel(node.dataset.phase, Number(node.dataset.index));
        renderEditor();
    } else if (action === "rm-var") {
        const actionRow = payload.actions[node.dataset.phase][Number(node.dataset.index)];
        delete varBucket(actionRow)[node.dataset.key];
        if (actionRow._modes) delete actionRow._modes[node.dataset.key];
        renderAll();
        touch();
    } else if (action === "open-step") {
        const key = `${node.dataset.phase}:${node.dataset.index}`;
        state.openStep = state.openStep === key ? "" : key;
        renderAll();
    } else if (action === "remove-step") {
        const phase = node.dataset.phase;
        const index = Number(node.dataset.index);
        const row = payload.actions[phase][index];
        const users = stepReferences(row.name, row);
        const label = row.label || row.name || "this step";
        if (users.length && !window.confirm(`Delete “${label}”? ${users.join(", ")} ${users.length === 1 ? "uses" : "use"} its result.`)) return;
        payload.actions[phase].splice(index, 1);
        state.openStep = "";
        renderAll();
        touch();
        toast(`Deleted “${label}”.`, () => {
            payload.actions[phase].splice(index, 0, row);
            renderAll();
            touch();
        });
    } else if (action === "preview-tab") {
        state.previewTab = node.dataset.tab;
        renderPreview();
    } else if (action === "publish") {
        doPublish();
    } else if (action === "versions") {
        state.menu = false;
        renderHeader();
        openVersions();
    } else if (action === "discard") {
        state.menu = false;
        confirmDiscard();
    } else if (action === "test") {
        runTest();
    } else if (action === "download") {
        downloadYaml();
    } else if (action === "duplicate") {
        duplicate();
    } else if (action === "ns-create") {
        createScript(node.dataset.phase, Number(node.dataset.index));
    } else if (action === "ns-cancel") {
        state.newScript = null;
        renderEditor();
    } else if (action === "ns-copy") {
        const text = (state.newScript && state.newScript.code) || "";
        if (navigator.clipboard && text) navigator.clipboard.writeText(text).then(() => toast("Code copied"), () => toast("Couldn’t copy. Select the code and copy it."));
    } else if (action === "retry") {
        retryGit();
    } else if (action === "compare") {
        state.compare = !state.compare;
        if (!state.yamlText) refreshYaml();
        renderBanner();
    } else if (action === "load-git") {
        loadGit();
    } else if (action === "yaml-diff") {
        state.yamlDiff = !state.yamlDiff;
        if (!state.yamlText) refreshYaml().then(renderEditor);
        else renderEditor();
    } else if (action === "copy-link") {
        navigator.clipboard.writeText(window.location.href).then(() => toast("Link copied."));
    }
});

let drag = null;
app.addEventListener("toggle", (event) => {
    const node = event.target;
    if (!node.dataset || !node.dataset.adv) return;
    if (node.open) state.openAdvanced = node.dataset.adv;
    else if (state.openAdvanced === node.dataset.adv) state.openAdvanced = "";
}, true);

app.addEventListener("dragstart", (event) => {
    const node = event.target.closest("[data-drag]");
    if (!node) return;
    drag = { kind: node.dataset.drag, index: Number(node.dataset.index), phase: node.dataset.phase };
});
app.addEventListener("dragover", (event) => {
    if (drag) event.preventDefault();
});
app.addEventListener("drop", (event) => {
    const node = event.target.closest("[data-drag]");
    if (!drag || !node || node.dataset.drag !== drag.kind) return;
    event.preventDefault();
    const target = Number(node.dataset.index);
    if (drag.kind === "input" && target !== drag.index) {
        const [row] = payload.inputs.splice(drag.index, 1);
        payload.inputs.splice(target, 0, row);
        state.openInput = target;
    }
    if (drag.kind === "step" && node.dataset.phase === drag.phase && target !== drag.index) {
        const list = payload.actions[drag.phase];
        const [row] = list.splice(drag.index, 1);
        list.splice(target, 0, row);
    }
    drag = null;
    renderAll();
    touch();
});

async function doPublish() {
    const block = publishBlock();
    if (!config.can_publish) {
        state.section = "review";
        renderAll();
        return;
    }
    if (block) {
        state.section = block.section;
        renderAll();
        return;
    }
    if (!config.publish_url) {
        await saveDraft();
        return;
    }
    const body = {
        payload,
        base_revision: config.base_revision,
        commit_summary: state.note,
        repository: payload.source_repo || null,
    };
    if (!config.published_number) body.enable = state.enableOnPublish;
    const result = await api(config.publish_url, body);
    if (!result.ok) {
        const errors = (result.data && result.data.errors) || [];
        if (errors.length) {
            state.serverErrors = errors;
            renderAll();
        }
        toast(publishFailure(result));
        return;
    }
    toast(config.published_number ? "Changes published" : "Published");
    window.location.reload();
}

function publishFailure(result) {
    const data = result.data || {};
    const first = (data.errors || [])[0];
    if (data.detail) return data.detail;
    if (first && first.message) return data.errors.length > 1 ? `${first.message} (and ${data.errors.length - 1} more to fix)` : first.message;
    if (result.status === 403) return "You don’t have permission to publish this workflow.";
    if (!result.status) return "Couldn’t reach Nautobot. Check your connection and try again.";
    return "Publish didn’t complete. Nothing changed. Try again, or ask an admin to check the server log.";
}

async function setEnabled(on) {
    payload.enabled = on;
    if (!config.published_number) return;
    const result = await api(on ? config.enable_url : config.disable_url, {});
    if (!result.ok) {
        payload.enabled = !on;
        toast(result.data.detail || "Publish it first.");
        renderHeader();
    }
}

async function applyYaml() {
    if (!config.can_author_code) return;
    const result = await api(config.yaml_apply_url, { yaml: state.yamlText });
    if (!result.ok || !result.data.payload) {
        const error = (result.data.errors || [])[0];
        state.yamlLine = error && error.line ? Number(error.line) : 0;
        state.yamlError = error ? `Line ${error.line || "?"}: ${error.message}` : result.data.detail || "The YAML could not be read.";
        renderEditor();
        return;
    }
    state.yamlError = "";
    state.yamlLine = 0;
    Object.assign(payload, result.data.payload);
    payload.inputs = result.data.payload.inputs || [];
    payload.actions = result.data.payload.actions || { pre: [], main: [], post: [] };
    payload.schedule = result.data.payload.schedule || payload.schedule;
    touch();
}

function confirmDiscard() {
    const body = document.getElementById("builder-confirm-body");
    body.textContent = "Discard the shared draft? The published version stays as it is. The draft is kept in Versions.";
    const modal = window.bootstrap.Modal.getOrCreateInstance(document.getElementById("builder-confirm-dialog"));
    const ok = document.getElementById("builder-confirm-ok");
    ok.onclick = async () => {
        if (config.draft_url) await api(config.draft_url, {}, "DELETE");
        modal.hide();
        window.location.reload();
    };
    modal.show();
}

const REVISION_STATE = { published: "Live now", superseded: "Earlier version", draft: "Not published", discarded: "Discarded, never published" };

function revisionTitle(revision) {
    const published = revision.state === "published" || revision.state === "superseded";
    const when = published ? revision.published_at || revision.created : revision.created;
    const stamp = when ? new Date(when) : null;
    const date = stamp && !Number.isNaN(stamp.getTime()) ? stamp.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
    if (revision.state === "draft") return date ? `Current draft · started ${date}` : "Current draft";
    if (!published) return date ? `Draft from ${date}` : "Earlier draft";
    return date ? `Published ${date}` : "Published version";
}

async function openVersions() {
    const body = document.getElementById("builder-versions-body");
    body.replaceChildren(h("p", { text: "Loading versions…" }));
    const modal = window.bootstrap.Modal.getOrCreateInstance(document.getElementById("builder-versions-dialog"));
    modal.show();
    if (!config.revisions_url) {
        body.textContent = "Nothing published yet. Every publish is kept here.";
        return;
    }
    const result = await api(config.revisions_url, {}, "GET");
    const revisions = Array.isArray(result.data) ? result.data.slice().reverse() : [];
    body.replaceChildren(
        h("p", { text: "Every publish is kept here. Restore copies a version into the draft. Nothing goes live until someone publishes." }),
        ...revisions.map((revision) => h("div", { class: "d-flex gap-2 align-items-start border-bottom py-2" }, [
            h("div", { class: "flex-grow-1" }, [
                h("div", { class: "fw-semibold", text: revisionTitle(revision) }),
                h("div", { class: "small text-secondary", text: [revision.published_by || revision.created_by || "", revision.comment || "No note", REVISION_STATE[revision.state] || ""].filter(Boolean).join(" · ") }),
            ]),
            revision.state === "published" && revision.number === config.published_number
                ? h("span", { class: "nwe-pill nwe-pill-ok", text: "Live" })
                : h("button", { type: "button", class: "btn btn-sm nwe-btn", "data-restore": String(revision.number), text: "Restore into draft" }),
        ])),
    );
    body.querySelectorAll("[data-restore]").forEach((button) => {
        button.addEventListener("click", async () => {
            const url = config.restore_url_template.replace("__N__", button.dataset.restore);
            const restored = await api(url, {});
            if (!restored.ok) {
                toast(restored.data.detail || "Could not restore.");
                return;
            }
            toast("Restored into the draft. It is not live.");
            window.location.reload();
        });
    });
}

/** The Launch form preview's values, in the shape the launch page posts. */
function previewValues() {
    const values = {};
    payload.inputs.forEach((input) => {
        if (!input.key) return;
        const has = Object.prototype.hasOwnProperty.call(state.preview, input.key);
        const value = has ? state.preview[input.key] : previewDefault(input);
        if (value === undefined) return;
        values[input.key] = value;
    });
    return values;
}

function skipText(step, action) {
    if (action && parseWhen(action.when)) return `Skipped: it runs only if ${whenPlain(action)}, and that isn’t the case here.`;
    const shown = step.when_rendered === "" ? "empty" : `“${step.when_rendered}”`;
    return `Skipped: the condition came out ${shown}. Only true, yes, on, y or 1 run it.`;
}

function paramLine(key, value) {
    const text = typeof value === "string" ? value : JSON.stringify(value);
    return h("div", { class: "nwe-run-param" }, [h("code", { class: "k", text: key }), h("span", { class: "eq", text: "=" }), h("code", { class: "v", text: text === "" ? "(empty)" : text })]);
}

/**
 * Preview run: resolve the Launch form preview's values, evaluate each condition
 * and render each step's parameters on the server, in the sandbox. It never runs a
 * script, calls AWX, records a run or schedules a job.
 */
async function runTest() {
    const panel = window.bootstrap.Offcanvas.getOrCreateInstance(document.getElementById("builder-test-run-panel"));
    const host = document.getElementById("test-run-results");
    host.replaceChildren(h("div", { class: "nwe-muted" }, [icon("loading", "nwe-spin"), " Rendering the steps…"]));
    panel.show();
    const result = await api(config.dry_run_url, { payload, inputs: previewValues() });
    if (!result.ok) {
        host.replaceChildren(h("div", { class: "nwe-run-banner bad" }, [icon("alert-circle-outline"), h("span", { text: result.data.detail || "The preview run couldn’t start. Fix the issues in the draft and try again." })]));
        return;
    }
    const data = result.data || {};
    if (data.error) {
        host.replaceChildren(h("div", { class: "nwe-run-banner bad" }, [icon("timer-alert-outline"), h("span", { text: data.error.replace("Test run", "Preview run") })]));
        return;
    }
    const errors = data.form_errors || [];
    if (errors.length) {
        const label = (key) => (inputByKey(key) || {}).label || key;
        host.replaceChildren(
            h("div", { class: "nwe-run-banner warn" }, [icon("form-textbox"), h("span", { text: "Fill in the Launch form preview first. These fields stop the run before any step:" })]),
            h("ul", { class: "nwe-run-errors" }, errors.map((error) => h("li", {}, [h("b", { text: label(error.key) }), ` — ${error.message}`]))),
            h("button", { type: "button", class: "btn nwe-btn", "data-bs-dismiss": "offcanvas", onclick: () => { state.previewTab = "form"; renderPreview(); } }, [icon("arrow-left"), "Back to the form"]),
        );
        return;
    }
    const phases = Array.isArray(data.phases) ? data.phases : [];
    let ran = 0;
    let skipped = 0;
    let failed = 0;
    const rows = [];
    phases.forEach((block) => {
        const actions = payload.actions[block.phase] || [];
        if (!(block.steps || []).length) return;
        rows.push(h("li", { class: "phase", text: (PHASE_META[block.phase] || {}).label || block.phase }));
        block.steps.forEach((step, index) => {
            const action = actions[index] || {};
            const title = action.label || step.name || "Step";
            let cls = "step";
            let glyph = action.type === "awx" ? "ansible" : "script-text-outline";
            let sub;
            if (step.error) {
                failed += 1;
                cls += " bad";
                glyph = "alert-circle-outline";
                sub = h("small", { class: "text-danger", text: step.error });
            } else if (!step.will_run) {
                skipped += 1;
                cls += " skip";
                glyph = "debug-step-over";
                sub = h("div", {}, [
                    h("small", { text: skipText(step, action) }),
                    step.assumption ? h("small", { class: "nwe-run-assume" }, [icon("help-circle-outline"), step.assumption]) : null,
                ]);
            } else {
                ran += 1;
                const params = step.params && typeof step.params === "object" && !Array.isArray(step.params) ? Object.entries(step.params) : [];
                sub = h("div", {}, [
                    step.assumption ? h("small", { class: "nwe-run-assume" }, [icon("help-circle-outline"), step.assumption]) : null,
                    h("small", { text: `Would run ${action.type === "awx" ? `AWX template ${action.template_name || `#${action.template_id || "?"}`}` : scriptName(action.script) || "the script"}${step.when_rendered ? " · condition is true" : ""}` }),
                    scriptMeta(action)?.enabled === false ? h("small", { class: "nwe-run-assume" }, [icon("power-plug-off-outline"), "This script is switched off until an admin enables it, so a real run would stop here."]) : null,
                    params.length ? h("div", { class: "nwe-run-params" }, params.map(([key, value]) => paramLine(key, value))) : null,
                ]);
            }
            rows.push(h("li", { class: cls }, [h("span", { class: "ic", "aria-hidden": "true" }, [icon(glyph)]), h("span", { class: "tx" }, [h("b", { text: title }), sub])]));
        });
    });
    const parts = [`${ran} would run`, skipped ? `${skipped} skipped` : "", failed ? `${failed} with errors` : ""].filter(Boolean).join(" · ");
    host.replaceChildren(
        h("div", { class: `nwe-run-banner ${failed ? "bad" : "ok"}` }, [icon(failed ? "alert-circle-outline" : "check-circle-outline"), h("span", { text: rows.length ? parts : "There are no steps yet." })]),
        h("div", { class: "nwe-muted small mb-3", text: previewFilled() ? "Using the values in the Launch form preview." : "Using the Launch form’s defaults. Fill in the preview to try other values." }),
        rows.length ? h("ol", { class: "nwe-wh nwe-run-steps" }, rows) : null,
    );
}

function downloadYaml() {
    const blob = new Blob([state.yamlText || ""], { type: "text/yaml" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${payload.key || "workflow"}.yaml`;
    link.click();
    URL.revokeObjectURL(link.href);
}

async function duplicate() {
    const key = window.prompt("Key for the copy");
    const name = window.prompt("Name for the copy");
    if (!key || !name || !config.key) return;
    const url = config.duplicate_url || "";
    const result = await api(url, { key, name });
    if (!result.ok) toast(result.data.detail || "Could not duplicate.");
    else window.location.assign(config.edit_url_template.replace("__KEY__", key));
}

async function retryGit() {
    const result = await api(config.push_url, {});
    state.gitStatus = (result.data && result.data.git_status) || state.gitStatus;
    state.gitError = (result.data && result.data.git_error) || "";
    toast(state.gitStatus === "in_sync" ? "Synced" : state.gitError || "Trying again…");
    renderHeader();
    watchGit();
}

let gitPolls = 0;
function watchGit() {
    if (state.gitStatus !== "pending" || !config.git_status_url || gitPolls > 40) return;
    gitPolls += 1;
    window.setTimeout(async () => {
        const result = await api(config.git_status_url, {}, "GET");
        if (result.ok && result.data) {
            state.gitStatus = result.data.git_status || state.gitStatus;
            state.gitError = result.data.git_error || "";
            renderHeader();
            if (state.gitStatus !== "pending" && state.gitError) toast(state.gitError);
        }
        watchGit();
    }, 3000);
}

async function loadGit() {
    const result = await api(config.load_git_url, {});
    if (!result.ok) {
        toast(result.data.detail || "Could not load that version.");
        return;
    }
    toast("Loaded their version into the draft. It is not published.");
    window.location.reload();
}

if (config.refresh_url && config.key) {
    api(config.refresh_url, {}).then((result) => {
        if (result.ok && result.data && result.data.updated) window.location.reload();
    });
}
const requestedSection = new URLSearchParams(window.location.search).get("section");
if (SECTIONS.some((item) => item[0] === requestedSection)) state.section = requestedSection;
refreshYaml();
renderAll();
watchGit();
// Leaving the page just works: drafts autosave. Send a pending save on the way out.
function flushOnLeave() {
    if (!saveTimer || config.read_only || !config.key || !payload.key || !payload.name) return;
    clearTimeout(saveTimer);
    saveTimer = 0;
    const body = JSON.stringify({
        payload,
        strict: false,
        base_revision: config.base_revision,
        base_git_commit: config.base_git_commit || "",
        base_content_hash: config.base_content_hash || "",
    });
    fetch(config.draft_url, {
        method: "PUT",
        credentials: "same-origin",
        keepalive: true,
        headers: { "Content-Type": "application/json", "X-CSRFToken": config.csrf_token || "" },
        body,
    }).catch(() => {});
}
window.addEventListener("pagehide", flushOnLeave);
document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushOnLeave();
});

["click", "change", "input", "drop", "keyup"].forEach((type) => {
    document.addEventListener(type, () => queueMicrotask(noticeChanges));
});
if (config.key && new URLSearchParams(window.location.search).get("versions") === "1") {
    openVersions();
}
