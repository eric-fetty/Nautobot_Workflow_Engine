/** Tiny DOM helpers shared by the builder and the launch form. */

export function h(tag, attrs, kids) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
        if (value == null || value === false) return;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = String(value);
        else if (key === "html") throw new Error("h(): raw HTML is not supported");
        else if (key === "checked") node.checked = Boolean(value);
        else if (key === "value") node.value = value == null ? "" : String(value);
        else if (key === "disabled") node.disabled = Boolean(value);
        else if (key === "multiple") node.multiple = Boolean(value);
        else if (key === "hidden") node.hidden = Boolean(value);
        else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
        else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
        else if (key === "selected") {
            if (value) node.setAttribute("selected", "");
        } else node.setAttribute(key, value === true ? "" : String(value));
    });
    append(node, kids);
    return node;
}

function append(node, kids) {
    (kids || []).forEach((kid) => {
        if (kid == null || kid === false) return;
        if (Array.isArray(kid)) append(node, kid);
        else node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    });
}

/** A Material Design icon, as Nautobot ships them. */
export function icon(name, extra) {
    return h("i", { class: `mdi mdi-${name}${extra ? ` ${extra}` : ""}`, "aria-hidden": "true" });
}

export function plural(count, one, many) {
    return `${count} ${count === 1 ? one : many || `${one}s`}`;
}

/** Close a popover when the user clicks elsewhere or presses Escape. */
export function dismissOnOutside(root, close) {
    const onDown = (event) => {
        if (!root.contains(event.target)) close();
    };
    const onKey = (event) => {
        if (event.key === "Escape") close();
    };
    window.setTimeout(() => {
        document.addEventListener("mousedown", onDown, true);
        document.addEventListener("keydown", onKey, true);
    });
    return () => {
        document.removeEventListener("mousedown", onDown, true);
        document.removeEventListener("keydown", onKey, true);
    };
}
