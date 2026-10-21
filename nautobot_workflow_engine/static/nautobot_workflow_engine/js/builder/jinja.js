/**One Jinja editor: highlight, autocomplete, examples, and a place for the server error. */

import { h } from "./dom.js";

const EXAMPLES = [
    "{{ inputs.reason }}",
    "{{ inputs.device.name }}",
    "{{ results.reboot.status }}",
    "{{ user.username }}",
];

export function jinjaEditor({ id, value, pointer, bind, getReferences, readOnly }) {
    const highlight = h("pre", { class: "nwe-jinja-highlight", "aria-hidden": "true" });
    const area = h("textarea", {
        class: "form-control",
        id,
        rows: "3",
        spellcheck: "false",
        "data-bind": bind || "",
        "data-pointer": pointer || "",
        "data-jinja": "1",
    });
    area.value = value == null ? "" : String(value);
    if (readOnly) {
        area.readOnly = true;
    }
    const suggestions = h("div", { class: "list-group nwe-suggest shadow d-none", role: "listbox" });
    const syntax = h("div", { class: "invalid-feedback d-none", id: id ? `${id}-syntax` : undefined });
    const server = h("div", { class: "invalid-feedback d-none", id: id ? `${id}-server` : undefined });
    const example = h(
        "select",
        { class: "form-select form-select-sm mt-1", "aria-label": "Insert an example" },
        h("option", { value: "", text: "Insert an example…" }),
        ...EXAMPLES.map((item) => h("option", { value: item, text: item }))
    );
    example.addEventListener("change", () => {
        if (!example.value) {
            return;
        }
        insertAtCursor(area, example.value);
        example.value = "";
        paint(highlight, area.value);
        area.dispatchEvent(new Event("input", { bubbles: true }));
    });
    area.addEventListener("input", () => {
        paint(highlight, area.value);
        syntax.textContent = syntaxMessage(area.value);
        syntax.classList.toggle("d-none", !syntax.textContent);
        syntax.classList.toggle("d-block", Boolean(syntax.textContent));
        openSuggestions(area, suggestions, getReferences);
    });
    area.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            suggestions.classList.add("d-none");
        }
    });
    area.addEventListener("blur", () => {
        window.setTimeout(() => suggestions.classList.add("d-none"), 150);
    });
    paint(highlight, area.value);
    const box = h("div", { class: "nwe-jinja", "data-pointer": pointer || "" }, highlight, area, suggestions, syntax, server, example);
    box._textarea = area;
    box._server = server;
    return box;
}

export function setJinjaServerError(box, message) {
    if (!box || !box._server) {
        return;
    }
    box._server.textContent = message || "";
    box._server.classList.toggle("d-none", !message);
    box._server.classList.toggle("d-block", Boolean(message));
    if (box._textarea) {
        box._textarea.classList.toggle("is-invalid", Boolean(message));
    }
}

function paint(highlight, value) {
    highlight.replaceChildren();
    const text = String(value || "");
    const pattern = /\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\}/g;
    let cursor = 0;
    let match = pattern.exec(text);
    while (match) {
        highlight.append(document.createTextNode(text.slice(cursor, match.index)));
        highlight.append(h("span", { class: "nwe-token", text: match[0] }));
        cursor = match.index + match[0].length;
        match = pattern.exec(text);
    }
    highlight.append(document.createTextNode(text.slice(cursor)));
    if (!text.endsWith("\n")) {
        highlight.append(document.createTextNode("\n"));
    }
}

function syntaxMessage(value) {
    const text = String(value || "");
    const open = (text.match(/\{\{/g) || []).length;
    const close = (text.match(/\}\}/g) || []).length;
    if (open !== close) {
        return "This template has an unclosed {{.";
    }
    return "";
}

function openSuggestions(area, suggestions, getReferences) {
    const token = tokenAt(area.value, area.selectionStart);
    suggestions.replaceChildren();
    if (!token || token.query.length < 1) {
        suggestions.classList.add("d-none");
        return;
    }
    const items = (getReferences ? getReferences() : []).filter((item) => item.insert.includes(token.query) || item.label.toLowerCase().includes(token.query.toLowerCase()));
    if (!items.length) {
        suggestions.classList.add("d-none");
        return;
    }
    items.slice(0, 8).forEach((item) => {
        const button = h("button", { type: "button", class: "list-group-item list-group-item-action py-1", text: item.label });
        button.addEventListener("mousedown", (event) => {
            event.preventDefault();
            const next = area.value.slice(0, token.start) + item.insert + area.value.slice(area.selectionStart);
            area.value = next;
            paint(area.parentElement.querySelector(".nwe-jinja-highlight"), next);
            suggestions.classList.add("d-none");
            area.dispatchEvent(new Event("input", { bubbles: true }));
            area.focus();
        });
        suggestions.append(button);
    });
    suggestions.classList.remove("d-none");
}

function tokenAt(value, offset) {
    const until = String(value || "").slice(0, offset);
    const start = Math.max(until.lastIndexOf("{{"), until.lastIndexOf("{%"));
    if (start < 0) {
        return null;
    }
    const query = until.slice(start).replace(/^\{\{|\{%/, "").trim();
    if (query.includes("}}") || query.includes("%}")) {
        return null;
    }
    return { start, query };
}

function insertAtCursor(area, text) {
    const start = area.selectionStart || area.value.length;
    const end = area.selectionEnd || start;
    area.value = area.value.slice(0, start) + text + area.value.slice(end);
}

export function referenceList(payload, phase, index) {
    const items = [];
    (payload.inputs || []).forEach((input) => {
        if (!input.key) {
            return;
        }
        items.push({ label: `inputs.${input.key}`, insert: `{{ inputs.${input.key} }}` });
        if (input.type === "object" || input.type === "multiobject") {
            items.push({ label: `inputs.${input.key}.name`, insert: `{{ inputs.${input.key}.name }}` });
            items.push({ label: `inputs.${input.key}.id`, insert: `{{ inputs.${input.key}.id }}` });
        }
    });
    const order = ["pre", "main", "post"];
    const stop = order.indexOf(phase);
    order.forEach((name, phaseIndex) => {
        (payload.actions[name] || []).forEach((action, actionIndex) => {
            const earlier = phaseIndex < stop || (phaseIndex === stop && actionIndex < index);
            if (!earlier || !action.name) {
                return;
            }
            items.push({ label: `results.${action.name}`, insert: `{{ results.${action.name} }}` });
            items.push({ label: `results.${action.name}.status`, insert: `{{ results.${action.name}.status }}` });
        });
    });
    ["username", "email", "full_name"].forEach((attr) => {
        items.push({ label: `user.${attr}`, insert: `{{ user.${attr} }}` });
    });
    return items;
}
