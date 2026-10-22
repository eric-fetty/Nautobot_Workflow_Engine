/** The launch form: draws the fields from the page config and posts them in the shape the server expects. */

import { h, icon, plural } from "../shared/dom.js";
import { createField } from "../shared/fields.js";
import { fieldError, isEmpty } from "../shared/validate.js";

const config = JSON.parse(document.getElementById("nwe-launch-config").textContent || "{}");
const form = document.getElementById("workflow-launch-form");
const host = document.getElementById("nwe-launch-fields");
const valuesHost = document.getElementById("nwe-launch-values");
const timingHost = document.getElementById("nwe-launch-timing");
const toastHost = document.getElementById("nwe-launch-toast");

const specs = config.fields || [];
const byKey = new Map(specs.map((spec) => [spec.key, spec]));
const values = {};
const fields = {};
const waits = {};
const touched = new Set();
let startedAt = 0;

/* ---------- options from the server ---------- */
function depParams(spec) {
    const params = new URLSearchParams();
    (spec.depends_on || []).forEach((dep) => {
        const value = values[dep];
        if (isEmpty(value)) return;
        params.append(dep, Array.isArray(value) ? JSON.stringify(value) : String(value));
    });
    return params;
}

async function loadOptions(spec, { search, offset }) {
    const params = depParams(spec);
    if (search) params.set("search", search);
    if (offset) params.set("offset", String(offset));
    const url = `${config.options_url.replace("__CHILD__", encodeURIComponent(spec.key))}${params.toString() ? `?${params}` : ""}`;
    let response;
    try {
        response = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } });
    } catch (error) {
        return { options: [], total: 0, error: "Couldn't reach Nautobot to load the options." };
    }
    let data = {};
    try {
        data = await response.json();
    } catch (error) {
        data = {};
    }
    if (!response.ok || data.error) return { options: [], total: 0, error: data.error || data.detail || "Couldn't load the options." };
    const options = data.options || [];
    return { options, total: data.total_count ?? options.length, hasMore: Boolean(data.has_more) };
}

/* ---------- visibility ---------- */
function truthy(value) {
    if (Array.isArray(value)) return value.length > 0;
    const text = String(value ?? "").trim().toLowerCase();
    return text !== "" && !["false", "none", "0", "no"].includes(text);
}

function asText(value) {
    if (typeof value === "boolean") return value ? "true" : "false";
    return String(value ?? "");
}

function shown(spec) {
    const rule = spec.show_if;
    if (!rule) return true;
    if (rule.kind === "truthy") return truthy(values[rule.key]);
    if (rule.kind === "map") {
        return Object.entries(rule.conditions).every(([key, allowed]) => {
            const current = values[key];
            if (Array.isArray(current)) return current.some((item) => allowed.includes(asText(item)));
            return allowed.includes(asText(current));
        });
    }
    return true;
}

function waitingOn(spec) {
    return (spec.depends_on || []).filter((dep) => byKey.has(dep) && isEmpty(values[dep]));
}

function isVisible(spec) {
    return shown(spec) && !waitingOn(spec).length;
}

function paintVisibility() {
    specs.forEach((spec) => {
        const field = fields[spec.key];
        const wait = waits[spec.key];
        const visible = shown(spec);
        const blocked = waitingOn(spec);
        field.el.hidden = !visible || blocked.length > 0;
        if (wait) {
            wait.hidden = !visible || !blocked.length;
            const names = blocked.map((dep) => byKey.get(dep)?.label || dep);
            wait.querySelector("[data-wait]").textContent = `Choose ${names.join(" and ")} first.`;
        }
    });
}

/* ---------- values posted to the server ---------- */
function serialize() {
    valuesHost.replaceChildren();
    specs.forEach((spec) => {
        const value = values[spec.key];
        let text;
        if (spec.type === "boolean") {
            if (value !== true) return;
            text = "on";
        } else if (["multichoice", "multiobject", "list", "listdict"].includes(spec.type)) {
            text = JSON.stringify(Array.isArray(value) ? value : []);
        } else text = value == null ? "" : String(value);
        valuesHost.append(h("input", { type: "hidden", name: spec.key, value: text }));
    });
}

/* ---------- errors ---------- */
function validate(spec) {
    if (!isVisible(spec)) return "";
    return fieldError(spec, values[spec.key]);
}

function showToast(message) {
    const close = h("button", { type: "button", class: "nwe-iconbtn", "aria-label": "Dismiss" }, [icon("close")]);
    const toast = h("div", { class: "nwe-toast", role: "alert" }, [icon("information-outline"), h("span", { text: message }), close]);
    close.addEventListener("click", () => toast.remove());
    toastHost.replaceChildren(toast);
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => toast.remove(), 8000);
}

/* ---------- build the form ---------- */
function onChange(key, value) {
    if (!startedAt) startedAt = Date.now();
    values[key] = value;
    touched.add(key);
    const spec = byKey.get(key);
    if (fields[key].el.classList.contains("has-error")) fields[key].setError(validate(spec));
    paintVisibility();
    specs.forEach((other) => {
        if ((other.depends_on || []).includes(key) && fields[other.key] && !waitingOn(other).length) fields[other.key].reload();
    });
}

specs.forEach((spec) => {
    values[spec.key] = spec.value;
    const field = createField(spec, { value: spec.value, initialLabels: spec.labels, onChange, loadOptions });
    fields[spec.key] = field;
    host.append(field.el);
    if ((spec.depends_on || []).length) {
        const wait = h("div", { class: "nwe-f", "data-wait-for": spec.key, hidden: true }, [
            h("div", { class: "nwe-f-label" }, [spec.label, spec.required ? h("span", { class: "nwe-req", text: "*" }) : null]),
            h("div", { class: "nwe-f-wait" }, [icon("timer-sand"), h("span", { "data-wait": "1" })]),
        ]);
        waits[spec.key] = wait;
        host.append(wait);
    }
    const posted = (config.errors || {})[spec.key];
    if (posted && posted.length) field.setError(posted.join(" "));
});
paintVisibility();
specs.forEach((spec) => {
    const isPicker = ["object", "multiobject"].includes(spec.type) || (spec.dynamic && ["choice", "multichoice"].includes(spec.type));
    if (isPicker && isVisible(spec)) fields[spec.key].reload();
});
if (!specs.length) host.append(h("p", { class: "nwe-muted mb-0", text: "This workflow doesn't ask for anything. Choose when it should run." }));

/* ---------- when it runs ---------- */
const execution = config.execution || { choices: [], value: "immediate" };
let executionValue = execution.value || "immediate";
const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";

function pad(number) {
    return String(number).padStart(2, "0");
}

function localStamp(date) {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

const when = h("input", { type: "datetime-local", class: "form-control nwe-when", id: "nwe-when", value: execution.scheduled || localStamp(new Date(Date.now() + 10 * 60000)) });
when.min = localStamp(new Date(Date.now() + 5 * 60000));
const timingError = h("div", { class: "nwe-f-error", hidden: true });

function paintTiming() {
    if (!timingHost) return;
    const cards = h("div", { class: "nwe-rcards nwe-timing-cards", role: "radiogroup", "aria-labelledby": "nwe-timing-title" }, execution.choices.map((choice) => {
        const on = choice.value === executionValue;
        const input = h("input", { type: "radio", name: "nwe-execution", value: choice.value, checked: on });
        input.addEventListener("change", () => {
            executionValue = choice.value;
            timingError.hidden = true;
            paintTiming();
            timingHost.querySelector("input[name=nwe-execution]:checked")?.focus();
        });
        return h("label", { class: `nwe-rcard${on ? " on" : ""}` }, [
            input,
            h("span", { class: "rd", "aria-hidden": "true" }),
            h("span", {}, [choice.label, h("small", { text: choice.help })]),
        ]);
    }));
    const extra = [];
    if (executionValue === "scheduled") {
        extra.push(h("div", { class: "nwe-f nwe-when-row" }, [
            h("label", { class: "nwe-f-label", for: "nwe-when" }, ["Date and time", h("span", { class: "nwe-req", text: "*" })]),
            when,
            h("div", { class: "nwe-f-help", text: tz ? `In your time zone, ${tz}.` : "In your local time." }),
        ]));
    }
    if (executionValue === "servicenow") {
        extra.push(h("div", { class: "nwe-f-help nwe-when-row", text: "Nautobot checks the change in ServiceNow when you submit, then schedules the run for the start of its window." }));
    }
    timingHost.replaceChildren(cards, ...extra, timingError);
}

if ((config.errors || {}).scheduled_datetime || (config.errors || {}).execution_type) {
    timingError.replaceChildren(icon("alert-circle-outline"), h("span", { text: [...(config.errors.scheduled_datetime || []), ...(config.errors.execution_type || [])].join(" ") }));
    timingError.hidden = false;
}
paintTiming();

/* ---------- submit ---------- */
form.addEventListener("input", () => {
    if (!startedAt) startedAt = Date.now();
});

form.addEventListener("submit", (event) => {
    const bad = [];
    specs.forEach((spec) => {
        const message = validate(spec);
        fields[spec.key].setError(message);
        if (message) bad.push(spec);
    });
    let timingBad = false;
    if (executionValue === "scheduled") {
        const chosen = when.value ? new Date(when.value) : null;
        const message = !chosen ? "Choose a date and time." : chosen.getTime() < Date.now() + 60000 ? "Choose a time at least a minute from now." : "";
        timingError.replaceChildren(...(message ? [icon("alert-circle-outline"), h("span", { text: message })] : []));
        timingError.hidden = !message;
        timingBad = Boolean(message);
    }
    if (bad.length || timingBad) {
        event.preventDefault();
        const names = bad.map((spec) => spec.label);
        if (timingBad) names.push("When to run");
        showToast(`Fix ${plural(names.length, "field")} before running: ${names.join(", ")}.`);
        const first = bad[0] ? fields[bad[0].key] : null;
        if (first) {
            first.el.scrollIntoView({ block: "center", behavior: "smooth" });
            window.setTimeout(() => first.focus(), 250);
        } else when.focus();
        return;
    }
    serialize();
    const set = (name, value) => valuesHost.append(h("input", { type: "hidden", name, value }));
    set("execution_type", executionValue);
    if (executionValue === "scheduled") set("scheduled_datetime", when.value);
    set("browser_timezone_offset_minutes", String(new Date().getTimezoneOffset()));
    set("browser_timezone_name", tz);
    set("form_duration_seconds", String(startedAt ? Math.max(0, Math.round((Date.now() - startedAt) / 1000)) : 0));
    const submit = form.querySelector("[type=submit]");
    if (submit) {
        submit.disabled = true;
        submit.replaceChildren(icon("loading", "nwe-spin"), "Starting…");
    }
});

if (Object.keys(config.errors || {}).length) {
    const first = specs.find((spec) => (config.errors || {})[spec.key]);
    if (first) window.setTimeout(() => fields[first.key].el.scrollIntoView({ block: "center" }), 50);
}
