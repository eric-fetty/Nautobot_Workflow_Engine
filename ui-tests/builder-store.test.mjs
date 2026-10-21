import assert from "node:assert/strict";
import test from "node:test";

import { errorText } from "../nautobot_workflow_engine/static/nautobot_workflow_engine/js/builder/sections.js";
import { blankInput, referencesTo, slugify, toBindPath } from "../nautobot_workflow_engine/static/nautobot_workflow_engine/js/builder/store.js";

test("slugify keeps a workflow id short and url-safe", () => {
    assert.equal(slugify("Reboot Device!"), "reboot-device");
    assert.equal(slugify("  "), "");
});

test("server pointers land on the field the page binds", () => {
    assert.equal(toBindPath({ pointer: "/inputs/0/label" }), "inputs.0.label");
    assert.equal(toBindPath({ pointer: "/inputs/1/filter/query" }), "inputs.1.filter_query");
    assert.equal(toBindPath({ pointer: "/actions/0/name" }), "actions.main.0.name");
    assert.equal(toBindPath({ pointer: "/pre_actions/2/when" }), "actions.pre.2.when");
    assert.equal(toBindPath({ path: "actions.main[0].template_id" }), "actions.main.0.template_id");
    assert.equal(toBindPath({ pointer: "/schedule/servicenow/timezone" }), "schedule.timezone");
});

test("deleting an input names every place that still uses it", () => {
    const payload = {
        inputs: [
            blankInput("object"),
            { ...blankInput("string"), key: "note", label: "Note", depends_on: ["device"], show_if: "{{ inputs.device }}" },
        ],
        schedule: { change_input_key: "" },
        actions: { pre: [], main: [{ name: "reboot", when: "{{ inputs.device.name }}", with: {}, extra_vars: {} }], post: [] },
    };
    payload.inputs[0].key = "device";
    payload.inputs[0].label = "Device";
    const hits = referencesTo(payload, "device");
    assert.ok(hits.some((hit) => hit.includes("Wait for these fields")));
    assert.ok(hits.some((hit) => hit.includes("Show only when")));
    assert.ok(hits.some((hit) => hit.includes("Main")));
});

test("plain error text keeps script paths and drops json pointers", () => {
    assert.equal(errorText("Missing scripts/notify.py at /inputs/0/key"), "Missing scripts/notify.py at");
});
