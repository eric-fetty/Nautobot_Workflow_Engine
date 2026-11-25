/* Change freeze form: show the fields for the schedule kind, fill presets, and preview live. */
import { applyPreset, humanMinutes, matchPreset, previewLines } from "./hints.js";

const root = document.querySelector("[data-nwe-freeze-form]");
const form = root ? root.closest("form") : null;
const presets = JSON.parse(document.getElementById("nwe-freeze-presets")?.textContent || "{}");
const previewUrl = document.querySelector("[data-nwe-preview-url]")?.getAttribute("data-nwe-preview-url") || "";

function field(name) {
    return form ? form.querySelector(`[name="${name}"]`) : null;
}

function kind() {
    const checked = form?.querySelector('[name="kind"]:checked');
    return checked ? checked.value : "one_time";
}

function setValue(name, value) {
    const input = field(name);
    if (!input) return;
    if (input._flatpickr) input._flatpickr.setDate(value, false);
    else input.value = value;
    input.dispatchEvent(new Event("change", { bubbles: true }));
}

function paintKind() {
    root.querySelectorAll("[data-nwe-kind]").forEach((section) => {
        section.hidden = section.getAttribute("data-nwe-kind") !== kind();
    });
}

function paintDuration() {
    const input = field("duration_minutes");
    if (!input) return;
    let note = input.parentElement.querySelector("[data-nwe-duration]");
    if (!note) {
        note = document.createElement("div");
        note.className = "small text-secondary";
        note.setAttribute("data-nwe-duration", "1");
        input.insertAdjacentElement("afterend", note);
    }
    note.textContent = humanMinutes(input.value) ? `= ${humanMinutes(input.value)}` : "";
}

function values() {
    const data = new FormData(form);
    const body = {};
    for (const name of ["name", "message", "timezone", "kind", "starts_at", "ends_at", "rrule", "recur_start_date", "recur_start_time", "duration_minutes", "recur_until", "warning_lead_minutes"]) {
        body[name] = data.get(name) || "";
    }
    body.applies_to_all = data.get("applies_to_all") ? "true" : "false";
    for (const name of ["categories", "match_tags", "workflows", "excluded_workflows"]) body[name] = data.getAll(name);
    return body;
}

function csrf() {
    return form?.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
}

let timer = null;
let seq = 0;
function schedulePreview() {
    window.clearTimeout(timer);
    timer = window.setTimeout(preview, 350);
}

async function preview() {
    if (!previewUrl) return;
    const mine = ++seq;
    let body;
    try {
        const response = await fetch(previewUrl, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRFToken": csrf() },
            body: JSON.stringify(values()),
        });
        body = await response.json();
        if (!response.ok) body = { errors: { preview: body.detail || `HTTP ${response.status}` } };
    } catch (error) {
        body = { errors: { preview: String(error) } };
    }
    if (mine !== seq) return;
    const lines = previewLines(body);
    const card = document.getElementById("nwe-freeze-preview");
    card.querySelector("[data-nwe-preview-summary]").textContent = lines.summary || "Fill in the schedule to see the next windows.";
    const list = card.querySelector("[data-nwe-preview-windows]");
    list.replaceChildren(...lines.windows.map((text) => Object.assign(document.createElement("li"), { textContent: text })));
    card.querySelector("[data-nwe-preview-affects]").textContent = lines.affects;
    card.querySelector("[data-nwe-preview-runs]").textContent = lines.runs;
    card.querySelector("[data-nwe-preview-errors]").textContent = lines.errors;
}

if (root && form) {
    const presetInput = field("preset");
    const syncPreset = () => {
        if (presetInput) presetInput.value = matchPreset(presets, values());
        if (presetInput && window.jQuery) window.jQuery(presetInput).trigger("change.select2");
    };
    if (presetInput) {
        const onPreset = () => {
            const filled = applyPreset(presets, presetInput.value);
            if (!filled) return;
            setValue("rrule", filled.rrule);
            setValue("recur_start_time", filled.recur_start_time);
            setValue("duration_minutes", filled.duration_minutes);
            paintDuration();
            schedulePreview();
        };
        presetInput.addEventListener("change", onPreset);
        if (window.jQuery) window.jQuery(presetInput).on("select2:select", onPreset);
    }
    form.addEventListener("change", (event) => {
        if (event.target?.name === "kind") paintKind();
        if (["rrule", "recur_start_time", "duration_minutes"].includes(event.target?.name)) syncPreset();
        paintDuration();
        schedulePreview();
    });
    form.addEventListener("input", () => {
        paintDuration();
        schedulePreview();
    });
    if (window.jQuery) window.jQuery(form).on("select2:select select2:unselect", schedulePreview);
    paintKind();
    paintDuration();
    syncPreset();
    preview();
}
