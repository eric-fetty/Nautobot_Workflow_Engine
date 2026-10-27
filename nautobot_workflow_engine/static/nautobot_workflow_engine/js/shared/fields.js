/**
 * Launch-form controls. The launch page and the builder preview both use these,
 * so what an editor previews is what people get.
 *
 * createField(spec, ctx) returns {el, get(), set(value), setError(message), reload()}.
 *   spec: one input, as launch_ui.field_spec() describes it.
 *   ctx.onChange(key, value): called after every change.
 *   ctx.loadOptions(spec, {search, offset}): resolves to {options, total, hasMore, error}.
 */

import { csvTemplate, mapCsv, parseCsv, spreadsheetToCsv } from "./csv.js";
import { dismissOnOutside, h, icon, plural } from "./dom.js";
import { lines, rangeSentence, rowErrors } from "./validate.js";

let uid = 0;
const nextId = (key) => `nwe-f-${key || "field"}-${(uid += 1)}`;

export const RADIO_LIMIT = 4;
export const CHIP_LIMIT = 12;

export function createField(spec, ctx) {
    const id = nextId(spec.key);
    const control = makeControl(spec, ctx, id);
    const error = h("div", { class: "nwe-f-error", hidden: true });
    const label = spec.type === "boolean"
        ? null
        : h("label", { class: "nwe-f-label", for: control.focusId || id, id: `${id}-label` }, [spec.label || spec.key, spec.required ? h("span", { class: "nwe-req", "aria-hidden": "true", text: "*" }) : null]);
    const help = spec.help && !control.ownsHelp ? h("div", { class: "nwe-f-help", id: `${id}-help`, text: spec.help }) : null;
    const el = h("div", { class: "nwe-f", "data-field": spec.key, "data-type": spec.type }, [label, control.el, control.hint || null, error, help]);
    return {
        el,
        key: spec.key,
        spec,
        get: control.get,
        set: control.set,
        reload: control.reload || (() => Promise.resolve()),
        labelFor: control.labelFor || ((raw) => raw),
        optionFor: control.optionFor || (() => null),
        focus: () => (control.focusEl ? control.focusEl() : el.querySelector("input, select, textarea, [tabindex]"))?.focus(),
        setError(message) {
            const text = Array.isArray(message) ? message.filter(Boolean).join(" ") : message || "";
            error.replaceChildren(...(text ? [icon("alert-circle-outline"), h("span", { text })] : []));
            error.hidden = !text;
            el.classList.toggle("has-error", Boolean(text));
            if (control.invalid) control.invalid(Boolean(text));
        },
    };
}

function makeControl(spec, ctx, id) {
    const type = spec.type;
    if (type === "boolean") return toggleControl(spec, ctx, id);
    if (type === "integer") return textControl(spec, ctx, id, true);
    if (type === "choice") {
        const fixed = !spec.dynamic && (spec.choices || []).length && spec.choices.length <= RADIO_LIMIT;
        return fixed ? radioControl(spec, ctx, id) : pickerControl(spec, ctx, id, false);
    }
    if (type === "multichoice") {
        const fixed = !spec.dynamic && (spec.choices || []).length && spec.choices.length <= CHIP_LIMIT;
        return fixed ? chipControl(spec, ctx, id) : pickerControl(spec, ctx, id, true);
    }
    if (type === "object") return pickerControl(spec, ctx, id, false);
    if (type === "multiobject") return pickerControl(spec, ctx, id, true);
    if (type === "list") return listControl(spec, ctx, id);
    if (type === "listdict") return tableControl(spec, ctx, id);
    return textControl(spec, ctx, id, false);
}

function changed(spec, ctx, value) {
    if (ctx && typeof ctx.onChange === "function") ctx.onChange(spec.key, value);
}

/* ---------- text and whole numbers ---------- */
function textControl(spec, ctx, id, numeric) {
    const input = h("input", {
        id,
        class: `form-control${numeric ? " nwe-number" : ""}`,
        type: numeric ? "number" : "text",
        inputmode: numeric ? "numeric" : null,
        step: numeric ? "1" : null,
        min: numeric && spec.min_value != null ? spec.min_value : null,
        max: numeric && spec.max_value != null ? spec.max_value : null,
        placeholder: spec.placeholder || "",
        value: ctx.value ?? "",
        "aria-required": spec.required ? "true" : null,
        disabled: ctx.disabled,
    });
    input.addEventListener("input", () => changed(spec, ctx, input.value));
    return {
        el: input,
        get: () => input.value,
        set: (value) => {
            input.value = value ?? "";
        },
        invalid: (bad) => input.classList.toggle("is-invalid", bad),
    };
}

/* ---------- yes / no ---------- */
function toggleControl(spec, ctx, id) {
    const input = h("input", { id, type: "checkbox", role: "switch", checked: ctx.value === true || ctx.value === "true", disabled: ctx.disabled });
    input.addEventListener("change", () => changed(spec, ctx, input.checked));
    const el = h("label", { class: "nwe-switch", for: id }, [
        input,
        h("span", { class: "nwe-switch-ui", "aria-hidden": "true" }),
        h("span", { class: "nwe-switch-copy" }, [
            h("b", {}, [spec.label || spec.key, spec.required ? h("span", { class: "nwe-req", text: "*" }) : null]),
            spec.help ? h("small", { text: spec.help }) : null,
        ]),
    ]);
    return {
        el,
        ownsHelp: true,
        get: () => input.checked,
        set: (value) => {
            input.checked = value === true || value === "true";
        },
    };
}

/* ---------- pick one, as radio cards ---------- */
function radioControl(spec, ctx, id) {
    let value = ctx.value == null ? "" : String(ctx.value);
    const group = h("div", { class: "nwe-rcards", role: "radiogroup", id, "aria-required": spec.required ? "true" : null });
    const paint = () => {
        group.replaceChildren(...spec.choices.map((choice) => {
            const on = String(choice.value) === value;
            const input = h("input", { type: "radio", name: id, value: choice.value, checked: on, disabled: ctx.disabled });
            input.addEventListener("change", () => {
                value = String(choice.value);
                paint();
                group.querySelector("input:checked")?.focus();
                changed(spec, ctx, value);
            });
            return h("label", { class: `nwe-rcard${on ? " on" : ""}` }, [
                input,
                h("span", { class: "rd", "aria-hidden": "true" }),
                h("span", {}, [choice.label || choice.value, choice.help ? h("small", { text: choice.help }) : null]),
            ]);
        }));
    };
    paint();
    return {
        el: group,
        focusId: null,
        get: () => value,
        set: (next) => {
            value = next == null ? "" : String(next);
            paint();
        },
        labelFor: (raw) => (spec.choices.find((choice) => String(choice.value) === String(raw)) || {}).label || raw,
        focusEl: () => group.querySelector("input:checked") || group.querySelector("input"),
    };
}

/* ---------- pick many, as checkbox chips ---------- */
function chipControl(spec, ctx, id) {
    let value = Array.isArray(ctx.value) ? ctx.value.map(String) : [];
    const group = h("div", { class: "nwe-checks", role: "group", id });
    const paint = () => {
        group.replaceChildren(...spec.choices.map((choice) => {
            const on = value.includes(String(choice.value));
            const input = h("input", { type: "checkbox", value: choice.value, checked: on, disabled: ctx.disabled });
            input.addEventListener("change", () => {
                const key = String(choice.value);
                value = input.checked ? [...value, key] : value.filter((item) => item !== key);
                value = spec.choices.map((item) => String(item.value)).filter((item) => value.includes(item));
                paint();
                group.querySelector(`input[value="${CSS.escape(key)}"]`)?.focus();
                changed(spec, ctx, value);
            });
            return h("label", { class: `nwe-ck${on ? " on" : ""}` }, [
                input,
                h("span", { class: "bx", "aria-hidden": "true" }, [on ? icon("check-bold") : null]),
                h("span", { text: choice.label || choice.value }),
            ]);
        }));
    };
    paint();
    return {
        el: group,
        get: () => value.slice(),
        set: (next) => {
            value = Array.isArray(next) ? next.map(String) : [];
            paint();
        },
        focusEl: () => group.querySelector("input"),
    };
}

/* ---------- searchable picker: objects, long choice lists ---------- */
function normalizeOption(option) {
    if (option == null) return null;
    if (typeof option !== "object") return { value: String(option), label: String(option), meta: "" };
    return { value: String(option.value ?? option.id ?? ""), label: String(option.label ?? option.name ?? option.value ?? ""), meta: option.meta || "" };
}

function pickerControl(spec, ctx, id, multi) {
    const isObject = spec.type === "object" || spec.type === "multiobject";
    const nouns = spec.nouns || (isObject ? "objects" : "options");
    const modelIcon = spec.icon || (isObject ? "cube-outline" : "format-list-bulleted");
    const staticOptions = !spec.dynamic && !isObject ? (spec.choices || []).map(normalizeOption) : null;
    const state = {
        options: staticOptions || [],
        total: staticOptions ? staticOptions.length : null,
        hasMore: false,
        loaded: Boolean(staticOptions),
        loading: false,
        error: "",
        query: "",
        results: null,
        open: false,
        hl: 0,
        selected: [],
    };
    const initial = multi ? (Array.isArray(ctx.value) ? ctx.value : []) : ctx.value == null || ctx.value === "" ? [] : [ctx.value];
    state.selected = initial.map(normalizeOption).filter((option) => option && option.value !== "");
    const known = new Map();
    (ctx.initialLabels || []).forEach((option) => {
        const item = normalizeOption(option);
        if (item) known.set(item.value, item);
    });
    state.selected = state.selected.map((option) => known.get(option.value) || option);

    const combo = h("div", {
        class: "nwe-combo",
        id,
        role: "combobox",
        tabindex: ctx.disabled ? "-1" : "0",
        "aria-haspopup": "listbox",
        "aria-expanded": "false",
        "aria-required": spec.required ? "true" : null,
    });
    const hint = h("div", { class: "nwe-f-hint" });
    const wrap = h("div", { class: "nwe-picker" }, [combo]);
    let pop = null;
    let undismiss = null;
    let searchTimer = 0;
    let searchInput = null;

    const optionLabel = (option) => {
        const found = state.options.find((item) => item.value === option.value) || known.get(option.value);
        return found || option;
    };

    function paintCombo() {
        const kids = [];
        if (!multi) {
            const current = state.selected[0] ? optionLabel(state.selected[0]) : null;
            kids.push(isObject ? icon(modelIcon) : null);
            if (current) {
                kids.push(h("span", { class: "nwe-combo-copy" }, [
                    h("span", { class: "nwe-combo-title", text: current.label }),
                    current.meta ? h("span", { class: "nwe-combo-meta", text: current.meta }) : null,
                ]));
            } else {
                kids.push(h("span", { class: "nwe-combo-copy" }, [h("span", { class: "nwe-combo-ph", text: placeholder() })]));
            }
        } else {
            kids.push(isObject ? icon(modelIcon) : null);
            if (state.selected.length) {
                kids.push(h("span", { class: "nwe-combo-chips" }, state.selected.map((option) => {
                    const item = optionLabel(option);
                    const remove = h("button", { type: "button", "aria-label": `Remove ${item.label}`, tabindex: "-1" }, [icon("close")]);
                    remove.addEventListener("click", (event) => {
                        event.stopPropagation();
                        toggle(item);
                    });
                    return h("span", { class: "nwe-chip" }, [h("span", { text: item.label }), remove]);
                })));
            } else {
                kids.push(h("span", { class: "nwe-combo-copy" }, [h("span", { class: "nwe-combo-ph", text: placeholder() })]));
            }
        }
        kids.push(icon(state.loading && !state.open ? "loading nwe-spin" : "chevron-down", "caret"));
        combo.replaceChildren(...kids.filter(Boolean));
        combo.classList.toggle("open", state.open);
        combo.setAttribute("aria-expanded", state.open ? "true" : "false");
        paintHint();
    }

    function placeholder() {
        if (state.loading && !state.loaded) return "Loading…";
        if (state.error) return "Couldn't load the options";
        if (state.loaded && state.total === 0) return `No ${nouns} available`;
        return spec.placeholder || (multi ? `Choose ${nouns}…` : "Choose…");
    }

    function paintHint() {
        const bits = [];
        if (state.error) {
            hint.replaceChildren(icon("alert-circle-outline"), h("span", { text: state.error }));
            hint.hidden = false;
            return;
        }
        if (state.loaded && state.total != null && isObject) {
            if (multi && state.selected.length) bits.push(`${state.selected.length} of ${plural(state.total, spec.noun || "object", nouns)} selected`);
            else bits.push(`${plural(state.total, spec.noun || "object", nouns)} available`);
        }
        if (spec.help) bits.push(spec.help);
        hint.replaceChildren(...(bits.length ? [isObject && state.loaded ? icon("filter-variant") : null, h("span", { text: bits.join(" · ") })].filter(Boolean) : []));
        hint.hidden = !bits.length;
    }

    async function load(search) {
        if (staticOptions) return;
        state.loading = true;
        state.error = "";
        paintCombo();
        if (pop) paintList();
        let result;
        try {
            result = await ctx.loadOptions(spec, { search: search || "", offset: 0 });
        } catch (error) {
            result = { options: [], total: 0, error: "Couldn't load the options. Try again." };
        }
        state.loading = false;
        const options = (result.options || []).map(normalizeOption).filter((option) => option && option.value !== "__create__");
        options.forEach((option) => known.set(option.value, option));
        if (search) {
            state.results = options;
        } else {
            state.options = options;
            state.total = result.total ?? options.length;
            state.hasMore = Boolean(result.hasMore);
            state.loaded = !result.error;
            state.results = null;
        }
        state.error = result.error || "";
        paintCombo();
        if (pop) paintList();
    }

    function visible() {
        const query = state.query.trim().toLowerCase();
        if (state.results && query) return state.results;
        if (!query) return state.options;
        return state.options.filter((option) => `${option.label} ${option.meta}`.toLowerCase().includes(query));
    }

    function isOn(option) {
        return state.selected.some((item) => item.value === option.value);
    }

    function toggle(option) {
        if (multi) {
            state.selected = isOn(option) ? state.selected.filter((item) => item.value !== option.value) : [...state.selected, option];
        } else {
            state.selected = [option];
            close();
        }
        paintCombo();
        if (pop) paintList();
        changed(spec, ctx, api.get());
    }

    function paintList() {
        if (!pop) return;
        const list = pop.querySelector(".nwe-picker-list");
        const tools = pop.querySelector(".nwe-picker-tools");
        const rows = visible();
        if (state.hl >= rows.length) state.hl = Math.max(0, rows.length - 1);
        if (tools) {
            const everything = !state.hasMore && !state.query;
            const all = h("button", { type: "button", class: "nwe-link", disabled: !rows.length, text: everything ? `Select all ${rows.length}` : `Select all ${rows.length} shown` });
            all.addEventListener("click", () => {
                const add = rows.filter((option) => !isOn(option));
                state.selected = [...state.selected, ...add];
                paintCombo();
                paintList();
                changed(spec, ctx, api.get());
            });
            const clear = h("button", { type: "button", class: "nwe-link muted", disabled: !state.selected.length, text: "Clear" });
            clear.addEventListener("click", () => {
                state.selected = [];
                paintCombo();
                paintList();
                changed(spec, ctx, api.get());
            });
            tools.replaceChildren(all, clear, h("span", { class: "count", text: `${state.selected.length} selected` }));
        }
        if (state.loading) {
            list.replaceChildren(h("div", { class: "nwe-picker-empty" }, [icon("loading", "nwe-spin"), " Loading…"]));
            return;
        }
        if (state.error) {
            list.replaceChildren(h("div", { class: "nwe-picker-empty", text: state.error }));
            return;
        }
        if (!rows.length) {
            list.replaceChildren(h("div", { class: "nwe-picker-empty", text: state.query ? `No ${nouns} match “${state.query}”.` : `No ${nouns} available.` }));
            return;
        }
        list.replaceChildren(...rows.map((option, index) => {
            const on = isOn(option);
            const row = h("button", {
                type: "button",
                class: `nwe-opt${on ? " on" : ""}${index === state.hl ? " hl" : ""}`,
                role: "option",
                "aria-selected": on ? "true" : "false",
                tabindex: "-1",
            }, [
                multi ? h("span", { class: "bx", "aria-hidden": "true" }, [on ? icon("check-bold") : null]) : null,
                h("span", { class: "nwe-combo-copy" }, [
                    h("span", { class: "nwe-combo-title", text: option.label }),
                    option.meta ? h("span", { class: "nwe-combo-meta", text: option.meta }) : null,
                ]),
                !multi && on ? icon("check", "ck") : null,
            ]);
            row.addEventListener("mousedown", (event) => event.preventDefault());
            row.addEventListener("click", () => toggle(option));
            return row;
        }));
        const more = pop.querySelector(".nwe-picker-more");
        if (more) {
            const showing = state.query ? rows.length : state.options.length;
            more.hidden = !(state.hasMore && state.total > showing);
            more.textContent = `Showing ${showing} of ${state.total}. Type to search for the rest.`;
        }
        list.querySelector(".hl")?.scrollIntoView({ block: "nearest" });
    }

    function open() {
        if (state.open || ctx.disabled) return;
        state.open = true;
        state.query = "";
        state.results = null;
        state.hl = 0;
        searchInput = h("input", { type: "search", placeholder: `Search ${nouns}…`, "aria-label": `Search ${nouns}`, autocomplete: "off" });
        searchInput.addEventListener("input", () => {
            state.query = searchInput.value;
            state.hl = 0;
            clearTimeout(searchTimer);
            if (!staticOptions && state.hasMore && state.query.trim()) {
                searchTimer = window.setTimeout(() => load(state.query.trim()), 250);
            }
            paintList();
        });
        searchInput.addEventListener("keydown", onKeys);
        pop = h("div", { class: "nwe-pop nwe-picker-pop", role: "listbox", "aria-multiselectable": multi ? "true" : null }, [
            h("div", { class: "nwe-picker-search" }, [icon("magnify"), searchInput]),
            multi ? h("div", { class: "nwe-picker-tools" }) : null,
            h("div", { class: "nwe-picker-list" }),
            h("div", { class: "nwe-picker-more", hidden: true }),
        ]);
        wrap.append(pop);
        undismiss = dismissOnOutside(wrap, close);
        paintCombo();
        paintList();
        if (!state.loaded && !state.loading && !staticOptions) load("");
        searchInput.focus();
    }

    function close() {
        if (!state.open) return;
        state.open = false;
        if (undismiss) undismiss();
        undismiss = null;
        pop?.remove();
        pop = null;
        paintCombo();
        combo.focus();
    }

    function onKeys(event) {
        const rows = visible();
        if (event.key === "ArrowDown") {
            event.preventDefault();
            state.hl = Math.min(rows.length - 1, state.hl + 1);
            paintList();
        } else if (event.key === "ArrowUp") {
            event.preventDefault();
            state.hl = Math.max(0, state.hl - 1);
            paintList();
        } else if (event.key === "Enter") {
            event.preventDefault();
            if (rows[state.hl]) toggle(rows[state.hl]);
        } else if (event.key === "Escape") {
            event.preventDefault();
            close();
        } else if (event.key === "Tab") {
            close();
        }
    }

    combo.addEventListener("click", () => (state.open ? close() : open()));
    combo.addEventListener("keydown", (event) => {
        if (["Enter", " ", "ArrowDown"].includes(event.key)) {
            event.preventDefault();
            open();
        }
    });

    const api = {
        el: wrap,
        hint,
        ownsHelp: true,
        focusId: id,
        get() {
            if (multi) return state.selected.map((option) => option.value);
            const current = state.selected[0];
            return current ? current.value : "";
        },
        set(value) {
            const list = multi ? (Array.isArray(value) ? value : []) : value == null || value === "" ? [] : [value];
            state.selected = list.map(normalizeOption).filter(Boolean).map((option) => known.get(option.value) || option);
            paintCombo();
        },
        reload() {
            state.loaded = false;
            state.options = staticOptions || [];
            return load("").then(() => {
                const allowed = new Set(state.options.map((option) => option.value));
                if (!state.hasMore && state.selected.some((option) => !allowed.has(option.value))) {
                    state.selected = state.selected.filter((option) => allowed.has(option.value));
                    paintCombo();
                    changed(spec, ctx, api.get());
                }
            });
        },
        labelFor: (raw) => (known.get(String(raw)) || {}).label || raw,
        optionFor: (raw) => known.get(String(raw)) || null,
        invalid: (bad) => combo.classList.toggle("is-invalid", bad),
        focusEl: () => combo,
    };
    paintCombo();
    return api;
}

/* ---------- list of text: one item per line ---------- */
function listControl(spec, ctx, id) {
    const start = Array.isArray(ctx.value) ? ctx.value.join("\n") : String(ctx.value || "");
    const area = h("textarea", {
        id,
        class: "form-control nwe-lines",
        rows: String(Math.max(3, Math.min(8, start.split("\n").length + 1))),
        placeholder: spec.placeholder || "One per line",
        spellcheck: "false",
        value: start,
        disabled: ctx.disabled,
    });
    const counter = h("div", { class: "nwe-counter" });
    const paint = () => {
        const count = lines(area.value).length;
        const bits = [plural(count, "item")];
        if (spec.max_items != null && spec.max_items !== "") bits.push(`up to ${spec.max_items}`);
        if (spec.min_items != null && spec.min_items !== "" && Number(spec.min_items) > 0) bits.push(`at least ${spec.min_items}`);
        bits.push("one per line");
        counter.textContent = bits.join(" · ");
        const over = spec.max_items != null && spec.max_items !== "" && count > Number(spec.max_items);
        counter.classList.toggle("bad", over);
    };
    area.addEventListener("input", () => {
        paint();
        changed(spec, ctx, lines(area.value));
    });
    paint();
    return {
        el: h("div", {}, [area, counter]),
        focusId: id,
        get: () => lines(area.value),
        set: (value) => {
            area.value = Array.isArray(value) ? value.join("\n") : String(value || "");
            paint();
        },
        invalid: (bad) => area.classList.toggle("is-invalid", bad),
        focusEl: () => area,
    };
}

/* ---------- table ---------- */
function columnHint(column) {
    const type = String(column.type || "string").toLowerCase();
    const bits = [{ integer: "Whole number", number: "Number", boolean: "Yes / No", choice: "Pick one" }[type] || "Text"];
    const range = rangeSentence(column.min_value, column.max_value);
    if (range && (type === "integer" || type === "number")) {
        const low = column.min_value;
        const high = column.max_value;
        bits.push(low != null && low !== "" && high != null && high !== "" ? `${low}–${high}` : range.toLowerCase());
    }
    if (column.unique === true) bits.push("unique");
    return bits.join(" · ");
}

function tableControl(spec, ctx, id) {
    const columns = (spec.columns || []).filter((column) => column && column.key);
    let rows = Array.isArray(ctx.value) ? ctx.value.map((row) => ({ ...row })) : [];
    let summary = null;
    let onlyBad = false;
    // Row problems show once someone leaves the row, imports a file or presses Run.
    let showAll = false;
    const visited = new WeakSet();
    const body = h("tbody");
    const sumHost = h("div");
    const fileInput = h("input", { type: "file", accept: ".csv,text/csv,.tsv,text/tab-separated-values,text/plain", hidden: true });
    const meta = h("span", { class: "meta" });
    const addBtn = h("button", { type: "button", class: "nwe-link", disabled: ctx.disabled }, [icon("plus"), "Add row"]);
    const onlyBadBtn = h("button", { type: "button", class: "nwe-link muted", hidden: true });
    const wrapEl = h("div", { class: "nwe-grid-wrap", id }, [
        sumHost,
        h("div", { class: "nwe-grid-scroll" }, [
            h("table", { class: "nwe-grid" }, [
                h("thead", {}, [h("tr", {}, [
                    h("th", { class: "rn", text: "#" }),
                    ...columns.map((column) => h("th", { class: String(column.type) === "boolean" ? "tb" : "" }, [
                        column.label || column.key,
                        column.required === true ? h("span", { class: "nwe-req", text: "*" }) : null,
                        h("span", { class: "ct" }, columnHint(column).split(" · ").flatMap((part, index) => [index ? " · " : null, h("span", { class: "ct-part", text: part })])),
                    ])),
                    h("th", { class: "rx" }, [h("span", { class: "nwe-sr", text: "Remove" })]),
                ])]),
                body,
            ]),
        ]),
        h("div", { class: "nwe-grid-foot" }, [
            addBtn,
            meta,
            onlyBadBtn,
            spec.csv_import === false ? null : h("span", { class: "end" }, [
                h("button", { type: "button", class: "btn btn-sm nwe-btn", "data-act": "template" }, [icon("download"), "Download template"]),
                h("button", { type: "button", class: "btn btn-sm nwe-btn", "data-act": "import", disabled: ctx.disabled }, [icon("file-delimited-outline"), "Import CSV"]),
                fileInput,
            ]),
        ]),
    ]);

    const emit = () => changed(spec, ctx, rows.map((row) => ({ ...row })));
    const blankRow = () => Object.fromEntries(columns.map((column) => [column.key, String(column.type) === "boolean" ? false : ""]));

    function cell(column, row, rowIndex, problem) {
        const type = String(column.type || "string").toLowerCase();
        const value = row[column.key];
        let input;
        if (type === "boolean") {
            input = h("input", { type: "checkbox", class: "nwe-cell-check", "data-cell": `${rowIndex}:${column.key}`, checked: value === true || value === "true", "aria-label": `${column.label || column.key}, row ${rowIndex + 1}`, disabled: ctx.disabled });
            input.addEventListener("change", () => {
                row[column.key] = input.checked;
                emit();
            });
            return h("td", { class: "tb" }, [input]);
        }
        if (type === "choice") {
            input = h("select", { class: `nwe-cell${problem ? " bad" : ""}`, "data-cell": `${rowIndex}:${column.key}`, "aria-label": `${column.label || column.key}, row ${rowIndex + 1}`, disabled: ctx.disabled }, [
                h("option", { value: "", text: "—" }),
                ...(column.choices || []).map((choice) => h("option", { value: choice.value, text: choice.label || choice.value, selected: String(choice.value) === String(value ?? "") })),
            ]);
            input.addEventListener("change", () => {
                row[column.key] = input.value;
                emit();
                repaintSoon();
            });
        } else {
            const numeric = type === "integer" || type === "number";
            input = h("input", {
                class: `nwe-cell${problem ? " bad" : ""}`,
                type: numeric ? "number" : "text",
                step: type === "integer" ? "1" : numeric ? "any" : null,
                value: value ?? "",
                "data-cell": `${rowIndex}:${column.key}`,
                "aria-label": `${column.label || column.key}, row ${rowIndex + 1}`,
                "aria-invalid": problem ? "true" : null,
                disabled: ctx.disabled,
            });
            input.addEventListener("input", () => {
                row[column.key] = input.value;
                emit();
            });
            input.addEventListener("change", repaintSoon);
            input.addEventListener("paste", (event) => paste(event, rowIndex, columns.indexOf(column)));
        }
        return h("td", {}, [input]);
    }

    function paste(event, rowIndex, columnIndex) {
        const text = event.clipboardData ? event.clipboardData.getData("text") : "";
        if (!/[\t\n]/.test(text.trim())) return;
        event.preventDefault();
        const grid = text.replace(/\r/g, "").replace(/\n+$/, "").split("\n").map((line) => line.split("\t"));
        grid.forEach((cells, offset) => {
            const at = rowIndex + offset;
            if (spec.max_rows != null && spec.max_rows !== "" && at >= Number(spec.max_rows)) return;
            while (rows.length <= at) rows.push(blankRow());
            cells.forEach((raw, step) => {
                const column = columns[columnIndex + step];
                if (!column) return;
                const type = String(column.type || "string").toLowerCase();
                rows[at][column.key] = type === "boolean" ? ["true", "1", "yes", "y", "on"].includes(raw.trim().toLowerCase()) : raw.trim();
            });
        });
        paintRows();
        emit();
    }

    // Repaint after focus has moved, so tabbing between cells keeps working.
    function repaintSoon() {
        window.setTimeout(paintRows, 0);
    }

    body.addEventListener("focusout", (event) => {
        const from = event.target.closest("tr");
        const to = event.relatedTarget && event.relatedTarget.closest ? event.relatedTarget.closest("tr") : null;
        if (!from || from === to) return;
        const index = Number(from.dataset.row);
        if (Number.isInteger(index) && rows[index]) {
            visited.add(rows[index]);
            repaintSoon();
        }
    });

    function paintRows() {
        const active = document.activeElement;
        const focusCell = active && body.contains(active) ? active.dataset.cell : null;
        const caret = focusCell && typeof active.selectionStart === "number" ? [active.selectionStart, active.selectionEnd] : null;
        const seen = {};
        const problems = rows.map((row, index) => {
            seen.__row = index + 1;
            const found = rowErrors(columns, row, seen);
            return showAll || visited.has(row) ? found : {};
        });
        const badCount = problems.filter((item) => Object.keys(item).length).length;
        if (!badCount) onlyBad = false;
        const out = [];
        if (!rows.length) {
            out.push(h("tr", {}, [h("td", { class: "tempty", colspan: String(columns.length + 2), text: "No rows yet. Add a row, paste from a spreadsheet, or import a CSV." })]));
        }
        rows.forEach((row, index) => {
            const problem = problems[index];
            const bad = Object.keys(problem).length > 0;
            if (onlyBad && !bad) return;
            const remove = h("button", { type: "button", class: "nwe-iconbtn", "data-cell": `${index}:__remove`, "aria-label": `Remove row ${index + 1}`, title: "Remove row", disabled: ctx.disabled }, [icon("close")]);
            remove.addEventListener("click", () => {
                rows.splice(index, 1);
                paintRows();
                emit();
            });
            out.push(h("tr", { class: bad ? "bad" : "", "data-row": String(index) }, [
                h("td", { class: "rn", text: String(index + 1) }),
                ...columns.map((column) => cell(column, row, index, problem[column.key])),
                h("td", { class: "rx" }, [remove]),
            ]));
            if (bad) {
                out.push(h("tr", { class: "rowerr" }, [h("td", { colspan: String(columns.length + 2) }, [icon("alert-circle-outline"), `Row ${index + 1}: ${Object.values(problem).join(" · ")}`])]));
            }
        });
        body.replaceChildren(...out);
        if (focusCell) {
            const again = body.querySelector(`[data-cell="${CSS.escape(focusCell)}"]`);
            if (again) {
                again.focus({ preventScroll: true });
                if (caret) {
                    try {
                        again.setSelectionRange(caret[0], caret[1]);
                    } catch (error) {
                        /* number inputs don't support selection ranges */
                    }
                }
            }
        }
        const bits = [plural(rows.length, "row")];
        if (spec.max_rows != null && spec.max_rows !== "") bits.push(`up to ${spec.max_rows}`);
        if (spec.min_rows != null && spec.min_rows !== "" && Number(spec.min_rows) > 0) bits.push(`at least ${spec.min_rows}`);
        meta.textContent = bits.join(" · ");
        addBtn.disabled = Boolean(ctx.disabled) || (spec.max_rows != null && spec.max_rows !== "" && rows.length >= Number(spec.max_rows));
        onlyBadBtn.hidden = !(badCount && rows.length > 6);
        onlyBadBtn.textContent = onlyBad ? "Show all rows" : `Show only the ${plural(badCount, "row")} to fix`;
        wrapEl.classList.toggle("has-bad", badCount > 0);
    }

    function paintSummary() {
        if (!summary) {
            sumHost.replaceChildren();
            return;
        }
        const close = h("button", { type: "button", class: "nwe-iconbtn ms-auto", "aria-label": "Dismiss" }, [icon("close")]);
        close.addEventListener("click", () => {
            summary = null;
            paintSummary();
        });
        const extra = (summary.actions || []).map(([text, run]) => {
            const button = h("button", { type: "button", class: "btn btn-sm nwe-btn", text });
            button.addEventListener("click", run);
            return button;
        });
        sumHost.replaceChildren(h("div", { class: `nwe-csv-sum ${summary.tone}` }, [
            icon(summary.tone === "ok" ? "check-circle-outline" : summary.tone === "bad" ? "alert-circle-outline" : "alert-outline"),
            h("div", {}, [
                h("div", { text: summary.title }),
                summary.items && summary.items.length ? h("ul", {}, summary.items.map((text) => h("li", { text }))) : null,
                extra.length ? h("div", { class: "d-flex gap-2 mt-2" }, extra) : null,
            ]),
            close,
        ]));
    }

    function imported(result, file) {
        const items = [...result.blocking, ...result.issues];
        const shown = items.slice(0, 5);
        if (items.length > 5) shown.push(`${items.length - 5} more not shown.`);
        if (result.ignored.length) shown.push(`Ignored column${result.ignored.length === 1 ? "" : "s"}: ${result.ignored.join(", ")}.`);
        const commit = (mode) => {
            let next = mode === "append" ? [...rows, ...result.rows] : result.rows;
            let cut = 0;
            if (spec.max_rows != null && spec.max_rows !== "" && next.length > Number(spec.max_rows)) {
                cut = next.length - Number(spec.max_rows);
                next = next.slice(0, Number(spec.max_rows));
            }
            rows = next;
            showAll = true;
            const added = result.rows.length - cut;
            summary = {
                tone: items.length || cut ? "warn" : "ok",
                title: `Imported ${plural(added, "row")} from ${file}${cut ? `. ${cut} didn't fit the ${spec.max_rows}-row limit` : ""}.${items.length ? " Check the rows marked in red." : ""}`,
                items: shown,
            };
            paintRows();
            paintSummary();
            emit();
        };
        if (result.missing.length) {
            summary = { tone: "bad", title: `${file} is missing ${result.missing.length === 1 ? "a required column" : "required columns"}: ${result.missing.join(", ")}. Nothing was imported.` };
            paintSummary();
            return;
        }
        if (!result.rows.length) {
            summary = { tone: "bad", title: `${file} has no rows to import.` };
            paintSummary();
            return;
        }
        if (rows.some((row) => Object.values(row).some((value) => value !== "" && value !== false && value != null))) {
            summary = {
                tone: "warn",
                title: `${file} has ${plural(result.rows.length, "row")}. The table already has ${plural(rows.length, "row")}.`,
                actions: [["Replace the rows", () => commit("replace")], ["Add to the end", () => commit("append")]],
            };
            paintSummary();
            return;
        }
        commit("replace");
    }

    fileInput.addEventListener("change", () => {
        const file = fileInput.files && fileInput.files[0];
        fileInput.value = "";
        if (!file) return;
        if (file.size > 2 * 1024 * 1024) {
            summary = { tone: "bad", title: `${file.name} is larger than 2 MB. Split it and import the parts.` };
            paintSummary();
            return;
        }
        file.text().then((text) => {
            try {
                imported(mapCsv(parseCsv(spreadsheetToCsv(text)), columns), file.name);
            } catch (error) {
                summary = { tone: "bad", title: `${file.name} couldn't be read: ${error.message}` };
                paintSummary();
            }
        });
    });
    wrapEl.addEventListener("click", (event) => {
        const act = event.target.closest("[data-act]")?.dataset.act;
        if (act === "import") fileInput.click();
        if (act === "template") {
            const link = document.createElement("a");
            link.href = URL.createObjectURL(new Blob([csvTemplate(columns)], { type: "text/csv" }));
            link.download = `${spec.key || "table"}-template.csv`;
            link.click();
            window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        }
    });
    addBtn.addEventListener("click", () => {
        rows.push(blankRow());
        paintRows();
        emit();
        const last = body.querySelectorAll("tr:not(.rowerr)");
        last[last.length - 1]?.querySelector("input, select")?.focus();
    });
    onlyBadBtn.addEventListener("click", () => {
        onlyBad = !onlyBad;
        paintRows();
    });
    paintRows();
    return {
        el: wrapEl,
        focusId: null,
        get: () => rows.map((row) => ({ ...row })),
        set: (value) => {
            rows = Array.isArray(value) ? value.map((row) => ({ ...row })) : [];
            paintRows();
        },
        invalid: (bad) => {
            wrapEl.classList.toggle("is-invalid", bad);
            if (bad && !showAll) {
                showAll = true;
                paintRows();
            }
        },
        focusEl: () => wrapEl.querySelector("input, select, button"),
    };
}
