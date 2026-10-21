/**Workflow builder: one shared draft, five steps, live preview. */

import { blankAction, blankInput, referencesTo, setPath, slugify } from "./store.js";

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
        else node.setAttribute(key, value === true ? "" : String(value));
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
            if (action.type === "nautobot" && !action.script) buckets.steps.push(`${who}: Choose which script to run`);
            if (action.type === "awx" && !action.template_id) buckets.steps.push(`${who}: choose an AWX template.`);
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
        const recent = state.savedAt && Date.now() - state.savedAt < 15000;
        node.append(h("span", { text: recent ? "Draft saved · just now" : "Draft saved" }));
    }
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

function renderHeader() {
    const header = document.getElementById("studio-header");
    const [text, kind] = statusText();
    const block = publishBlock();
    const git = gitIndicator();
    header.replaceChildren(
        h("a", { class: "btn btn-light btn-sm", href: config.list_url, title: "Back to workflows", text: "←" }),
        h("div", {}, [
            h("h1", { class: "nwe-title", id: "nwe-title", text: payload.name || "Untitled workflow" }),
            h("div", { class: "nwe-state" }, [
                h("span", { class: `nwe-pill ${kind === "ok" ? "nwe-pill-ok" : "nwe-pill-draft"}` }, [
                    h("span", { class: "dot" }),
                    text,
                ]),
                h("button", { type: "button", class: "btn btn-sm btn-light", "data-action": "how", title: "How editing works", text: "ⓘ" }),
                h("span", { id: "nwe-save" }),
            ]),
        ]),
        h("div", { class: "ms-auto d-flex flex-wrap align-items-center gap-2" }, [
            git.bad
                ? h("button", { type: "button", class: "btn btn-sm btn-outline-danger", "data-action": "retry", text: git.text })
                : h("span", { class: "text-secondary small", text: git.text }),
            h("div", { class: "form-check form-switch m-0" }, [
                h("input", {
                    class: "form-check-input",
                    type: "checkbox",
                    id: "nwe-enabled",
                    "data-action": "enabled",
                    checked: payload.enabled,
                    disabled: !config.published_number || config.read_only,
                }),
                h("label", { class: "form-check-label", for: "nwe-enabled", text: "Enabled", title: config.published_number ? "Applies immediately. It does not make a Git commit." : "Publish it first" }),
            ]),
            h("div", { class: "btn-group btn-group-sm" }, [
                h("button", { type: "button", class: `btn ${state.yaml ? "btn-light" : "btn-secondary"}`, "data-action": "mode", "data-mode": "visual", text: "Visual" }),
                h("button", { type: "button", class: `btn ${state.yaml ? "btn-secondary" : "btn-light"}`, "data-action": "mode", "data-mode": "yaml", text: "YAML" }),
            ]),
            h("div", { class: "position-relative" }, [
                h("button", { type: "button", class: "btn btn-light btn-sm", "data-action": "menu", text: "⋯" }),
                state.menu ? menu() : null,
            ]),
            h("div", { class: "d-flex align-items-center gap-2" }, [
                block ? h("a", { href: "#", class: "nwe-why text-danger", "data-action": "goto", "data-section": block.section, text: block.message }) : null,
                h("button", {
                    type: "button",
                    class: "btn nwe-publish",
                    "data-action": "publish",
                    disabled: Boolean(block) || config.read_only,
                    text: config.can_publish ? "Publish changes" : "Request publish",
                }),
            ]),
        ]),
    );
    if (state.how) header.querySelector(".nwe-state")?.append(howPop());
    paintSave();
}

function gitIndicator() {
    if (state.gitStatus === "not_synced" || state.gitStatus === "git_invalid") {
        return { text: "Not synced · Retry", bad: true };
    }
    if (!config.published_number) return { text: "Not in Git yet", bad: false };
    if (state.gitStatus === "pending") return { text: "Syncing…", bad: false };
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
    return h("div", { class: "nwe-pop", style: "right:0;top:2.2rem;width:15rem" }, [
        item("versions", "Versions"),
        item("test", "Test run the draft"),
        item("download", "Download draft YAML"),
        item("duplicate", "Duplicate"),
        h("hr", { class: "my-1" }),
        config.published_number
            ? item("discard", "Discard draft…")
            : item("discard", "Delete this draft…"),
    ]);
}

function item(action, text) {
    return h("button", { type: "button", class: "dropdown-item", "data-action": action, text });
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
                h("button", { type: "button", class: "btn btn-sm btn-light", "data-action": "compare", text: "Compare" }),
                h("button", { type: "button", class: "btn btn-sm btn-primary", "data-action": "load-git", text: "Load their version" }),
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
        h("div", { class: "nwe-stepper p-2" }, [
            h("div", { class: "small fw-semibold text-secondary px-2 py-1", text: "BUILD YOUR WORKFLOW" }),
            ...SECTIONS.map(([id, label], index) => {
                const count = buckets[id].length;
                const on = state.section === id && !state.yaml;
                const cls = ["nwe-step", on ? "on" : "", count ? "err" : ""].filter(Boolean).join(" ");
                return h("button", { type: "button", class: cls, "data-action": "goto", "data-section": id }, [
                    h("span", { class: "nwe-num", text: count ? "!" : String(index + 1) }),
                    h("span", {}, [
                        h("span", { class: "fw-semibold d-block", text: label }),
                        h("span", { class: `small ${count ? "text-danger" : "text-secondary"}`, text: count ? `${count} issue${count === 1 ? "" : "s"} to fix` : summary(id) }),
                    ]),
                ]);
            }),
            h("div", { class: "small text-secondary p-2" }, [
                "Tip: Prefer code? ",
                h("button", { type: "button", class: "btn btn-link btn-sm p-0", "data-action": "mode", "data-mode": "yaml", text: "Edit as YAML" }),
                ". Both views always show the same draft.",
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
            h("div", { class: "d-flex justify-content-between mt-3" }, [
                index > 0 ? h("button", { type: "button", class: "btn btn-light", "data-action": "goto", "data-section": SECTIONS[index - 1][0], text: `← Back: ${SECTIONS[index - 1][1]}` }) : h("span"),
                index < SECTIONS.length - 1 ? h("button", { type: "button", class: "btn nwe-publish", "data-action": "goto", "data-section": SECTIONS[index + 1][0], text: `Next: ${SECTIONS[index + 1][1]} →` }) : h("span"),
            ]),
        ]),
    );
}

function sectionView() {
    const id = state.section;
    if (id === "inputs") return inputsView();
    if (id === "steps") return stepsView();
    if (id === "schedule") return scheduleView();
    if (id === "review") return reviewView();
    return detailsView();
}

function detailsView() {
    const manual = Number(payload.manual_duration_minutes || 0);
    const hours = manual ? Math.round((48 * Math.max(0, manual - 4)) / 60) : 0;
    return h("div", {}, [
        h("div", { class: "nwe-kicker", text: "Step 1 of 5" }),
        h("h2", { text: "Details" }),
        h("p", { class: "text-secondary", text: "What this workflow is, and how long it takes by hand." }),
        field("Name", h("input", { class: "form-control", "data-bind": "name", value: payload.name || "" }), "The name on the list and the launch form."),
        field("Description", h("textarea", { class: "form-control", rows: "3", "data-bind": "description", value: payload.description || "" }), "Shown under the name."),
        field("Category", h("input", { class: "form-control", "data-bind": "category", value: payload.category || "" }), "Groups the workflow on the list."),
        field(
            "Manual effort per run (minutes)",
            h("input", { class: "form-control", type: "number", min: "0", "data-bind": "manual_duration_minutes", value: payload.manual_duration_minutes ?? "" }),
            `Example: 48 runs × ${manual || "—"} min by hand, minus about 4 min per automated run ≈ ${hours} hours saved a month on the dashboard.`,
        ),
        field(
            "Repository",
            h("select", { class: "form-select", "data-bind": "source_repo" }, [
                h("option", { value: "", text: "Choose a repository…" }),
                ...(config.repositories || []).map((repo) => h("option", { value: repo.id, text: repo.name, selected: String(payload.source_repo || "") === String(repo.id) })),
            ]),
            "Published YAML is committed here. Drafts stay in Nautobot.",
        ),
        h("details", { class: "mt-2" }, [
            h("summary", { text: "Advanced · workflow ID" }),
            field(
                "Workflow ID",
                h("input", { class: "form-control", "data-bind": "key", value: payload.key || "", disabled: Boolean(config.key) }),
                config.key ? "Locked after the first save. Referenced in URLs and in Git." : "Made from the name. It locks once the workflow is saved.",
            ),
        ]),
    ]);
}

function inputsView() {
    return h("div", {}, [
        h("div", { class: "nwe-kicker", text: "Step 2 of 5" }),
        h("div", { class: "d-flex align-items-center" }, [
            h("h2", { class: "me-auto", text: "Inputs" }),
            h("button", { type: "button", class: "btn btn-light btn-sm", "data-action": "add-type", text: "+ Add input" }),
        ]),
        h("p", { class: "text-secondary", text: "The fields people fill in when they run the workflow, in this order." }),
        state.addType ? typePicker() : null,
        ...payload.inputs.map((input, index) => inputRow(input, index)),
        h("button", { type: "button", class: "btn btn-light w-100", "data-action": "add-type", text: "+ Add input" }),
    ]);
}

function typePicker() {
    return h("div", { class: "nwe-card" }, [
        h("div", { class: "fw-semibold mb-2", text: "What should people enter?" }),
        h("div", { class: "d-flex flex-wrap gap-2" }, TYPES.map(([type, label]) => h("button", { type: "button", class: "btn btn-outline-secondary btn-sm", "data-action": "add-input", "data-type": type, text: label }))),
    ]);
}

function inputRow(input, index) {
    const open = state.openInput === index;
    const tags = [];
    if (input.required) tags.push("Required");
    if ((input.filter_rows || []).length || input.filter_mode === "jinja") tags.push("Filtered");
    if ((input.depends_on || []).length) tags.push(`Waits for ${(input.depends_on || []).join(", ")}`);
    if (input.show_if && (typeof input.show_if === "string" ? input.show_if : Object.keys(input.show_if).length)) tags.push("Conditional");
    if ((input.validators || []).length) tags.push("Validated");
    return h("div", {}, [
        h("div", { class: `nwe-row ${open ? "open" : ""}`, draggable: "true", "data-drag": "input", "data-index": String(index) }, [
            h("span", { class: "text-secondary", text: "⋮⋮", title: "Drag to reorder" }),
            h("button", { type: "button", class: "btn btn-link text-start flex-grow-1 p-0 text-decoration-none", "data-action": "open-input", "data-index": String(index) }, [
                h("span", { class: "fw-semibold", "data-row-label": String(index), text: input.label || "Untitled" }),
                h("span", { class: "text-secondary small ms-2", text: TYPE_LABEL[input.type] || input.type }),
                ...tags.map((tag) => h("span", { class: "badge text-bg-light ms-1", text: tag })),
            ]),
            h("button", { type: "button", class: "btn btn-sm btn-light", "data-action": "remove-input", "data-index": String(index), title: "Remove", text: "🗑" }),
            h("button", { type: "button", class: "btn btn-sm btn-light", "data-action": "open-input", "data-index": String(index), text: open ? "▴" : "▾" }),
        ]),
        open ? inputEditor(input, index) : null,
    ]);
}

function inputEditor(input, index) {
    const base = `inputs.${index}`;
    return h("div", { class: "nwe-card" }, [
        field("Label", h("input", { class: "form-control", "data-bind": `${base}.label`, value: input.label || "" })),
        field("Field type", h("select", { class: "form-select", "data-bind": `${base}.type` }, TYPES.map(([type, label]) => h("option", { value: type, text: label, selected: input.type === type })))),
        field("Help text", h("input", { class: "form-control", "data-bind": `${base}.help_text`, value: input.help_text || "" }), "Optional. Shown under the field."),
        input.type === "string" || input.type === "integer" || input.type === "list"
            ? field("Placeholder", h("input", { class: "form-control", "data-bind": `${base}.placeholder`, value: input.placeholder || "" }))
            : null,
        input.type === "integer"
            ? h("div", { class: "row" }, [
                h("div", { class: "col" }, [field("Minimum", h("input", { class: "form-control", type: "number", "data-bind": `${base}.min_value`, value: input.min_value ?? "" }))]),
                h("div", { class: "col" }, [field("Maximum", h("input", { class: "form-control", type: "number", "data-bind": `${base}.max_value`, value: input.max_value ?? "" }))]),
            ])
            : null,
        input.type === "choice" || input.type === "multichoice" ? choicesEditor(input, index) : null,
        input.type === "object" || input.type === "multiobject" ? filterEditor(input, index) : null,
        input.type === "list"
            ? h("div", { class: "row" }, [
                h("div", { class: "col" }, [field("At least", h("input", { class: "form-control", type: "number", min: "0", "data-bind": `${base}.min_items`, value: input.min_items ?? "" }))]),
                h("div", { class: "col" }, [field("At most", h("input", { class: "form-control", type: "number", min: "0", "data-bind": `${base}.max_items`, value: input.max_items ?? "" }))]),
            ])
            : null,
        input.type === "listdict" ? tableEditor(input, index) : null,
        h("div", { class: "form-check form-switch" }, [
            h("input", { class: "form-check-input", type: "checkbox", "data-bind": `${base}.required`, "data-check": "1", checked: input.required, id: `req-${index}` }),
            h("label", { class: "form-check-label", for: `req-${index}`, text: "Required" }),
        ]),
        h("details", { class: "mt-3" }, [
            h("summary", { text: "Advanced · variable name" }),
            field(
                "Variable name",
                h("input", { class: "form-control", "data-bind": `${base}.key`, value: input.key || "", disabled: inputLocked(input) }),
                inputLocked(input) ? "Locked because a published version uses it." : `Referenced as {{ inputs.${input.key || "name"} }} · made from the label.`,
            ),
        ]),
    ]);
}

function choicesEditor(input, index) {
    input.choices = input.choices || [];
    return h("div", { class: "mb-3" }, [
        h("div", { class: "form-label", text: "Options" }),
        ...input.choices.map((choice, choiceIndex) => h("div", { class: "d-flex gap-2 mb-1" }, [
            h("input", { class: "form-control", placeholder: "Value", "data-bind": `inputs.${index}.choices.${choiceIndex}.value`, value: choice.value || "" }),
            h("input", { class: "form-control", placeholder: "Label", "data-bind": `inputs.${index}.choices.${choiceIndex}.label`, value: choice.label || "" }),
            h("button", { type: "button", class: "btn btn-light", "data-action": "remove-choice", "data-index": String(index), "data-choice": String(choiceIndex), text: "×" }),
        ])),
        h("button", { type: "button", class: "btn btn-sm btn-light", "data-action": "add-choice", "data-index": String(index), text: "+ Option" }),
    ]);
}

function filterEditor(input, index) {
    const models = config.content_types || [];
    scheduleCount(index);
    return h("div", {}, [
        field("Which kind of object?", (() => {
            const select = h("select", { class: "form-select", "data-bind": `inputs.${index}.model` }, [
                h("option", { value: "", text: "Choose…" }),
                ...models.map((model) => h("option", { value: model.value, text: model.label })),
            ]);
            select.value = input.model || "";
            return select;
        })()),
        h("div", { class: "d-flex justify-content-between" }, [
            h("div", { class: "form-label", text: "Only offer objects where…" }),
            h("button", { type: "button", class: "btn btn-link btn-sm", "data-action": "filter-mode", "data-index": String(index), text: input.filter_mode === "jinja" ? "Switch to the filter builder" : "Switch to advanced (Jinja)" }),
        ]),
        input.filter_mode === "jinja"
            ? field("Jinja or YAML query", h("textarea", { class: "form-control", rows: "4", "data-bind": `inputs.${index}.filter_query`, value: input.filter_query || "" }))
            : h("div", {}, [
                ...(input.filter_rows || []).map((row, rowIndex) => filterRow(input, index, row, rowIndex)),
                h("button", { type: "button", class: "btn btn-link btn-sm px-0", "data-action": "add-filter", "data-index": String(index), text: "+ Add condition" }),
                h("div", { class: "small mt-2", id: `nwe-count-${index}`, text: state.counts[index] || "The count updates as you edit the filter." }),
            ]),
    ]);
}

function filterRow(input, index, row, rowIndex) {
    const base = `inputs.${index}.filter_rows.${rowIndex}`;
    const others = payload.inputs.filter((item, itemIndex) => itemIndex !== index && item.key);
    return h("div", { class: "d-flex flex-wrap gap-2 mb-2 align-items-center" }, [
        h("span", { class: "small text-secondary", text: rowIndex === 0 ? "WHERE" : "AND" }),
        h("input", { class: "form-control", style: "max-width:10rem", placeholder: "Field", "data-bind": `${base}.field`, value: row.field || "" }),
        h("select", { class: "form-select", style: "max-width:9rem", "data-bind": `${base}.op` }, OPS.map((op) => h("option", { value: op, text: op, selected: (row.op || "is") === op }))),
        h("select", { class: "form-select", style: "max-width:11rem", "data-bind": `${base}.source` }, [
            h("option", { value: "value", text: "Fixed value", selected: row.source !== "input" }),
            h("option", { value: "input", text: "From another input", selected: row.source === "input" }),
        ]),
        row.source === "input"
            ? h("select", { class: "form-select", style: "max-width:12rem", "data-bind": `${base}.input` }, [
                h("option", { value: "", text: "Choose an input…" }),
                ...others.map((item) => h("option", { value: item.key, text: item.label || item.key, selected: row.input === item.key })),
            ])
            : h("input", { class: "form-control", style: "max-width:12rem", "data-bind": `${base}.value`, value: row.value ?? "" }),
        h("button", { type: "button", class: "btn btn-light", "data-action": "remove-filter", "data-index": String(index), "data-row": String(rowIndex), text: "×" }),
    ]);
}

function tableEditor(input, index) {
    input.columns = input.columns || [];
    return h("div", { class: "mb-3" }, [
        h("div", { class: "form-label", text: "Columns" }),
        ...input.columns.map((column, columnIndex) => h("div", { class: "border rounded p-2 mb-2" }, [
            h("div", { class: "row g-2" }, [
                h("div", { class: "col" }, [h("input", { class: "form-control", placeholder: "Label", "data-bind": `inputs.${index}.columns.${columnIndex}.label`, value: column.label || "" })]),
                h("div", { class: "col" }, [h("input", { class: "form-control", placeholder: "Key", "data-bind": `inputs.${index}.columns.${columnIndex}.key`, value: column.key || "" })]),
                h("div", { class: "col" }, [h("select", { class: "form-select", "data-bind": `inputs.${index}.columns.${columnIndex}.type` }, ["string", "integer", "number", "boolean", "choice"].map((type) => h("option", { value: type, text: type, selected: (column.type || "string") === type })))]),
            ]),
            h("div", { class: "row g-2 mt-1" }, [
                h("div", { class: "col" }, [h("input", { class: "form-control", type: "number", placeholder: "Min", "data-bind": `inputs.${index}.columns.${columnIndex}.min_value`, value: column.min_value ?? "" })]),
                h("div", { class: "col" }, [h("input", { class: "form-control", type: "number", placeholder: "Max", "data-bind": `inputs.${index}.columns.${columnIndex}.max_value`, value: column.max_value ?? "" })]),
                h("div", { class: "col" }, [h("button", { type: "button", class: "btn btn-light", "data-action": "remove-column", "data-index": String(index), "data-column": String(columnIndex), text: "Remove column" })]),
            ]),
        ])),
        h("button", { type: "button", class: "btn btn-sm btn-light", "data-action": "add-column", "data-index": String(index), text: "+ Column" }),
        h("div", { class: "row mt-2" }, [
            h("div", { class: "col" }, [field("At least rows", h("input", { class: "form-control", type: "number", min: "0", "data-bind": `inputs.${index}.min_rows`, value: input.min_rows ?? "" }))]),
            h("div", { class: "col" }, [field("At most rows", h("input", { class: "form-control", type: "number", min: "0", "data-bind": `inputs.${index}.max_rows`, value: input.max_rows ?? "" }))]),
        ]),
        h("div", { class: "form-check form-switch" }, [
            h("input", { class: "form-check-input", type: "checkbox", "data-bind": `inputs.${index}.csv_import`, "data-check": "1", checked: input.csv_import !== false, id: `csv-${index}` }),
            h("label", { class: "form-check-label", for: `csv-${index}`, text: "Allow CSV import" }),
        ]),
        h("div", { class: "form-text", text: "Import CSV reads the file in the browser. It is not uploaded or stored." }),
    ]);
}

function stepsView() {
    return h("div", {}, [
        h("div", { class: "nwe-kicker", text: "Step 3 of 5" }),
        h("h2", { text: "Steps" }),
        h("p", { class: "text-secondary", text: "What happens when someone runs this workflow, from top to bottom." }),
        ...PHASES.map(([phase, label, help]) => h("div", {}, [
            h("div", { class: "nwe-phase" }, [
                h("div", { class: "fw-semibold", text: `${label} · ${(payload.actions[phase] || []).length}` }),
                h("div", { class: "small text-secondary", text: help }),
            ]),
            ...(payload.actions[phase] || []).map((action, index) => stepCard(phase, action, index)),
            h("button", { type: "button", class: "btn btn-sm btn-light", "data-action": "add-step", "data-phase": phase, text: "+ Add step" }),
        ])),
    ]);
}

function stepCard(phase, action, index) {
    const open = state.openStep === `${phase}:${index}`;
    const missing = action.type !== "awx" && !action.script;
    const sentence = action.type === "awx"
        ? `Launch AWX template ${action.template_id || "…"}`
        : missing
            ? "Choose which script to run"
            : `Run script ${action.script}`;
    return h("div", { class: `nwe-card ${missing ? "bad" : ""}`, draggable: "true", "data-drag": "step", "data-phase": phase, "data-index": String(index) }, [
        h("div", { class: "d-flex align-items-start gap-2" }, [
            h("span", { class: "text-secondary", text: "⋮⋮" }),
            h("button", { type: "button", class: "btn btn-link text-start p-0 text-decoration-none flex-grow-1", "data-action": "open-step", "data-phase": phase, "data-index": String(index) }, [
                h("div", { class: "fw-semibold", text: action.label || "Untitled step" }),
                h("div", { class: `small ${missing ? "text-danger" : "text-secondary"}`, text: sentence }),
            ]),
            h("button", { type: "button", class: "btn btn-sm btn-light", "data-action": "remove-step", "data-phase": phase, "data-index": String(index), text: "🗑" }),
        ]),
        open ? stepEditor(phase, action, index) : null,
    ]);
}

function stepEditor(phase, action, index) {
    const base = `actions.${phase}.${index}`;
    const scripts = (config.scripts_by_repo && config.scripts_by_repo[payload.source_repo]) || [];
    return h("div", { class: "mt-3" }, [
        field("Step name", h("input", { class: "form-control", "data-bind": `${base}.label`, value: action.label || "" })),
        field("Type", h("select", { class: "form-select", "data-bind": `${base}.type` }, [
            h("option", { value: "nautobot", text: "Nautobot script", selected: action.type !== "awx" }),
            h("option", { value: "awx", text: "AWX job", selected: action.type === "awx" }),
        ])),
        action.type === "awx"
            ? field("Template ID", h("input", { class: "form-control", "data-bind": `${base}.template_id`, value: action.template_id ?? "" }))
            : field("Script", h("input", { class: "form-control", list: "nwe-scripts", "data-bind": `${base}.script`, value: action.script || "", placeholder: "Choose which script to run" }), scripts.length ? "" : "Choose a repository to list its scripts."),
        h("datalist", { id: "nwe-scripts" }, scripts.map((path) => h("option", { value: path }))),
        field("Run only if", h("input", { class: "form-control", "data-bind": `${base}.when`, value: action.when || "", placeholder: "{{ inputs.confirm }}" }), "Leave empty to always run. A visual condition is still a Jinja expression."),
        h("details", {}, [
            h("summary", { text: "Advanced · step key" }),
            field(
                "Step key",
                h("input", { class: "form-control", "data-bind": `${base}.name`, value: action.name || "", disabled: stepLocked(action) }),
                stepLocked(action) ? `Locked because v${config.published_number} uses it.` : "Made from the step name. Later steps use {{ results." + (action.name || "name") + " }}.",
            ),
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
            h("div", { class: "form-check form-switch" }, [
                h("input", { class: "form-check-input", type: "checkbox", id: "nwe-approval", "data-bind": "approval_required", "data-check": "1", checked: payload.approval_required }),
                h("label", { class: "form-check-label", for: "nwe-approval", text: "Require approval before it runs" }),
            ]),
            h("div", { class: "form-text", text: "Uses Nautobot Approval Workflows. Each run waits until it’s approved." }),
        ]),
        h("div", { class: "nwe-card" }, [
            h("div", { class: "form-check form-switch" }, [
                h("input", { class: "form-check-input", type: "checkbox", id: "nwe-window", "data-bind": "schedule.enabled", "data-check": "1", checked: schedule.enabled }),
                h("label", { class: "form-check-label", for: "nwe-window", text: "Only run inside an approved ServiceNow change window" }),
            ]),
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
                field("Allowed change states", h("input", { class: "form-control", "data-bind": "schedule.allowed_states", value: (schedule.allowed_states || []).join(", ") }), "Comma-separated. The run is only allowed while the change is in one of these states."),
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
            ...changes.map((change) => h("div", { class: "mb-1" }, [h("span", { class: "me-2", text: change[0] }), change[1]])),
            h("button", { type: "button", class: "btn btn-link btn-sm px-0", "data-action": "yaml-diff", text: state.yamlDiff ? "Hide the YAML diff" : "Show the YAML diff (what Git gets)" }),
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
                        ? h("div", { class: "form-check form-switch" }, [
                            h("input", { class: "form-check-input", type: "checkbox", id: "nwe-enable-pub", "data-action": "enable-on-publish", checked: state.enableOnPublish }),
                            h("label", { class: "form-check-label", for: "nwe-enable-pub", text: "Turn it on when it’s published" }),
                        ])
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
        h("div", { class: "fw-semibold mb-2", text: "Checks" }),
        ...rows.map(([id, label, detail]) => h("div", { class: "d-flex gap-2 mb-2" }, [
            h("span", { text: buckets[id].length ? "!" : "✓" }),
            h("div", { class: "flex-grow-1" }, [
                h("div", { class: "fw-semibold", text: label }),
                h("div", { class: "small text-secondary", text: detail }),
                ...buckets[id].slice(0, 3).map((message) => h("div", { class: "small text-danger", text: message })),
            ]),
            h("button", { type: "button", class: "btn btn-link btn-sm", "data-action": "goto", "data-section": id, text: "Edit" }),
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
    return h("div", {}, [
        h("div", { class: "nwe-kicker", text: "Edit as YAML" }),
        h("h2", { text: `${payload.key || "workflow"}.yaml` }),
        h("p", { class: "text-secondary", text: locked ? "You need the Author templates and scripts permission to edit YAML." : "The draft, as YAML. Edits apply to the draft. Git gets this file when you publish." }),
        h("textarea", { class: "form-control nwe-yaml", "data-yaml": "1", disabled: locked, text: state.yamlText || "" }),
        state.yamlError ? h("div", { class: "text-danger small mt-2", text: state.yamlError }) : h("div", { class: "text-success small mt-2", text: state.yamlText ? "Valid · edits apply to the draft as you type" : "" }),
    ]);
}

function renderPreview() {
    const host = document.getElementById("studio-preview");
    const tab = state.previewTab;
    host.replaceChildren(
        h("div", { class: "nwe-preview-card" }, [
            h("div", { class: "d-flex align-items-center p-2 border-bottom" }, [
                h("span", { class: "fw-semibold", text: "Preview" }),
                h("div", { class: "btn-group btn-group-sm ms-auto" }, [
                    h("button", { type: "button", class: `btn ${tab === "form" ? "btn-secondary" : "btn-light"}`, "data-action": "preview-tab", "data-tab": "form", text: "Launch form" }),
                    h("button", { type: "button", class: `btn ${tab === "plan" ? "btn-secondary" : "btn-light"}`, "data-action": "preview-tab", "data-tab": "plan", text: "What runs" }),
                ]),
            ]),
            h("div", { class: "p-3" }, [tab === "form" ? previewForm() : previewPlan()]),
            h("div", { class: "small text-secondary px-3 pb-3", text: tab === "form" ? "The draft's launch form. People running it see the published version until you publish." : "The order things happen when someone runs it." }),
        ]),
    );
}

function previewForm() {
    if (!payload.inputs.length) return h("p", { class: "text-secondary", text: "Add an input to see it here." });
    return h("div", {}, payload.inputs.map((input) => h("div", { class: `mb-3 ${state.openInput != null && payload.inputs[state.openInput] === input && state.section === "inputs" ? "border border-warning rounded p-2" : ""}` }, [
        h("label", { class: "form-label", text: `${input.label || "Untitled"}${input.required ? " *" : ""}` }),
        previewControl(input),
        input.help_text ? h("div", { class: "form-text", text: input.help_text }) : null,
    ])));
}

function previewControl(input) {
    if (input.type === "boolean") return h("div", { class: "form-check form-switch" }, [h("input", { class: "form-check-input", type: "checkbox", disabled: true })]);
    if (input.type === "choice" && (input.choices || []).length && input.choices.length <= 4) {
        return h("div", {}, input.choices.map((choice) => h("div", { class: "form-check" }, [
            h("input", { class: "form-check-input", type: "radio", disabled: true }),
            h("label", { class: "form-check-label", text: choice.label || choice.value || "Option" }),
        ])));
    }
    if (input.type === "listdict") return h("div", { class: "form-text", text: "Editable table, with paste, Download template, and Import CSV on the launch form." });
    return h("input", { class: "form-control", disabled: true, placeholder: input.placeholder || (input.type === "object" ? "Choose…" : "") });
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
    if (path.includes(".filter_rows.")) scheduleCount(Number(path.split(".")[1]));
}

function scheduleCount(index) {
    const input = payload.inputs[index];
    if (!input || !input.model || !config.filter_count_url) return;
    clearTimeout(input._countTimer);
    input._countTimer = window.setTimeout(async () => {
        const query = {};
        (input.filter_rows || []).forEach((row) => {
            if (!row.field) return;
            const op = row.op || "is";
            let value = row.source === "input" && row.input ? `{{ inputs.${row.input}.id }}` : row.value;
            let key = row.field;
            if (op === "is not") key = `${row.field}__n`;
            else if (op === "contains") key = `${row.field}__ic`;
            else if (op === "starts with") key = `${row.field}__isw`;
            else if (op === "is empty") {
                key = `${row.field}__isnull`;
                value = true;
            } else if (op === "is one of") value = String(row.value || "").split(",").map((part) => part.trim()).filter(Boolean);
            query[key] = value;
        });
        const result = await api(config.filter_count_url, { model: input.model, query, values: state.preview });
        const node = document.getElementById(`nwe-count-${index}`);
        if (!node) return;
        if (!result.ok) {
            node.textContent = result.data.detail || "The count could not be loaded.";
            return;
        }
        const names = (result.data.names || []).slice(0, 3).join(", ");
        const count = result.data.count;
        const modelName = String(input.model || "record").split(".").pop().toLowerCase();
        const noun = count === 1 ? modelName : `${modelName}s`;
        const verb = count === 1 ? "matches" : "match";
        node.textContent = `${count} ${noun} ${verb}${names ? ` · ${names}` : ""}`;
        state.counts[index] = node.textContent;
    }, 400);
}

function valueOf(target) {
    if (target.type === "checkbox") return target.checked;
    return target.value;
}

app.addEventListener("input", (event) => {
    const target = event.target;
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
    if (target.dataset.action === "enable-on-publish") {
        state.enableOnPublish = target.checked;
        return;
    }
    if (!target.dataset.bind) return;
    assign(target.dataset.bind, valueOf(target));
    if (target.tagName === "SELECT") renderAll();
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
        actionRow._nameTouched = false;
        payload.actions[node.dataset.phase].push(actionRow);
        state.openStep = `${node.dataset.phase}:${payload.actions[node.dataset.phase].length - 1}`;
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
        state.yamlError = error ? `Line ${error.line || "?"}: ${error.message}` : result.data.detail || "The YAML could not be read.";
        renderEditor();
        return;
    }
    state.yamlError = "";
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
                : h("button", { type: "button", class: "btn btn-sm btn-light", "data-restore": String(revision.number), text: "Restore into draft" }),
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
