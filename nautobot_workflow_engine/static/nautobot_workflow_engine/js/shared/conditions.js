/**
 * Step conditions ("Run only if …") as a visual row <-> Jinja text.
 *
 * Inputs:        {{ inputs.mode == "soft" }}
 * Step status:   {{ results.check is defined and results.check.status == "success" }}
 * Step output:   {{ results.check is defined and results.check.return_value is mapping and results.check.return_value.reachable == true }}
 *
 * The runtime stores each step's result as results.<step key> with status
 * "success", "failed" or "skipped" and its return_value. The "is defined" guard
 * keeps a condition false (not an error) when the step never ran.
 */

export const STEP_TESTS = [
    ["succeeded", "succeeded"],
    ["failed", "failed"],
    ["skipped", "was skipped"],
    ["output", "output is"],
    ["output_not", "output is not"],
];

const STATUS = { succeeded: "success", failed: "failed", skipped: "skipped" };
const STATUS_BACK = { success: "succeeded", failed: "failed", skipped: "skipped" };
const IDENT = /^[A-Za-z_][A-Za-z0-9_]*$/;
const REF = String.raw`results(?:\.([A-Za-z_][A-Za-z0-9_]*)|\[\s*"([^"]+)"\s*\])`;
const FIELD = String.raw`[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*`;

export function stepRef(key) {
    return IDENT.test(key) ? `results.${key}` : `results[${JSON.stringify(key)}]`;
}

/** A typed value as a Jinja literal: true/false, numbers, otherwise a quoted string. */
export function literal(value) {
    if (value === true || value === "true") return "true";
    if (value === false || value === "false") return "false";
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
    if (typeof value === "string" && /^-?\d+(\.\d+)?$/.test(value.trim())) return value.trim();
    return JSON.stringify(String(value ?? ""));
}

function unliteral(raw) {
    const text = raw.trim();
    if ((text.startsWith('"') && text.endsWith('"')) || (text.startsWith("'") && text.endsWith("'"))) {
        try {
            return text.startsWith('"') ? JSON.parse(text) : text.slice(1, -1);
        } catch {
            return text.slice(1, -1);
        }
    }
    if (text === "true") return true;
    if (text === "false") return false;
    if (/^-?\d+(\.\d+)?$/.test(text)) return text;
    return undefined;
}

/** Parse a condition. Returns a row, an empty-row null for blank, or null when it needs the Jinja editor. */
export function parseWhen(value) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
        const entry = Object.entries(value)[0];
        if (!entry) return null;
        return { kind: "input", input: entry[0], op: "is", value: entry[1] };
    }
    if (typeof value !== "string") return null;
    const text = value.trim();
    const bare = text.match(/^\{\{\s*inputs\.([A-Za-z0-9_]+)\s*\}\}$/);
    if (bare) return { kind: "input", input: bare[1], op: "is", value: true };
    const cmp = text.match(/^\{\{\s*inputs\.([A-Za-z0-9_]+)\s*(==|!=)\s*(.+?)\s*\}\}$/);
    if (cmp) {
        const parsed = unliteral(cmp[3]);
        if (parsed === undefined) return null;
        return { kind: "input", input: cmp[1], op: cmp[2] === "!=" ? "is not" : "is", value: parsed };
    }
    const head = text.match(new RegExp(String.raw`^\{\{\s*${REF}\s+is\s+defined\s+and\s+(.+?)\s*\}\}$`));
    if (!head) return null;
    const step = head[1] || head[2];
    const rest = head[3];
    const same = (match) => match && (match[1] || match[2]) === step;
    const status = rest.match(new RegExp(String.raw`^${REF}\.status\s*==\s*"(success|failed|skipped)"$`));
    if (same(status)) return { kind: "step", step, test: STATUS_BACK[status[3]], field: "", value: "" };
    const field = rest.match(new RegExp(String.raw`^${REF}\.return_value\s+is\s+mapping\s+and\s+${REF}\.return_value\.(${FIELD})\s*(==|!=)\s*(.+)$`));
    if (field && same(field) && (field[3] || field[4]) === step) {
        const parsed = unliteral(field[7]);
        if (parsed === undefined) return null;
        return { kind: "step", step, test: field[6] === "!=" ? "output_not" : "output", field: field[5], value: parsed };
    }
    const whole = rest.match(new RegExp(String.raw`^${REF}\.return_value\s*(==|!=)\s*(.+)$`));
    if (same(whole)) {
        const parsed = unliteral(whole[4]);
        if (parsed === undefined) return null;
        return { kind: "step", step, test: whole[3] === "!=" ? "output_not" : "output", field: "", value: parsed };
    }
    return null;
}

/** Write a row back as Jinja text ("" when the row is empty). */
export function writeWhen(row) {
    if (!row) return "";
    if (row.kind === "step") {
        if (!row.step) return "";
        const ref = stepRef(row.step);
        if (STATUS[row.test]) return `{{ ${ref} is defined and ${ref}.status == "${STATUS[row.test]}" }}`;
        const op = row.test === "output_not" ? "!=" : "==";
        const field = String(row.field || "").trim().replace(/^\.+|\.+$/g, "");
        if (field && new RegExp(`^${FIELD}$`).test(field)) {
            return `{{ ${ref} is defined and ${ref}.return_value is mapping and ${ref}.return_value.${field} ${op} ${literal(row.value)} }}`;
        }
        return `{{ ${ref} is defined and ${ref}.return_value ${op} ${literal(row.value)} }}`;
    }
    if (!row.input) return "";
    const lit = literal(row.value);
    if (row.op === "is not") return `{{ inputs.${row.input} != ${lit} }}`;
    if (lit === "true") return `{{ inputs.${row.input} }}`;
    return `{{ inputs.${row.input} == ${lit} }}`;
}

/** Steps that run before position (phase, index): earlier phases, then earlier rows of the same phase. */
export function earlierSteps(actions, phase, index) {
    const order = ["pre", "main", "post"];
    const out = [];
    for (const name of order) {
        const rows = (actions && actions[name]) || [];
        rows.forEach((row, at) => {
            if (name === phase && at >= index) return;
            if (order.indexOf(name) > order.indexOf(phase)) return;
            if (row && row.name) out.push({ phase: name, index: at, key: row.name, label: row.label || row.name });
        });
        if (name === phase) break;
    }
    return out;
}

/** The step keys a condition refers to (for "this step was moved before X" warnings). */
export function referencedSteps(text) {
    const found = new Set();
    const pattern = new RegExp(REF, "g");
    let match;
    while ((match = pattern.exec(String(text || "")))) found.add(match[1] || match[2]);
    return [...found];
}
