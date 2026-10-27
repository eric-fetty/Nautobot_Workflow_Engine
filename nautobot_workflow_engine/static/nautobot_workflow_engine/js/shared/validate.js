/** Launch-form validation, shared by the launch page and the builder preview. Pure functions. */

const FLAG = { IGNORECASE: "i", I: "i", i: "i", MULTILINE: "m", M: "m", m: "m", DOTALL: "s", S: "s", s: "s" };

export function regexFlags(flags) {
    const list = Array.isArray(flags) ? flags : String(flags || "").split(/[\s,|]+/);
    let out = "";
    list.forEach((flag) => {
        const mapped = FLAG[String(flag || "").trim()];
        if (mapped && !out.includes(mapped)) out += mapped;
    });
    return out;
}

function compiled(validator) {
    if (!validator || !validator.regex) return null;
    try {
        return new RegExp(validator.regex, regexFlags(validator.flags));
    } catch (error) {
        return null;
    }
}

export function patternError(validators, text) {
    if (!text) return "";
    for (const validator of validators || []) {
        const regex = compiled(validator);
        if (regex && !regex.test(text)) return validator.message || "This value doesn't match the required format.";
    }
    return "";
}

function plainNumber(value) {
    const number = Number(value);
    return Number.isInteger(number) ? String(number) : String(value);
}

export function rangeSentence(low, high) {
    const hasLow = low !== null && low !== undefined && low !== "";
    const hasHigh = high !== null && high !== undefined && high !== "";
    if (hasLow && hasHigh) return `Between ${plainNumber(low)} and ${plainNumber(high)}`;
    if (hasLow) return `At least ${plainNumber(low)}`;
    if (hasHigh) return `At most ${plainNumber(high)}`;
    return "";
}

export function isEmpty(value) {
    if (value == null) return true;
    if (Array.isArray(value)) return value.length === 0;
    if (typeof value === "string") return value.trim() === "";
    return false;
}

export function lines(text) {
    return String(text || "")
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);
}

/** Cell problems for one table row: {columnKey: message}. */
export function rowErrors(columns, row, seen) {
    const out = {};
    columns.forEach((column) => {
        const key = column.key;
        if (!key) return;
        const type = String(column.type || "string").toLowerCase();
        const value = row ? row[key] : undefined;
        const label = column.label || key;
        const text = value == null ? "" : String(value).trim();
        if (column.required === true && type !== "boolean" && !text) {
            out[key] = `${label} is required`;
            return;
        }
        if (!text || type === "boolean") return;
        if (type === "integer" && !/^[-+]?\d+$/.test(text)) {
            out[key] = `${label} must be a whole number`;
            return;
        }
        if (type === "number" && Number.isNaN(Number(text))) {
            out[key] = `${label} must be a number`;
            return;
        }
        if ((type === "integer" || type === "number") && (column.min_value != null || column.max_value != null)) {
            const number = Number(text);
            const low = column.min_value === "" || column.min_value == null ? null : Number(column.min_value);
            const high = column.max_value === "" || column.max_value == null ? null : Number(column.max_value);
            if ((low != null && number < low) || (high != null && number > high)) {
                out[key] = `${label} must be ${rangeSentence(low, high).toLowerCase()}`;
                return;
            }
        }
        if (type !== "integer" && type !== "number" && "=+-@".includes(text.charAt(0))) {
            out[key] = `${label} starts with "${text.charAt(0)}", which looks like a spreadsheet formula`;
            return;
        }
        if (type === "choice") {
            const allowed = (column.choices || []).map((choice) => String(choice.value));
            if (allowed.length && !allowed.includes(text)) {
                out[key] = `${label} isn't one of the options`;
                return;
            }
        }
        const pattern = patternError(column.validators, text);
        if (pattern) {
            out[key] = `${label}: ${pattern}`;
            return;
        }
        if (column.unique === true && seen) {
            const bucket = seen[key] || (seen[key] = new Map());
            const normalized = text.toLowerCase();
            if (bucket.has(normalized)) out[key] = `${label} is already used in row ${bucket.get(normalized)}`;
            else bucket.set(normalized, seen.__row);
        }
    });
    return out;
}

/** All row problems for a table: [{row (1-based), cells: {key: message}}]. */
export function tableErrors(columns, rows) {
    const seen = {};
    const out = [];
    (rows || []).forEach((row, index) => {
        seen.__row = index + 1;
        const cells = rowErrors(columns || [], row, seen);
        if (Object.keys(cells).length) out.push({ row: index + 1, cells });
    });
    return out;
}

/** One message for a field, or "" when it is fine. */
export function fieldError(spec, value) {
    const type = spec.type;
    if (spec.required && type !== "boolean" && isEmpty(value)) {
        if (type === "object" || type === "choice") return "Choose one.";
        if (type === "multiobject" || type === "multichoice") return "Choose at least one.";
        if (type === "list") return "Add at least one line.";
        if (type === "listdict") return "Add at least one row.";
        return "This field is required.";
    }
    if (isEmpty(value)) return "";
    if (type === "integer") {
        const text = String(value).trim();
        if (!/^[-+]?\d+$/.test(text)) return "Enter a whole number.";
        const number = Number(text);
        const low = spec.min_value;
        const high = spec.max_value;
        if ((low != null && low !== "" && number < Number(low)) || (high != null && high !== "" && number > Number(high))) {
            return `${rangeSentence(low, high)}.`;
        }
        return patternError(spec.validators, text);
    }
    if (type === "string") return patternError(spec.validators, String(value));
    if (type === "list") {
        const items = Array.isArray(value) ? value : lines(value);
        if (spec.min_items != null && spec.min_items !== "" && items.length < Number(spec.min_items)) return `Enter at least ${spec.min_items} lines.`;
        if (spec.max_items != null && spec.max_items !== "" && items.length > Number(spec.max_items)) return `Enter at most ${spec.max_items} lines.`;
        for (let index = 0; index < items.length; index += 1) {
            const problem = patternError(spec.validators, items[index]);
            if (problem) return `Line ${index + 1}: ${problem}`;
        }
        return "";
    }
    if (type === "listdict") {
        const rows = Array.isArray(value) ? value : [];
        if (spec.min_rows != null && spec.min_rows !== "" && rows.length < Number(spec.min_rows)) return `Add at least ${spec.min_rows} rows.`;
        if (spec.max_rows != null && spec.max_rows !== "" && rows.length > Number(spec.max_rows)) return `Use at most ${spec.max_rows} rows.`;
        const bad = tableErrors(spec.columns || [], rows);
        if (bad.length) return `${bad.length} row${bad.length === 1 ? " needs" : "s need"} fixing.`;
        return "";
    }
    return "";
}
