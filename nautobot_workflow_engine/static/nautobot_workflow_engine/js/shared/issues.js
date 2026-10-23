/**
 * Issue bookkeeping for the builder: local checks plus the server's last
 * validation, without duplicates and without answers to an older draft.
 */

const SECTIONS = ["details", "inputs", "steps", "schedule", "review"];

function norm(text) {
    return String(text || "")
        .toLowerCase()
        .replace(/[“”"'`.]/g, "")
        .replace(/\s+/g, " ")
        .trim();
}

const PHASE_KEYS = { pre_actions: "pre", actions: "main", post_actions: "post" };

/** The field a server error points at, in the same shape the local checks use ("inputs.0.model"). */
export function fieldKey(pointer) {
    const parts = String(pointer || "").split("/").filter(Boolean).map((part) => part.replace(/~1/g, "/").replace(/~0/g, "~"));
    if (!parts.length) return "";
    if (PHASE_KEYS[parts[0]]) return ["steps", PHASE_KEYS[parts[0]], ...parts.slice(1)].join(".");
    if (parts[0] === "schedule" && parts[1] === "servicenow") return ["schedule", ...parts.slice(2)].join(".");
    return parts.join(".");
}

/**
 * Merge local buckets with server errors. Local items are strings or
 * [message, fieldKey]. A server error about a field the client already flags,
 * or with the same words, is dropped so nothing is counted twice.
 */
export function mergeIssues(local, serverErrors) {
    const buckets = {};
    const seen = new Set();
    SECTIONS.forEach((section) => {
        buckets[section] = ((local && local[section]) || []).map((item) => {
            const [message, key] = Array.isArray(item) ? item : [item, ""];
            seen.add(norm(message));
            if (key) seen.add(`field:${key}`);
            return message;
        });
    });
    (serverErrors || []).forEach((item) => {
        const section = SECTIONS.includes(item && item.section) ? item.section : "review";
        const message = (item && item.message) || "Check this value.";
        const key = fieldKey(item && item.pointer);
        if (seen.has(norm(message)) || (key && seen.has(`field:${key}`))) return;
        seen.add(norm(message));
        if (key) seen.add(`field:${key}`);
        buckets[section].push(message);
    });
    return buckets;
}

/**
 * Remember what each validation request was sent for. A response only counts
 * when the draft still looks exactly like it did when the request left; a newer
 * request is already on its way otherwise.
 */
export function validationGate() {
    let latest = 0;
    return {
        send(snapshot) {
            latest += 1;
            return { id: latest, snapshot };
        },
        accept(ticket, currentSnapshot) {
            return Boolean(ticket) && ticket.id === latest && ticket.snapshot === currentSnapshot;
        },
    };
}

/** Bookkeeping keys (leading underscore) are UI state, not part of the draft. */
export function draftSnapshot(payload) {
    return JSON.stringify(payload, (key, value) => (key.startsWith("_") && key !== "_" ? undefined : value));
}
