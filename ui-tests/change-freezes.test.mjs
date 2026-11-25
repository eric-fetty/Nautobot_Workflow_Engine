import assert from "node:assert/strict";
import test from "node:test";

import {
    applyPreset,
    freezeHint,
    hintText,
    humanMinutes,
    matchPreset,
    previewLines,
} from "../nautobot_workflow_engine/static/nautobot_workflow_engine/js/freezes/hints.js";

const NOW = Date.parse("2026-12-01T12:00:00Z");
const WINDOWS = [
    { name: "Weekend", start: "2026-12-04T23:00:00Z", end: "2026-12-07T12:00:00Z", warning_lead_minutes: 120, message: "Weekend" },
    { name: "Quiet", start: "2026-12-10T00:00:00Z", end: "2026-12-10T06:00:00Z", warning_lead_minutes: 0 },
];
const PRESETS = {
    weekend: { label: "Weekly: Fri 17:00 – Mon 06:00", rrule: "FREQ=WEEKLY;BYDAY=FR", start_time: "17:00", duration_minutes: 3660 },
    nightly: { label: "Nightly", rrule: "FREQ=DAILY", start_time: "22:00", duration_minutes: 480 },
};

test("a time inside a window is flagged", () => {
    const hint = freezeHint(WINDOWS, Date.parse("2026-12-05T10:00:00Z"), NOW);
    assert.equal(hint.kind, "inside");
    assert.equal(hint.name, "Weekend");
    assert.match(hintText(hint, (iso) => iso), /inside change freeze “Weekend”/);
});

test("a freeze in effect now wins over the picked time", () => {
    const hint = freezeHint(WINDOWS, Date.parse("2026-12-20T10:00:00Z"), Date.parse("2026-12-05T00:00:00Z"));
    assert.equal(hint.kind, "now");
    assert.match(hintText(hint, (iso) => iso), /pre-actions/);
});

test("the lead time warns, and 0 turns the warning off", () => {
    const soon = freezeHint(WINDOWS, Date.parse("2026-12-04T22:00:00Z"), NOW);
    assert.deepEqual([soon.kind, soon.minutes], ["soon", 60]);
    assert.match(hintText(soon), /starts 60 min after/);
    assert.equal(freezeHint(WINDOWS, Date.parse("2026-12-04T20:00:00Z"), NOW).kind, "");
    assert.equal(freezeHint(WINDOWS, Date.parse("2026-12-09T23:30:00Z"), NOW).kind, "");
    assert.equal(hintText({ kind: "" }), "");
});

test("the window end is open and bad input is ignored", () => {
    assert.equal(freezeHint(WINDOWS, Date.parse("2026-12-07T12:00:00Z"), NOW).kind, "");
    assert.equal(freezeHint([{ name: "x", start: "nope", end: "nope" }], NOW, NOW).kind, "");
    assert.equal(freezeHint(null, NaN, NOW).kind, "");
});

test("presets fill the form and are recognised again", () => {
    assert.deepEqual(applyPreset(PRESETS, "weekend"), { rrule: "FREQ=WEEKLY;BYDAY=FR", recur_start_time: "17:00", duration_minutes: "3660" });
    assert.equal(applyPreset(PRESETS, "missing"), null);
    assert.equal(matchPreset(PRESETS, { rrule: "rrule:freq=daily", recur_start_time: "22:00:00", duration_minutes: "480" }), "nightly");
    assert.equal(matchPreset(PRESETS, { rrule: "FREQ=DAILY", recur_start_time: "21:00", duration_minutes: "480" }), "");
});

test("lengths read like people say them", () => {
    assert.equal(humanMinutes(3660), "61 h");
    assert.equal(humanMinutes(480), "8 h");
    assert.equal(humanMinutes(2880), "2 days");
    assert.equal(humanMinutes(30240), "21 days");
    assert.equal(humanMinutes(1565), "1 day 2 h 5 min");
    assert.equal(humanMinutes(0), "");
});

test("the preview card text", () => {
    const lines = previewLines({
        errors: {},
        summary: "Every Fri 17:00 – Mon 06:00, America/Chicago",
        windows: [{ start_label: "Fri 4 Dec 17:00", end_label: "Mon 7 Dec 06:00" }],
        has_scope: true,
        affected_count: 3,
        affected: ["A", "B"],
        runs_count: 1,
    });
    assert.equal(lines.affects, "Affects 3 workflows: A, B, …");
    assert.equal(lines.windows[0], "Fri 4 Dec 17:00 – Mon 7 Dec 06:00");
    assert.match(lines.runs, /^1 scheduled run fall in this window/);
    const broken = previewLines({ errors: { ends_at: "End must be after start." }, has_scope: false });
    assert.equal(broken.summary, "Fix the schedule to see the next windows.");
    assert.equal(broken.errors, "ends at: End must be after start.");
    assert.match(broken.affects, /^Choose what it covers/);
});
