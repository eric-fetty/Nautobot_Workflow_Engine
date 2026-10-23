/** CSV and spreadsheet helpers for the table input. Pure functions, no DOM. */

const TRUE_WORDS = ["true", "1", "yes", "y", "on"];
const FALSE_WORDS = ["false", "0", "no", "n", "off", ""];

export function normalizeHeader(value) {
    return String(value || "").trim().toLowerCase();
}

/** Turn tab-separated text (pasted from a spreadsheet) into CSV. */
export function spreadsheetToCsv(text) {
    const source = String(text || "").replace(/^\uFEFF/, "");
    const first = source.split(/\r?\n/)[0] || "";
    const tabs = (first.match(/\t/g) || []).length;
    const commas = (first.match(/,/g) || []).length;
    if (tabs <= commas) return source;
    return source
        .split(/\r?\n/)
        .map((line) => line.split("\t").map((cell) => (/[",\n]/.test(cell) ? `"${cell.replace(/"/g, '""')}"` : cell)).join(","))
        .join("\n");
}

/** Parse CSV text into {headers, rows:[{values, lineNumber}], issues}. */
export function parseCsv(text) {
    let source = String(text || "");
    if (source.charCodeAt(0) === 0xfeff) source = source.slice(1);
    source = source.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    const rows = [];
    let row = [];
    let cell = "";
    let quoted = false;
    let line = 1;
    for (let index = 0; index < source.length; index += 1) {
        const char = source[index];
        if (quoted) {
            if (char === '"') {
                if (source[index + 1] === '"') {
                    cell += '"';
                    index += 1;
                } else quoted = false;
            } else cell += char;
            continue;
        }
        if (char === '"') quoted = true;
        else if (char === ",") {
            row.push(cell);
            cell = "";
        } else if (char === "\n") {
            row.push(cell);
            rows.push({ values: row, lineNumber: line });
            row = [];
            cell = "";
            line += 1;
        } else cell += char;
    }
    if (quoted) throw new Error("The CSV has a quoted value that never ends.");
    if (cell !== "" || row.length) {
        row.push(cell);
        rows.push({ values: row, lineNumber: line });
    }
    if (!rows.length) return { headers: [], rows: [], issues: [] };
    const headers = rows.shift().values.map((header) => String(header || "").trim());
    const issues = [];
    const data = rows.filter((entry) => {
        const filled = entry.values.some((value) => String(value || "").trim() !== "");
        if (filled && entry.values.length !== headers.length) {
            issues.push(`Line ${entry.lineNumber}: expected ${headers.length} column${headers.length === 1 ? "" : "s"}, found ${entry.values.length}.`);
        }
        return filled;
    });
    return { headers, rows: data, issues };
}

export function blankCell(column) {
    return String(column?.type || "string").toLowerCase() === "boolean" ? false : "";
}

/** Coerce one imported cell. Returns {value, issue, blocking}. */
export function coerceCell(column, raw) {
    const type = String(column?.type || "string").toLowerCase();
    const text = String(raw == null ? "" : raw).trim();
    if (type === "boolean") {
        const lowered = text.toLowerCase();
        if (TRUE_WORDS.includes(lowered)) return { value: true, issue: null, blocking: false };
        if (FALSE_WORDS.includes(lowered)) return { value: false, issue: null, blocking: false };
        return { value: false, issue: `"${text}" isn't yes or no. Use true/false, yes/no, on/off or 1/0.`, blocking: true };
    }
    if (type === "choice") {
        if (!text) return { value: "", issue: null, blocking: false };
        const match = (column.choices || []).find((choice) => {
            const value = String(choice?.value ?? "").trim().toLowerCase();
            const label = String(choice?.label ?? choice?.value ?? "").trim().toLowerCase();
            return text.toLowerCase() === value || text.toLowerCase() === label;
        });
        if (match) return { value: match.value, issue: null, blocking: false };
        return { value: text, issue: `"${text}" isn't one of the options`, blocking: false };
    }
    const numeric = type === "integer" || type === "number";
    if (text && !(numeric && /^-?\d+(?:\.\d+)?$/.test(text)) && "=+-@".includes(text.charAt(0))) {
        return { value: raw ?? "", issue: `starts with "${text.charAt(0)}", which looks like a spreadsheet formula`, blocking: false };
    }
    return { value: raw == null ? blankCell(column) : raw, issue: null, blocking: false };
}

/** Map parsed CSV onto the table's columns by key or label. */
export function mapCsv(parsed, columns) {
    const byHeader = {};
    parsed.headers.forEach((header, index) => {
        const key = normalizeHeader(header);
        if (key && byHeader[key] === undefined) byHeader[key] = index;
    });
    const used = new Set();
    const mapped = columns.map((column) => {
        let index = byHeader[normalizeHeader(column.key)];
        if (index === undefined) index = byHeader[normalizeHeader(column.label || "")];
        if (index !== undefined) used.add(index);
        return { column, index };
    });
    const missing = mapped.filter((entry) => entry.index === undefined && entry.column.required === true).map((entry) => entry.column.label || entry.column.key);
    const ignored = parsed.headers.filter((_header, index) => !used.has(index) && String(_header).trim());
    const issues = [...(parsed.issues || [])];
    const blocking = [];
    const rows = parsed.rows.map((entry, offset) => {
        const row = {};
        mapped.forEach(({ column, index }) => {
            const raw = index === undefined ? blankCell(column) : entry.values[index];
            const result = coerceCell(column, raw);
            row[column.key] = result.value;
            if (result.issue) {
                const text = `Row ${offset + 1}: ${column.label || column.key} ${result.issue}`;
                (result.blocking ? blocking : issues).push(text);
            }
        });
        return row;
    });
    return { rows, missing, ignored, issues, blocking };
}

export function csvTemplate(columns) {
    return `${columns.map((column) => `"${String(column.label || column.key || "").replace(/"/g, '""')}"`).join(",")}\n`;
}
