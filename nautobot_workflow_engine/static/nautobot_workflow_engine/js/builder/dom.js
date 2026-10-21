/**Small DOM helpers. Text is assigned with textContent, never innerHTML. */

export function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
        if (value == null || value === false) {
            return;
        }
        if (key === "class") {
            node.className = value;
        } else if (key === "text") {
            node.textContent = value;
        } else if (key === "checked") {
            node.checked = Boolean(value);
        } else if (key === "value") {
            node.value = value == null ? "" : String(value);
        } else if (key.startsWith("on") && typeof value === "function") {
            node.addEventListener(key.slice(2).toLowerCase(), value);
        } else {
            node.setAttribute(key, value === true ? "" : String(value));
        }
    });
    children.flat().forEach((child) => {
        if (child == null || child === false) {
            return;
        }
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    });
    return node;
}

export function field(label, help, control, { id, error, required } = {}) {
    const inputId = id || control.id;
    if (inputId && control.tagName !== "DIV") {
        control.id = inputId;
    }
    const labelNode = h("label", { class: "form-label", for: inputId || undefined }, label || "");
    if (required) {
        labelNode.append(document.createTextNode(" *"));
    }
    if (help) {
        labelNode.title = help;
        labelNode.append(
            h("span", { class: "text-muted fw-normal", text: " ⓘ", title: help })
        );
    }
    const described = [];
    const nodes = [labelNode, control];
    if (help) {
        const helpId = inputId ? `${inputId}-help` : "";
        const helpNode = h("div", { class: "form-text", id: helpId || undefined, text: help });
        nodes.push(helpNode);
        if (helpId) {
            described.push(helpId);
        }
    }
    const errorId = inputId ? `${inputId}-error` : "";
    if (error) {
        control.classList.add("is-invalid");
    }
    nodes.push(
        h("div", {
            class: error ? "invalid-feedback d-block" : "invalid-feedback",
            id: errorId || undefined,
            "data-error-for": inputId || "",
            text: error || "",
        })
    );
    if (error && errorId) {
        described.push(errorId);
    }
    if (described.length && control.setAttribute) {
        control.setAttribute("aria-describedby", described.join(" "));
    }
    return h("div", { class: "mb-3" }, nodes);
}
