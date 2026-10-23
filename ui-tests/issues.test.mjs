import assert from "node:assert/strict";
import test from "node:test";

import { draftSnapshot, fieldKey, mergeIssues, validationGate } from "../nautobot_workflow_engine/static/nautobot_workflow_engine/js/shared/issues.js";

test("server messages the client already shows are not counted twice", () => {
    const merged = mergeIssues({ details: ["Name is required."] }, [
        { section: "details", message: "Name is required" },
        { section: "steps", message: "Script scripts/x.py isn’t in the repository." },
        { section: "nowhere", message: "Odd" },
        { section: "steps", message: "Script scripts/x.py isn’t in the repository." },
    ]);
    assert.deepEqual(merged.details, ["Name is required."]);
    assert.deepEqual(merged.steps, ["Script scripts/x.py isn’t in the repository."]);
    assert.deepEqual(merged.review, ["Odd"]);
});

test("a fix clears the issue once the server answers for the new draft", () => {
    assert.deepEqual(mergeIssues({ steps: [] }, []).steps, []);
});

test("answers to an older draft are ignored", () => {
    const gate = validationGate();
    const first = gate.send("a");
    const second = gate.send("b");
    assert.equal(gate.accept(first, "b"), false, "stale request");
    assert.equal(gate.accept(second, "c"), false, "draft changed while waiting");
    assert.equal(gate.accept(second, "b"), true);
});

test("UI bookkeeping does not count as a change", () => {
    const one = draftSnapshot({ name: "A", actions: { main: [{ name: "x", _whenMode: "jinja" }] } });
    const two = draftSnapshot({ name: "A", actions: { main: [{ name: "x", _whenMode: "visual" }] } });
    assert.equal(one, two);
    assert.notEqual(one, draftSnapshot({ name: "B", actions: { main: [{ name: "x" }] } }));
});

test("a server error on a field the client already flags is dropped, whatever its wording", () => {
    const merged = mergeIssues({ details: [["Name is required.", "name"]], inputs: [["Device: choose an object type.", "inputs.0.model"]] }, [
        { section: "details", pointer: "/name", message: "Enter a name." },
        { section: "inputs", pointer: "/inputs/0/model", message: "Choose a model." },
        { section: "steps", pointer: "/actions/1/script", message: "Choose a script from the source repository." },
    ]);
    assert.deepEqual(merged.details, ["Name is required."]);
    assert.deepEqual(merged.inputs, ["Device: choose an object type."]);
    assert.deepEqual(merged.steps, ["Choose a script from the source repository."]);
    assert.equal(fieldKey("/post_actions/0/when"), "steps.post.0.when");
    assert.equal(fieldKey("/schedule/servicenow/timezone"), "schedule.timezone");
});
