/**
 * Plain-text helpers for Jinja templates and step parameters. No DOM here, so the
 * builder, the launch form and the node tests share one copy.
 */

const INPUT_REF = /^\{\{\s*inputs\.([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}$/;

/** True when a value is Jinja rather than a plain value. */
export function isTemplate(value) {
    return typeof value === "string" && /\{\{|\{%/.test(value);
}

/**
 * Work out how a stored parameter value reads in the builder.
 * Nothing extra is written to YAML: the mode comes from the value itself.
 *   "{{ inputs.device.id }}" -> { mode: "input", input: "device", attr: "id" }
 *   "{{ inputs.device.name | upper }}" -> { mode: "expr" }
 *   15, true, "active" -> { mode: "fixed" }
 */
export function paramMode(value) {
    if (typeof value === "string") {
        const match = value.match(INPUT_REF);
        if (match) return { mode: "input", input: match[1], attr: match[2] ? match[2].slice(1) : "" };
        if (isTemplate(value)) return { mode: "expr" };
    }
    return { mode: "fixed" };
}

/** The stored value for a parameter that comes straight from an input. */
export function inputRef(input, attr) {
    if (!input) return "";
    return `{{ inputs.${input}${attr ? `.${attr}` : ""} }}`;
}

/**
 * Parse what someone typed as a fixed value. Declared numbers and yes/no values
 * keep their YAML type; everything else stays text, so `"0012"` is not mangled.
 */
export function fixedValue(text, declaredType, previous) {
    const raw = String(text ?? "");
    const kind = declaredType || (typeof previous === "number" ? "integer" : typeof previous === "boolean" ? "boolean" : "");
    if (kind === "integer" || kind === "number") {
        if (/^-?\d+(\.\d+)?$/.test(raw.trim())) return Number(raw.trim());
        return raw;
    }
    if (kind === "boolean") {
        if (/^(true|yes|on|1)$/i.test(raw.trim())) return true;
        if (/^(false|no|off|0)$/i.test(raw.trim())) return false;
        return raw;
    }
    if ((Array.isArray(previous) || (previous && typeof previous === "object")) && /^[[{]/.test(raw.trim())) {
        try {
            return JSON.parse(raw);
        } catch (error) {
            return raw;
        }
    }
    return raw;
}

/** Text shown in a fixed-value box. */
export function fixedText(value) {
    if (value == null) return "";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
}

/** Split a template into plain text, {{ expressions }}, {% statements %} and {# comments #}. */
export function tokenize(text) {
    const source = String(text ?? "");
    const parts = [];
    const pattern = /\{\{[\s\S]*?(\}\}|$)|\{%[\s\S]*?(%\}|$)|\{#[\s\S]*?(#\}|$)/g;
    let cursor = 0;
    let match = pattern.exec(source);
    while (match) {
        if (match.index > cursor) parts.push({ kind: "text", text: source.slice(cursor, match.index), start: cursor });
        const body = match[0];
        const kind = body.startsWith("{{") ? "expr" : body.startsWith("{%") ? "stmt" : "comment";
        const closed = Boolean(match[1] || match[2] || match[3]);
        parts.push({ kind, text: body, start: match.index, closed });
        cursor = match.index + body.length;
        if (!body.length) break;
        match = pattern.exec(source);
    }
    if (cursor < source.length) parts.push({ kind: "text", text: source.slice(cursor), start: cursor });
    return parts;
}

function distance(a, b) {
    const row = Array.from({ length: b.length + 1 }, (_, index) => index);
    for (let i = 1; i <= a.length; i += 1) {
        let prev = row[0];
        row[0] = i;
        for (let j = 1; j <= b.length; j += 1) {
            const keep = row[j];
            row[j] = Math.min(row[j] + 1, row[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
            prev = keep;
        }
    }
    return row[b.length];
}

function closest(name, options) {
    let best = "";
    let score = Infinity;
    options.forEach((option) => {
        const value = distance(name.toLowerCase(), option.toLowerCase());
        if (value < score) {
            score = value;
            best = option;
        }
    });
    return score <= Math.max(2, Math.floor(name.length / 3)) ? best : "";
}

const BLOCKS = { if: "endif", for: "endfor", macro: "endmacro", filter: "endfilter", set: "endset", with: "endwith", call: "endcall" };

/**
 * Check a template the way someone writing it needs: unclosed tags, unbalanced
 * blocks, and references to inputs or steps that don't exist (with a suggestion).
 * Returns a list of { message, start, end } problems; empty means it looks fine.
 * The server still renders it in the sandbox, which is the final word.
 */
export function checkTemplate(text, known) {
    const inputs = (known && known.inputs) || [];
    const results = (known && known.results) || [];
    const problems = [];
    const stack = [];
    tokenize(text).forEach((part) => {
        if (part.kind === "text") {
            const stray = part.text.search(/\}\}|%\}/);
            if (stray >= 0) problems.push({ message: "There's a closing }} without an opening {{.", start: part.start + stray, end: part.start + stray + 2 });
            return;
        }
        if (!part.closed) {
            const want = part.kind === "expr" ? "}}" : part.kind === "stmt" ? "%}" : "#}";
            problems.push({ message: `This ${part.text.slice(0, 2)} is never closed. Add ${want}.`, start: part.start, end: part.start + part.text.length });
            return;
        }
        if (part.kind === "comment") return;
        const inner = part.text.slice(2, -2);
        if (part.kind === "expr" && !inner.trim()) {
            problems.push({ message: "This {{ }} is empty.", start: part.start, end: part.start + part.text.length });
        }
        if (part.kind === "stmt") {
            const word = (inner.trim().replace(/^-|-$/g, "").trim().split(/\s+/)[0] || "").toLowerCase();
            if (BLOCKS[word] && !(word === "set" && inner.includes("="))) stack.push({ word, part });
            else if (word.startsWith("end")) {
                const open = stack.pop();
                if (!open || BLOCKS[open.word] !== word) {
                    problems.push({ message: open ? `{% ${word} %} doesn't match {% ${open.word} %}.` : `{% ${word} %} has no opening tag.`, start: part.start, end: part.start + part.text.length });
                }
            }
        }
        const refs = /\b(inputs|results)\.([A-Za-z_][A-Za-z0-9_]*)/g;
        let ref = refs.exec(inner);
        while (ref) {
            const [whole, root, name] = ref;
            const list = root === "inputs" ? inputs : results;
            if (list.length && !list.includes(name)) {
                const near = closest(name, list);
                const offset = part.start + 2 + ref.index;
                const noun = root === "inputs" ? "input" : "earlier step";
                problems.push({
                    message: `There's no ${noun} called “${name}”.${near ? ` Did you mean “${near}”?` : ""}`,
                    start: offset,
                    end: offset + whole.length,
                    fix: near ? { from: whole, to: `${root}.${near}` } : null,
                });
            } else if (!list.length && root === "results") {
                const offset = part.start + 2 + ref.index;
                problems.push({ message: "No earlier step has a result yet. Results only exist for steps above this one.", start: offset, end: offset + whole.length });
            }
            ref = refs.exec(inner);
        }
    });
    stack.forEach((open) => {
        problems.push({ message: `{% ${open.word} %} needs a matching {% ${BLOCKS[open.word]} %}.`, start: open.part.start, end: open.part.start + open.part.text.length });
    });
    return problems;
}

/**
 * What to complete at the caret: inside an open {{ or {%, returns the partial
 * name typed so far and where it starts. Outside a tag returns null.
 */
export function completionAt(text, caret) {
    const before = String(text ?? "").slice(0, caret);
    const open = Math.max(before.lastIndexOf("{{"), before.lastIndexOf("{%"));
    if (open < 0) return null;
    const close = Math.max(before.lastIndexOf("}}"), before.lastIndexOf("%}"));
    if (close > open) return null;
    const word = before.slice(open + 2).match(/[A-Za-z0-9_.]*$/)[0];
    return { tagStart: open, start: caret - word.length, query: word };
}

/** Filter and rank references for the autocomplete list. */
export function matchReferences(references, query) {
    const needle = String(query || "").toLowerCase();
    if (!needle) return references.slice();
    return references
        .map((item) => {
            const path = item.path.toLowerCase();
            const label = String(item.label || "").toLowerCase();
            let rank = -1;
            if (path.startsWith(needle)) rank = 0;
            else if (path.split(".").some((bit) => bit.startsWith(needle))) rank = 1;
            else if (path.includes(needle) || label.includes(needle)) rank = 2;
            return { item, rank };
        })
        .filter((entry) => entry.rank >= 0)
        .sort((a, b) => a.rank - b.rank)
        .map((entry) => entry.item);
}
