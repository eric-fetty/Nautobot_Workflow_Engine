/**Workflow builder: one shared draft, five steps, live preview. */

import { blankAction, blankInput, referencesTo, setPath, slugify } from "./store.js";
import { createField } from "../shared/fields.js";

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
        action._nameTouched = Boolean(action.name);
        if (!action.label) action.label = action.name || "";
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
    how: false,
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
const sidenav = document.getElementById("sidenav");
if (sidenav) sidenav.classList.add("nb-sidenav-collapsed");

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

function stepLocked(action) {
    return (config.published_step_names || []).includes(action.name) && action._nameTouched;
}

function allSteps() {
    return PHASES.flatMap(([phase]) => payload.actions[phase] || []);
}

function localIssues() {
    const buckets = { details: [], inputs: [], steps: [], schedule: [], review: [] };
    if (!payload.name) buckets.details.push("Name is required.");
    if (!payload.key) buckets.details.push("Workflow ID is required.");
    payload.inputs.forEach((input, index) => {
        const who = input.label || `Input ${index + 1}`;
        if (!input.label) buckets.inputs.push(`${who}: add a label.`);
        if (!input.key) buckets.inputs.push(`${who}: add a variable name.`);
        if ((input.type === "object" || input.type === "multiobject") && !input.model) {
            buckets.inputs.push(`${who}: choose an object type.`);
        }
        if (input.type === "listdict" && !(input.columns || []).length) {
            buckets.inputs.push(`${who}: add a column.`);
        }
    });
    PHASES.forEach(([phase]) => {
        (payload.actions[phase] || []).forEach((action) => {
            const who = action.label || action.name || "Step";
            if (!action.name) buckets.steps.push(`${who}: add a step key.`);
            if (action.type === "awx" && !action.template_name && !action.template_id) buckets.steps.push(`${who}: Choose an AWX job template`);
        });
    });
    if (payload.schedule.enabled && !payload.schedule.integration_name) {
        buckets.schedule.push("Choose a ServiceNow connection.");
    }
    state.serverErrors.forEach((item) => {
        const section = item.section || "review";
        const bucket = buckets[section] ? section : "review";
        buckets[bucket].push(item.message || "Check this value.");
    });
    return buckets;
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
    const order = ["steps", "inputs", "details", "schedule", "review"];
    const hit = order.find((section) => buckets[section].length);
    if (hit) {
        const count = buckets[hit].length;
        const label = SECTIONS.find((item) => item[0] === hit)[1];
        return { message: `Fix ${count} issue${count === 1 ? "" : "s"} in ${label}`, section: hit };
    }
    if (!differs()) return { message: "No changes to publish", section: "review" };
    return null;
}

function statusText() {
    if (!config.published_number) return ["Draft (not published yet)", "new"];
    if (differs() || state.dirty) return [`Published v${config.published_number} · draft changes`, "draft"];
    return [`Published v${config.published_number} · up to date`, "ok"];
}

async function api(url, body, method) {
    const response = await fetch(url, {
        method: method || "POST",
        credentials: "same-origin",
        headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": config.csrf_token || "",
        },
        body: method === "GET" || method === "DELETE" ? undefined : JSON.stringify(body || {}),
    });
    const text = await response.text();
    let data = {};
    if (text) {
        try {
            data = JSON.parse(text);
        } catch (error) {
            data = { detail: text };
        }
    }
    return { ok: response.ok, status: response.status, data };
}

function toast(message) {
    const node = document.getElementById("builder-toast-text");
    const toastNode = document.getElementById("builder-toast");
    if (!node || !toastNode || !window.bootstrap) return;
    node.textContent = message;
    window.bootstrap.Toast.getOrCreateInstance(toastNode, { delay: 4000 }).show();
}

let saveTimer = 0;
function touch() {
    if (config.read_only) return;
    state.dirty = true;
    state.save = "dirty";
    paintChrome();
    clearTimeout(saveTimer);
    saveTimer = window.setTimeout(saveDraft, 800);
}

async function saveDraft() {
    if (config.read_only) return;
    if (!payload.key && payload.name) payload.key = slugify(payload.name);
    if (!payload.key || !payload.name) return;
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
        paintSave();
        if (result && result.status === 409) toast(result.data.detail || "Reload to continue.");
        return;
    }
    if (result.data.revision && result.data.revision.number != null) {
        config.base_revision = result.data.revision.number;
        config.draft_by = config.username;
    }
    state.serverErrors = result.data.errors || [];
    state.save = "saved";
    state.savedAt = Date.now();
    config.has_draft_changes = true;
    paintChrome();
    refreshYaml();
}

async function refreshYaml() {
    const result = await api(config.validate_url, { payload, strict: false, repository: payload.source_repo || null });
    if (!result.ok) return;
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
    renderHint();
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
                    h("span", { class: `nwe-pill ${kind === "ok" ? "nwe-pill-ok" : "nwe-pill-draft"}` }, [
                        h("span", { class: "dot" }),
                        text,
                    ]),
                    h("button", { type: "button", class: "nwe-info", "data-action": "how", title: "How editing works", text: "ⓘ" }),
                    h("span", { class: "nwe-saved", id: "nwe-save" }),
                ]),
            ]),
        ]),
        h("div", { class: "nwe-tools" }, [
            git.bad
                ? h("button", { type: "button", class: "btn btn-sm nwe-btn nwe-git-bad", "data-action": "retry" }, [icon("alert-circle-outline"), git.text])
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
            block ? h("a", { href: "#", class: "nwe-why", "data-action": "goto", "data-section": block.section }, [icon("alert-circle-outline"), block.message]) : null,
            h("button", {
                type: "button",
                class: "btn nwe-btn-accent nwe-publish",
                "data-action": "publish",
                disabled: Boolean(block) || config.read_only,
            }, [icon("rocket-launch-outline"), config.can_publish ? "Publish changes" : "Request publish"]),
        ]),
    );
    if (state.how) header.querySelector(".nwe-state")?.append(howPop());
    paintSave();
}

function gitIndicator() {
    if (state.gitStatus === "not_synced" || state.gitStatus === "git_invalid") {
        return { text: "Not synced · Retry", bad: true };
    }
    if (!config.published_number) return { text: "Not in Git yet", bad: false, icon: "cloud-outline" };
    if (state.gitStatus === "pending") return { text: "Syncing…", bad: false, icon: "cloud-sync-outline" };
    return { text: "Synced", bad: false };
}

function howPop() {
    const version = config.published_number ? `v${config.published_number}` : "nothing (it isn’t published yet)";
    const next = `v${(config.published_number || 0) + 1}`;
    return h("div", { class: "nwe-pop nwe-how", id: "nwe-how" }, [
        h("div", { class: "fw-semibold mb-2", text: "How editing works" }),
        h("ol", {}, [
            h("li", {}, [h("b", { text: "Edit. " }), "Every change saves automatically within a second into one shared draft in Nautobot. There's no Save button."]),
            h("li", {}, [h("b", { text: "Only editors see the draft" }), `, and only here in the builder. People running it keep getting ${version}, unchanged. Other editors see the same draft.`]),
            h("li", {}, [h("b", { text: "Try it" }), " with Test run in the preview. It's a dry run only you can see; nothing real happens."]),
            h("li", {}, [h("b", { text: "Publish changes" }), ` saves ${next}, commits it to Git, and makes it the version everyone runs. Runs in progress finish on the version they started with.`]),
        ]),
        h("table", { class: "table table-sm mt-2 mb-2" }, [
            h("thead", {}, [h("tr", {}, [h("th", { text: "" }), h("th", { text: "Sees the draft" }), h("th", { text: "Runs" })])]),
            h("tbody", {}, [
                h("tr", {}, [h("td", { text: "Editors" }), h("td", { text: "✓ in the builder" }), h("td", { text: config.published_number ? `v${config.published_number}` : "—" })]),
                h("tr", {}, [h("td", { text: "People running it" }), h("td", { text: "✗" }), h("td", { text: config.published_number ? `v${config.published_number}` : "can’t yet" })]),
                h("tr", {}, [h("td", { text: "Git" }), h("td", { text: "✗ never" }), h("td", { text: config.published_number ? `has v${config.published_number}` : "nothing yet" })]),
            ]),
        ]),
        h("div", { class: "small text-secondary", text: "Versions keeps every published version. Restoring one copies it into the draft; it never goes live by itself." }),
        h("button", { type: "button", class: "btn btn-link btn-sm px-0", "data-action": "versions", text: "Versions" }),
    ]);
}

function menu() {
    return h("div", { class: "nwe-pop nwe-menu", role: "menu" }, [
        item("versions", "Versions", "history"),
        item("test", "Test run the draft", "play-circle-outline"),
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

function renderHint() {
    const host = document.getElementById("studio-hint");
    const seen = window.localStorage.getItem("nwe-how-hint") === "1";
    if (seen) {
        host.replaceChildren();
        return;
    }
    host.replaceChildren(
        h("div", { class: "nwe-hint" }, [
            h("span", {}, [h("b", { text: "Changes save automatically as a draft that only editors see. " }), "Publish makes them live for everyone and syncs them to Git."]),
            h("button", { type: "button", class: "btn btn-link btn-sm", "data-action": "how", text: "How editing works" }),
            h("button", { type: "button", class: "btn btn-sm btn-light ms-auto", "data-action": "hint", text: "Got it" }),
        ]),
    );
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
    return block ? block.message : "Ready to publish";
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
        h("div", { class: "nwe-in-head", "data-action": "open-input", "data-index": String(index) }, [
            h("span", { class: "nwe-grip", title: "Drag to reorder", "aria-hidden": "true" }, [icon("drag-vertical")]),
            tile(input.type),
            h("div", { class: "nwe-in-copy" }, [
                h("div", { class: "nwe-in-name", "data-row-label": String(index), text: input.label || "Untitled" }),
                h("div", { class: "nwe-in-meta", text: inputSubtitle(input) }),
            ]),
            h("div", { class: "nwe-pills" }, inputPills(input).map(([text, tone]) => h("span", { class: `nwe-tag ${tone}`, text }))),
            h("button", { type: "button", class: "nwe-iconbtn danger", "data-action": "remove-input", "data-index": String(index), title: "Remove input", "aria-label": `Remove ${input.label || "input"}` }, [icon("trash-can-outline")]),
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
            ? h("textarea", { class: "form-control font-monospace", rows: "4", "data-bind": `inputs.${index}.filter_query`, value: input.filter_query || "" })
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
    if (!row || !row.input) return "";
    const literal = row.value === true || row.value === "true" ? "true" : row.value === false || row.value === "false" ? "false" : JSON.stringify(String(row.value ?? ""));
    if (row.op === "is not") return `{{ inputs.${row.input} != ${literal} }}`;
    if (literal === "true") return `{{ inputs.${row.input} }}`;
    return `{{ inputs.${row.input} == ${literal} }}`;
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
    const row = parseShow(actionRow.when) || { input: "", op: "is", value: "" };
    row[part] = value;
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
            h("textarea", { class: "form-control font-monospace", rows: "2", "data-bind": `inputs.${index}.show_if`, value: typeof input.show_if === "string" ? input.show_if : "" }),
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

function stepsView() {
    const phases = [
        ["pre", "Before", "bi", "Runs first, and again when a scheduled run is booked. If a step fails, the run stops here."],
        ["main", "Main", "play", "Runs in order. If a step fails, it’s recorded and the remaining main steps still run."],
        ["post", "Always run at the end", "flag", "Runs last, even after failures. Use it for cleanup and notes. A failure here doesn’t fail the run."],
    ];
    return h("div", { class: "nwe-section" }, [
        h("div", { class: "nwe-kicker", text: "Step 3 of 5" }),
        h("h2", { text: "Steps" }),
        h("p", { class: "nwe-sub", text: "What happens when someone runs this workflow, from top to bottom." }),
        ...phases.map(([phase, label, icon, help]) => phaseBlock(phase, label, icon, help)),
    ]);
}

function phaseBlock(phase, label, icon, help) {
    const list = payload.actions[phase] || [];
    return h("div", { class: `nwe-phase ph-${phase}` }, [
        h("div", { class: "nwe-phase-head" }, [
            h("span", { class: `nwe-phase-ico ${icon}`, text: icon === "play" ? "▶" : icon === "flag" ? "⚑" : "🛡" }),
            h("div", {}, [
                h("div", { class: "fw-semibold", text: `${label}  ${list.length} step${list.length === 1 ? "" : "s"}` }),
                h("div", { class: "small text-secondary", text: help }),
            ]),
        ]),
        h("div", { class: "nwe-phase-body" }, [
            insertPoint(phase, 0),
            ...list.flatMap((action, index) => [stepCard(phase, action, index), insertPoint(phase, index + 1)]),
        ]),
    ]);
}

function insertPoint(phase, index) {
    return h("div", { class: "nwe-insert" }, [
        h("button", { type: "button", "data-action": "add-step", "data-phase": phase, "data-at": String(index), title: "Add a step here", text: "+" }),
    ]);
}

function paramEntries(action) {
    const raw = action.type === "awx" ? action.extra_vars : action.with;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
    return Object.entries(raw);
}

function valueChip(value) {
    const text = String(value ?? "");
    const match = text.match(/^\{\{\s*inputs\.([A-Za-z0-9_]+)(?:\.([A-Za-z0-9_.]+))?\s*\}\}$/);
    if (!match) return h("span", { text });
    const input = inputByKey(match[1]);
    const label = input ? input.label || input.key : match[1];
    return h("span", { class: "nwe-var", text: match[2] ? `${label} › ${match[2]}` : label });
}

function stepSummary(action) {
    const bits = [];
    paramEntries(action).forEach(([key, value], index) => {
        if (index) bits.push(", ");
        bits.push(h("span", { class: "nwe-param", text: key }));
        bits.push(" = ");
        bits.push(valueChip(value));
    });
    if (action.type === "awx") {
        if (!action.template_name) return h("span", { class: "nwe-missing", text: "Choose an AWX job template" });
        return h("span", {}, ["Launch AWX template ", h("b", { text: action.template_name }), bits.length ? " with " : "", ...bits]);
    }
    const name = scriptName(action.script);
    if (!name) return h("span", { class: "nwe-missing", text: "Choose which script to run" });
    return h("span", {}, ["Run script ", h("b", { text: name }), bits.length ? " with " : "", ...bits]);
}

function whenPlain(action) {
    const row = parseShow(action.when);
    if (!row) return action.when ? "a custom condition is true" : "";
    const input = inputByKey(row.input);
    const label = input ? input.label || input.key : row.input;
    const shown = row.value === true || row.value === "true" ? "Yes" : row.value === false || row.value === "false" ? "No" : row.value;
    return `“${label}” ${row.op} ${shown}`;
}

function stepCard(phase, action, index) {
    const open = state.openStep === `${phase}:${index}`;
    const missing = action.type === "awx" ? !action.template_name && !action.template_id : !scriptName(action.script);
    const flags = [];
    if (action.when) flags.push(`Only if ${whenPlain(action)}`);
    if (action.monitor) flags.push("Waits for the job to finish");
    return h("div", { class: `nwe-stepcard ${open ? "open" : ""} ${missing ? "bad" : ""}`, draggable: "true", "data-drag": "step", "data-phase": phase, "data-index": String(index) }, [
        h("div", { class: "nwe-step-head", "data-action": "open-step", "data-phase": phase, "data-index": String(index) }, [
            h("span", { class: "nwe-grip", text: "⋮⋮" }),
            tile(action.type === "awx" ? "awx" : "nautobot"),
            h("div", { class: "flex-grow-1" }, [
                h("div", { class: "nwe-step-title" }, [
                    action.label || "Untitled step",
                    h("span", { class: "nwe-soft", text: action.type === "awx" ? "AWX job" : "Nautobot script" }),
                ]),
                h("div", { class: "nwe-step-sum" }, [stepSummary(action)]),
                flags.length ? h("div", { class: "nwe-pills" }, flags.map((text) => h("span", { class: "nwe-soft blue", text }))) : null,
            ]),
            h("button", { type: "button", class: "nwe-icon", "data-action": "remove-step", "data-phase": phase, "data-index": String(index), text: "🗑" }),
            h("span", { class: "nwe-chev", text: open ? "▴" : "▾" }),
        ]),
        open ? stepEditor(phase, action, index) : null,
    ]);
}

function stepEditor(phase, action, index) {
    const base = `actions.${phase}.${index}`;
    const scripts = (config.scripts || []).filter((item) => !payload.source_repo || item.repo === String(payload.source_repo));
    const choices = scripts.length ? scripts : (config.scripts || []);
    return h("div", { class: "nwe-step-body" }, [
        h("div", { class: "nwe-grid" }, [
            gridField("Step name", h("input", { class: "form-control", "data-bind": `${base}.label`, value: action.label || "" }), "A friendly label, shown on this card and in run history."),
            action.type === "awx"
                ? gridField("Job template", h("input", { class: "form-control", "data-bind": `${base}.template_name`, value: action.template_name || "", placeholder: "Network · Reboot device" }), "The template’s name. The numeric id stays under Advanced.")
                : gridField("Script", h("select", { class: `form-select ${scriptName(action.script) ? "" : "is-invalid"}`, "data-bind": `${base}.script` }, [
                    h("option", { value: "", text: "Choose a script…" }),
                    ...choices.map((item) => h("option", { value: item.path, text: item.name, selected: action.script === item.path })),
                ]), scriptName(action.script) ? action.script : "Choose which script to run."),
            h("div", { class: "nwe-span" }, [varsEditor(phase, action, index)]),
            action.type === "awx" ? h("div", { class: "nwe-span" }, [
                h("label", { class: "nwe-switch" }, [
                    h("input", { type: "checkbox", "data-bind": `${base}.monitor`, checked: Boolean(action.monitor) }),
                    h("span", { class: "nwe-switch-ui" }),
                    h("span", {}, [h("b", { text: "Wait for the AWX job to finish" }), h("small", { text: "The run waits until AWX reports success or failure." })]),
                ]),
            ]) : null,
            h("div", { class: "nwe-span" }, [whenEditor(phase, action, index)]),
        ]),
        h("details", { class: "nwe-advanced" }, [
            h("summary", { text: "Advanced · step key" }),
            gridField(
                "Step key",
                h("input", { class: "form-control", "data-bind": `${base}.name`, value: action.name || "", disabled: stepLocked(action) }),
                stepLocked(action) ? `Locked because v${config.published_number} uses it.` : `Made from the step name. Later steps use {{ results.${action.name || "name"} }}.`,
            ),
            action.type === "awx" ? gridField("Template id", h("input", { class: "form-control", type: "number", min: "1", "data-bind": `${base}.template_id`, value: action.template_id ?? "" }), "The numeric job template id AWX expects.") : null,
        ]),
    ]);
}

function varsEditor(phase, action, index) {
    const entries = paramEntries(action);
    const raw = action.type === "awx" ? action.extra_vars : action.with;
    if (typeof raw === "string") {
        return gridField(action.type === "awx" ? "Extra variables" : "Script parameters", h("textarea", { class: "form-control font-monospace", rows: "3", "data-bind": `actions.${phase}.${index}.${action.type === "awx" ? "extra_vars" : "with"}`, value: raw }));
    }
    return h("div", {}, [
        h("label", { class: "form-label", text: action.type === "awx" ? "Extra variables" : "Script parameters" }),
        ...entries.map(([key, value]) => h("div", { class: "nwe-var-row" }, [
            h("input", { class: "form-control form-control-sm", "data-var-key": `${phase}:${index}`, "data-old": key, value: key }),
            h("input", { class: "form-control form-control-sm", "data-var-val": `${phase}:${index}`, "data-old": key, value: value }),
            h("button", { type: "button", class: "nwe-icon", "data-action": "rm-var", "data-phase": phase, "data-index": String(index), "data-key": key, text: "×" }),
        ])),
        h("button", { type: "button", class: "btn btn-link btn-sm px-0", "data-action": "add-var", "data-phase": phase, "data-index": String(index), text: action.type === "awx" ? "+ Add variable" : "+ Add parameter" }),
    ]);
}

function whenEditor(phase, action, index) {
    const advanced = action._whenMode === "jinja" || (action.when && !parseShow(action.when));
    if (advanced) {
        return h("div", {}, [
            h("label", { class: "form-label", text: "Condition" }),
            h("textarea", { class: "form-control font-monospace", rows: "2", "data-bind": `actions.${phase}.${index}.when`, value: action.when || "" }),
            h("button", { type: "button", class: "btn btn-link btn-sm px-0", "data-action": "when-visual", "data-phase": phase, "data-index": String(index), text: "Back to the condition builder" }),
        ]);
    }
    const row = parseShow(action.when);
    if (!row) {
        return h("div", {}, [
            h("label", { class: "form-label", text: "Condition" }),
            h("button", { type: "button", class: "btn btn-link btn-sm px-0", "data-action": "add-when", "data-phase": phase, "data-index": String(index), text: "+ Run only if…" }),
            h("span", { class: "text-secondary small", text: " Always runs" }),
        ]);
    }
    const others = payload.inputs.filter((item) => ["boolean", "choice", "string"].includes(item.type) && item.key);
    const source = inputByKey(row.input);
    return h("div", {}, [
        h("label", { class: "form-label", text: "Condition" }),
        h("div", { class: "nwe-cond" }, [
            h("span", { class: "nwe-where", text: "Run only if" }),
            h("select", { class: "form-select form-select-sm", "data-when": "input", "data-phase": phase, "data-index": String(index) }, others.map((item) => h("option", { value: item.key, text: item.label || item.key, selected: row.input === item.key }))),
            h("select", { class: "form-select form-select-sm", "data-when": "op", "data-phase": phase, "data-index": String(index) }, ["is", "is not"].map((op) => h("option", { value: op, text: op, selected: row.op === op }))),
            whenValue(source, row, phase, index),
            h("button", { type: "button", class: "btn btn-link btn-sm", "data-action": "when-jinja", "data-phase": phase, "data-index": String(index), text: "Switch to advanced (Jinja)" }),
            h("button", { type: "button", class: "nwe-icon", "data-action": "clear-when", "data-phase": phase, "data-index": String(index), text: "×" }),
        ]),
    ]);
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
                field("Window timezone", h("input", { class: "form-control", "data-bind": "schedule.timezone", value: schedule.timezone || "" })),
                gridField("Allowed change states", h("div", { class: "nwe-checks" }, (config.snow_states || []).map((name) => checkChip({ "data-action": "snow-state", "data-state": name }, (schedule.allowed_states || []).includes(name), name))), "The run is only allowed while the change is in one of these states."),
                h("details", {}, [
                    h("summary", { text: "Advanced · ServiceNow field names, ticket pattern" }),
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
    const next = (config.published_number || 0) + 1;
    const changes = changeList();
    return h("div", {}, [
        h("div", { class: "nwe-kicker", text: "Step 5 of 5" }),
        h("h2", { text: "Review & publish" }),
        h("p", { class: "text-secondary", text: config.published_number ? `Until you publish, people keep running v${config.published_number}.` : "Check it, then publish it for the first time. Until then nobody can run it." }),
        checks(),
        h("div", { class: "nwe-card", id: "nwe-review-diff" }, [
            h("div", { class: "fw-semibold", text: "What changes for people running it" }),
            h("div", { class: "small text-secondary mb-2", text: config.published_number ? `Compared with v${config.published_number}, which runs today.` : "This is the first version." }),
            ...changes.map((change) => h("div", { class: "nwe-change" }, [h("span", { class: `nwe-change-mark ${change[0] === "+" ? "add" : change[0] === "−" ? "del" : "mod"}`, text: change[0] }), change[1]])),
            h("button", { type: "button", class: "nwe-link", "data-action": "yaml-diff" }, [icon(state.yamlDiff ? "chevron-up" : "chevron-down"), state.yamlDiff ? "Hide the YAML diff" : "Show the YAML diff (what Git gets)"]),
            state.yamlDiff ? diffView(lineDiff(String(config.published_yaml || ""), state.yamlText || "")) : null,
        ]),
        h("div", { class: "nwe-card", id: "nwe-publish-card" }, [
            config.can_publish
                ? h("div", {}, [
                    h("div", { class: "fw-semibold", text: `Publish v${next}` }),
                    h("ol", {}, [
                        h("li", { text: `Saves it as v${next} with your name, the time and your note.` }),
                        h("li", { text: `Commits ${payload.key || "workflow"}.yaml${config.repository_name ? ` to ${config.repository_name}` : ""} and pushes it.` }),
                        h("li", { text: "Makes it the version everyone runs, right away. Runs already in progress finish on their version." }),
                    ]),
                    field("Note · optional", h("input", { class: "form-control", id: "nwe-note", "data-note": "1", value: state.note, placeholder: "Shown in Versions and used as the Git commit message." })),
                    !config.published_number
                        ? switchRow({ id: "nwe-enable-pub", "data-action": "enable-on-publish", checked: state.enableOnPublish }, "Turn it on when it’s published", "Off means it’s published but nobody can run it yet.")
                        : null,
                    h("div", { class: "d-flex align-items-center gap-2 mt-3" }, [
                        block ? h("span", { class: "text-danger small", text: block.message }) : h("span", { class: "text-success small", text: "Ready to publish" }),
                        h("button", { type: "button", class: "btn btn-light ms-auto", "data-action": "test", text: "Test run" }),
                        h("button", { type: "button", class: "btn nwe-publish", "data-action": "publish", disabled: Boolean(block), text: `Publish v${next}` }),
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

function previewPlan() {
    return h("div", {}, [
        ...PHASES.map(([phase, label]) => h("div", { class: "mb-2" }, [
            h("div", { class: "small fw-semibold", text: label }),
            ...(payload.actions[phase] || []).map((action) => h("div", { class: "small", text: action.label || action.name || "Step" })),
        ])),
        h("button", { type: "button", class: "btn btn-success w-100", "data-action": "test", text: "Test run this draft" }),
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
        const action = payload.actions[phase][Number(index)];
        if (action && !action._nameTouched && !stepLocked(action)) action.name = keyFrom(action.label);
    }
    if (path.endsWith(".name") && path.startsWith("actions.")) {
        const [, phase, index] = path.split(".");
        payload.actions[phase][Number(index)]._nameTouched = true;
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
    if (target.dataset.varKey) {
        const [phase, index] = target.dataset.varKey.split(":");
        const map = varBucket(payload.actions[phase][Number(index)]);
        const value = map[target.dataset.old];
        delete map[target.dataset.old];
        if (target.value) map[target.value] = value ?? "";
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
    touch();
});

app.addEventListener("click", (event) => {
    const node = event.target.closest("[data-action]");
    if (!node || !app.contains(node)) return;
    const action = node.dataset.action;
    if (action === "goto") {
        event.preventDefault();
        state.section = node.dataset.section;
        state.yaml = false;
        state.how = false;
        state.menu = false;
        renderAll();
    } else if (action === "how") {
        state.how = !state.how;
        renderHeader();
    } else if (action === "hint") {
        window.localStorage.setItem("nwe-how-hint", "1");
        renderHint();
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
    } else if (action === "remove-input") {
        const index = Number(node.dataset.index);
        const input = payload.inputs[index];
        const hits = referencesTo(payload, input.key);
        if (hits.length && !window.confirm(`Remove ${input.label || input.key}? It is used by ${hits.join(", ")}.`)) return;
        payload.inputs.splice(index, 1);
        renderAll();
        touch();
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
        actionRow._whenMode = "visual";
        actionRow.when = other ? writeWhen({ input: other.key, op: "is", value: other.type === "boolean" ? true : "" }) : "";
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
        if (!parseShow(actionRow.when)) actionRow.when = "";
        renderAll();
    } else if (action === "add-var") {
        const actionRow = payload.actions[node.dataset.phase][Number(node.dataset.index)];
        const map = varBucket(actionRow);
        const key = `param_${Object.keys(map).length + 1}`;
        map[key] = "";
        renderAll();
        touch();
    } else if (action === "rm-var") {
        const actionRow = payload.actions[node.dataset.phase][Number(node.dataset.index)];
        delete varBucket(actionRow)[node.dataset.key];
        renderAll();
        touch();
    } else if (action === "open-step") {
        const key = `${node.dataset.phase}:${node.dataset.index}`;
        state.openStep = state.openStep === key ? "" : key;
        renderAll();
    } else if (action === "remove-step") {
        payload.actions[node.dataset.phase].splice(Number(node.dataset.index), 1);
        renderAll();
        touch();
    } else if (action === "preview-tab") {
        state.previewTab = node.dataset.tab;
        renderPreview();
    } else if (action === "publish") {
        doPublish();
    } else if (action === "versions") {
        state.menu = false;
        state.how = false;
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
        toast(result.data.detail || "Publish did not complete.");
        return;
    }
    toast(`Published v${(config.published_number || 0) + 1}`);
    window.location.reload();
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

async function openVersions() {
    const body = document.getElementById("builder-versions-body");
    body.replaceChildren(h("p", { text: "Loading versions…" }));
    const modal = window.bootstrap.Modal.getOrCreateInstance(document.getElementById("builder-versions-dialog"));
    modal.show();
    if (!config.revisions_url) {
        body.textContent = "Nothing published yet. The first publish creates v1.";
        return;
    }
    const result = await api(config.revisions_url, {}, "GET");
    const revisions = Array.isArray(result.data) ? result.data.slice().reverse() : [];
    body.replaceChildren(
        h("p", { text: "Every publish is kept here. Restore copies a version into the draft. Nothing goes live until someone publishes." }),
        ...revisions.map((revision) => h("div", { class: "d-flex gap-2 align-items-start border-bottom py-2" }, [
            h("div", { class: "flex-grow-1" }, [
                h("div", { class: "fw-semibold", text: revision.state === "draft" ? "Saved draft" : `v${revision.number}` }),
                h("div", { class: "small text-secondary", text: `${revision.created_by || revision.published_by || ""} · ${revision.comment || "No note"} · ${revision.state}` }),
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

async function runTest() {
    const panel = window.bootstrap.Offcanvas.getOrCreateInstance(document.getElementById("builder-test-run-panel"));
    const host = document.getElementById("test-run-results");
    host.textContent = "Running…";
    panel.show();
    const result = await api(config.dry_run_url, { payload, inputs: state.preview });
    host.replaceChildren();
    if (!result.ok) {
        host.textContent = result.data.detail || "The test run did not start.";
        return;
    }
    const phases = result.data.phases || {};
    ["pre", "main", "post"].forEach((phase) => {
        (phases[phase] || []).forEach((step) => {
            host.append(h("div", { class: "mb-2" }, [
                h("div", { class: "fw-semibold", text: step.name || "Step" }),
                h("div", { class: "small", text: step.skip_reason || step.error || (step.will_run ? "Would run" : "Skipped") }),
            ]));
        });
    });
    if (!host.childNodes.length) host.textContent = "Nothing to run yet.";
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
    toast(state.gitStatus === "synced" ? "Synced" : state.gitError || "Retry started.");
    renderHeader();
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
if (config.key && new URLSearchParams(window.location.search).get("versions") === "1") {
    openVersions();
}
