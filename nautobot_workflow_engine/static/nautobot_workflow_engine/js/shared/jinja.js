/**
 * The one Jinja editor: highlighting, `{{` autocomplete, typo checks and a live
 * preview rendered by the server sandbox. Used for step parameters, step
 * conditions, field visibility and advanced filters.
 */

import { dismissOnOutside, h, icon } from "./dom.js";
import { checkTemplate, completionAt, matchReferences, tokenize } from "./template.js";

/**
 * options:
 *   value, placeholder, rows, label (aria), id
 *   references(): [{ path: "inputs.device.name", label: "Device › name", detail: "Nautobot object", group: "Inputs" }]
 *   known(): { inputs: [...keys], results: [...step keys] }   for typo checks
 *   render(template): Promise<{ rendered, error, note }>       live preview; omit to hide it
 *   onChange(value)
 */
export function jinjaEditor(options) {
    const opts = options || {};
    const hl = h("pre", { class: "nwe-jinja-hl", "aria-hidden": "true" });
    const ta = h("textarea", {
        class: "nwe-jinja-ta",
        id: opts.id || null,
        rows: String(opts.rows || 1),
        spellcheck: "false",
        autocomplete: "off",
        placeholder: opts.placeholder || "",
        "aria-label": opts.label || "Jinja template",
        "aria-autocomplete": "list",
    });
    ta.value = opts.value == null ? "" : String(opts.value);
    const stat = h("div", { class: "nwe-jinja-stat", "aria-live": "polite" });
    const preview = opts.render ? h("div", { class: "nwe-jinja-preview" }) : null;
    const box = h("div", { class: "nwe-jinja-box" }, [hl, ta]);
    const root = h("div", { class: "nwe-jinja" }, [box, stat, preview]);
    let pop = null;
    let undismiss = null;
    let items = [];
    let active = 0;
    let renderTimer = 0;
    let renderSeq = 0;

    function problems() {
        const found = checkTemplate(ta.value, opts.known ? opts.known() : null);
        // Don't shout about the tag someone is still typing; it gets a quiet hint instead.
        if (document.activeElement !== ta) return found;
        const caret = ta.selectionStart;
        return found.filter((issue) => !(issue.message.includes("never closed") && caret >= issue.start && caret === ta.value.length));
    }

    function typing() {
        const spot = document.activeElement === ta ? completionAt(ta.value, ta.selectionStart) : null;
        return spot && ta.selectionStart === ta.value.length && !/\}\}|%\}/.test(ta.value.slice(spot.tagStart));
    }

    function paint() {
        const text = ta.value;
        const issues = problems();
        hl.replaceChildren();
        tokenize(text).forEach((part) => {
            if (part.kind === "text") {
                hl.append(marked(part.text, part.start, issues, ""));
                return;
            }
            const open = part.text.slice(0, 2);
            const close = part.closed ? part.text.slice(-2) : "";
            const body = part.text.slice(2, part.text.length - close.length);
            const cls = part.kind === "expr" ? "tk-expr" : part.kind === "stmt" ? "tk-stmt" : "tk-comment";
            const span = h("span", { class: cls }, [
                h("span", { class: "tk-delim", text: open }),
                marked(body, part.start + 2, issues, ""),
                close ? h("span", { class: "tk-delim", text: close }) : null,
            ]);
            hl.append(span);
        });
        hl.append(document.createTextNode("\n"));
        hl.scrollTop = ta.scrollTop;
        root.classList.toggle("bad", issues.length > 0);
        if (issues.length) {
            const first = issues[0];
            stat.className = "nwe-jinja-stat bad";
            stat.replaceChildren(icon("alert-circle-outline"), h("span", {}, [
                first.message,
                first.fix ? " " : null,
                first.fix ? h("button", { type: "button", class: "nwe-link", text: "Fix it", onclick: () => applyFix(first.fix) }) : null,
                issues.length > 1 ? ` (+${issues.length - 1} more)` : null,
            ]));
        } else if (typing()) {
            const closing = ta.value.slice(completionAt(ta.value, ta.selectionStart).tagStart, completionAt(ta.value, ta.selectionStart).tagStart + 2) === "{%" ? "%}" : "}}";
            stat.className = "nwe-jinja-stat";
            stat.replaceChildren(icon("keyboard-outline"), h("span", {}, ["Pick a name, then close it with ", h("span", { class: "kbd", text: closing }), "."]));
        } else {
            stat.className = "nwe-jinja-stat";
            stat.replaceChildren(icon("code-braces"), h("span", {}, ["Type ", h("span", { class: "kbd", text: "{{" }), " to insert an input or an earlier step’s result."]));
        }
        return issues;
    }

    function marked(text, offset, issues) {
        const frag = document.createDocumentFragment();
        let cursor = 0;
        const spans = issues
            .filter((issue) => issue.end > offset && issue.start < offset + text.length)
            .sort((a, b) => a.start - b.start);
        spans.forEach((issue) => {
            const from = Math.max(issue.start - offset, cursor);
            const to = Math.min(issue.end - offset, text.length);
            if (to <= from) return;
            if (from > cursor) frag.append(document.createTextNode(text.slice(cursor, from)));
            frag.append(h("span", { class: "tk-err", text: text.slice(from, to) }));
            cursor = to;
        });
        if (cursor < text.length) frag.append(document.createTextNode(text.slice(cursor)));
        return frag;
    }

    function applyFix(fix) {
        ta.value = ta.value.replace(fix.from, fix.to);
        changed();
        ta.focus();
    }

    function fit() {
        ta.style.height = "auto";
        ta.style.height = `${Math.max(ta.scrollHeight, 34)}px`;
    }

    function changed() {
        fit();
        const issues = paint();
        if (opts.onChange) opts.onChange(ta.value);
        schedulePreview(issues);
    }

    function schedulePreview(issues) {
        if (!preview) return;
        window.clearTimeout(renderTimer);
        const text = ta.value;
        if (!text.trim()) {
            preview.replaceChildren(h("span", { class: "lbl", text: "Preview" }), h("span", { class: "hint", text: "Nothing yet." }));
            return;
        }
        if ((issues || problems()).length) {
            preview.replaceChildren(h("span", { class: "lbl", text: "Preview" }), h("span", { class: "hint", text: "Fix the template to see it." }));
            return;
        }
        preview.replaceChildren(h("span", { class: "lbl", text: "Preview" }), h("span", { class: "hint" }, [icon("loading", "nwe-spin"), " Rendering…"]));
        const seq = ++renderSeq;
        renderTimer = window.setTimeout(async () => {
            let result;
            try {
                result = await opts.render(text);
            } catch (error) {
                result = { error: "The preview couldn’t be rendered." };
            }
            if (seq !== renderSeq) return;
            if (result && result.error) {
                preview.replaceChildren(h("span", { class: "lbl", text: "Preview" }), h("span", { class: "text-danger", text: result.error }));
                return;
            }
            const rendered = result ? result.rendered : "";
            preview.replaceChildren(
                h("span", { class: "lbl", text: "Preview" }),
                rendered === "" ? h("span", { class: "hint", text: "(renders as empty)" }) : h("code", { text: rendered }),
                result && result.note ? h("span", { class: "hint", text: `· ${result.note}` }) : null,
            );
        }, 350);
    }

    function closePop() {
        if (undismiss) undismiss();
        undismiss = null;
        if (pop) pop.remove();
        pop = null;
        ta.removeAttribute("aria-expanded");
    }

    function openPop() {
        const spot = completionAt(ta.value, ta.selectionStart);
        if (!spot || ta.selectionStart !== ta.selectionEnd) {
            closePop();
            return;
        }
        items = matchReferences(opts.references ? opts.references() : [], spot.query).slice(0, 40);
        if (!items.length) {
            closePop();
            return;
        }
        active = Math.min(active, items.length - 1);
        if (!pop) {
            pop = h("div", { class: "nwe-pop nwe-ac", role: "listbox" });
            root.append(pop);
            ta.setAttribute("aria-expanded", "true");
            undismiss = dismissOnOutside(root, closePop);
        }
        const kids = [];
        let group = "";
        items.forEach((item, index) => {
            if (item.group && item.group !== group) {
                group = item.group;
                kids.push(h("div", { class: "nwe-pop-sec", text: group }));
            }
            kids.push(h("button", {
                type: "button",
                class: `nwe-pop-item${index === active ? " hl" : ""}`,
                role: "option",
                "aria-selected": index === active ? "true" : "false",
                onmousedown: (event) => {
                    event.preventDefault();
                    choose(index);
                },
            }, [
                h("code", { text: item.path }),
                h("small", { class: "nwe-muted ms-auto", text: item.detail || item.label || "" }),
            ]));
        });
        kids.push(h("div", { class: "nwe-ac-foot" }, [
            h("span", {}, [h("span", { class: "nwe-kbd", text: "↑↓" }), " choose"]),
            h("span", {}, [h("span", { class: "nwe-kbd", text: "Enter" }), " insert"]),
            h("span", {}, [h("span", { class: "nwe-kbd", text: "Esc" }), " close"]),
        ]));
        pop.replaceChildren(...kids);
        const on = pop.querySelector(".hl");
        if (on && on.scrollIntoView) on.scrollIntoView({ block: "nearest" });
    }

    function choose(index) {
        const item = items[index];
        const spot = completionAt(ta.value, ta.selectionStart);
        if (!item || !spot) return;
        const before = ta.value.slice(0, spot.tagStart);
        const after = ta.value.slice(ta.selectionStart);
        const isStmt = ta.value.slice(spot.tagStart, spot.tagStart + 2) === "{%";
        const head = ta.value.slice(spot.tagStart, spot.start);
        const lead = head.length > 2 ? head : `${head} `;
        const closing = isStmt ? "%}" : "}}";
        const hasClose = new RegExp(`^\\s*${closing === "}}" ? "\\}\\}" : "%\\}"}`).test(after);
        const insert = `${lead}${item.path}${hasClose ? "" : ` ${closing}`}`;
        ta.value = before + insert + after;
        const caret = (before + insert).length + (hasClose ? after.match(/^\s*(\}\}|%\})/)[0].length : 0);
        ta.setSelectionRange(caret, caret);
        closePop();
        changed();
        ta.focus();
    }

    ta.addEventListener("input", () => {
        active = 0;
        changed();
        openPop();
    });
    ta.addEventListener("click", openPop);
    ta.addEventListener("scroll", () => {
        hl.scrollTop = ta.scrollTop;
    });
    ta.addEventListener("keydown", (event) => {
        if (!pop) {
            if (event.key === " " && event.ctrlKey) {
                event.preventDefault();
                openPop();
            }
            return;
        }
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            active = (active + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
            openPop();
        } else if (event.key === "Enter" || event.key === "Tab") {
            event.preventDefault();
            choose(active);
        } else if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            closePop();
        }
    });
    ta.addEventListener("blur", () => window.setTimeout(() => {
        if (!root.contains(document.activeElement)) closePop();
        schedulePreview(paint());
    }, 120));

    const issues = paint();
    schedulePreview(issues);
    window.requestAnimationFrame(fit);

    return {
        el: root,
        textarea: ta,
        focus() {
            ta.focus();
        },
        get value() {
            return ta.value;
        },
        set value(next) {
            ta.value = next == null ? "" : String(next);
            fit();
            schedulePreview(paint());
        },
        refresh() {
            schedulePreview(paint());
        },
    };
}
