/* Change freeze helpers shared by the launch page and the freeze form. Pure functions, tested in ui-tests. */

/** Which freeze window matters for a run picked at `whenMs`. Kinds: now, inside, soon, or "" (none). */
export function freezeHint(windows, whenMs, nowMs = Date.now()) {
    const rows = (windows || []).map((row) => ({ ...row, s: Date.parse(row.start), e: Date.parse(row.end) })).filter((row) => !Number.isNaN(row.s) && !Number.isNaN(row.e));
    const now = rows.find((row) => nowMs >= row.s && nowMs < row.e);
    if (now) return { kind: "now", name: now.name, start: now.start, end: now.end, message: now.message || "" };
    if (whenMs === null || whenMs === undefined || Number.isNaN(whenMs)) return { kind: "" };
    const inside = rows.find((row) => whenMs >= row.s && whenMs < row.e);
    if (inside) return { kind: "inside", name: inside.name, start: inside.start, end: inside.end, message: inside.message || "" };
    const soon = rows
        .filter((row) => (row.warning_lead_minutes || 0) > 0 && row.s > whenMs && row.s - whenMs <= row.warning_lead_minutes * 60000)
        .sort((a, b) => a.s - b.s)[0];
    if (soon) return { kind: "soon", name: soon.name, start: soon.start, end: soon.end, message: soon.message || "", minutes: Math.round((soon.s - whenMs) / 60000) };
    return { kind: "" };
}

/** The sentence shown under the date picker for a hint. */
export function hintText(hint, formatTime = (iso) => new Date(iso).toLocaleString()) {
    if (!hint || !hint.kind) return "";
    if (hint.kind === "now") return `Change freeze “${hint.name}” is in effect until ${formatTime(hint.end)}. Scheduling now would run pre-actions during it.`;
    if (hint.kind === "inside") return `That time is inside change freeze “${hint.name}” (${formatTime(hint.start)} – ${formatTime(hint.end)}). Pick a time outside it.`;
    return `Change freeze “${hint.name}” starts ${hint.minutes} min after this time. A run still going then keeps running.`;
}

/** Preset id -> the values it puts in the form. Mirrors freezes.PRESETS. */
export function applyPreset(presets, id) {
    const preset = (presets || {})[id];
    if (!preset) return null;
    return { rrule: preset.rrule, recur_start_time: preset.start_time, duration_minutes: String(preset.duration_minutes) };
}

/** Which preset (if any) the current rule, start time and length match. */
export function matchPreset(presets, values) {
    const time = String(values.recur_start_time || "").slice(0, 5);
    const rule = String(values.rrule || "").replace(/^RRULE:/i, "").trim().toUpperCase();
    const minutes = Number(values.duration_minutes);
    for (const [id, preset] of Object.entries(presets || {})) {
        if (preset.rrule.toUpperCase() === rule && preset.start_time === time && preset.duration_minutes === minutes) return id;
    }
    return "";
}

/** "61 h", "21 days", "1 day 2 h 5 min". */
export function humanMinutes(total) {
    const value = Number(total);
    if (!Number.isFinite(value) || value <= 0) return "";
    const days = Math.floor(value / 1440);
    const hours = Math.floor((value % 1440) / 60);
    const minutes = value % 60;
    if (days && !hours && !minutes) return `${days} day${days === 1 ? "" : "s"}`;
    if (!days && hours && !minutes && value < 1440) return `${hours} h`;
    if (days && hours && !minutes && days < 3) return `${days * 24 + hours} h`;
    const parts = [];
    if (days) parts.push(`${days} day${days === 1 ? "" : "s"}`);
    if (hours) parts.push(`${hours} h`);
    if (minutes) parts.push(`${minutes} min`);
    return parts.join(" ");
}

/** The preview card's text from a preview API response. */
export function previewLines(body) {
    if (!body) return { summary: "", windows: [], affects: "", runs: "", errors: "" };
    const errors = Object.entries(body.errors || {}).map(([field, message]) => `${field.replace(/_/g, " ")}: ${message}`);
    const count = body.affected_count || 0;
    let affects = body.has_scope ? `Affects ${count} workflow${count === 1 ? "" : "s"}` : "Choose what it covers to see which workflows it affects.";
    if (body.has_scope && (body.affected || []).length) affects += `: ${body.affected.join(", ")}${count > body.affected.length ? ", …" : ""}`;
    const runs = body.runs_count ? `${body.runs_count} scheduled run${body.runs_count === 1 ? "" : "s"} fall in this window. They will be Blocked by freeze when they come due.` : "";
    return {
        summary: errors.length ? "Fix the schedule to see the next windows." : body.summary || "",
        windows: (body.windows || []).map((row) => `${row.start_label} – ${row.end_label}`),
        affects,
        runs,
        errors: errors.join(" "),
    };
}
