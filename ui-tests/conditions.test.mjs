import assert from "node:assert/strict";
import test from "node:test";

import { earlierSteps, literal, parseWhen, referencedSteps, writeWhen } from "../nautobot_workflow_engine/static/nautobot_workflow_engine/js/shared/conditions.js";

test("input conditions keep their existing YAML", () => {
    assert.equal(writeWhen({ kind: "input", input: "wait", op: "is", value: true }), "{{ inputs.wait }}");
    assert.equal(writeWhen({ kind: "input", input: "mode", op: "is", value: "soft" }), '{{ inputs.mode == "soft" }}');
    assert.equal(writeWhen({ input: "mode", op: "is not", value: "soft" }), '{{ inputs.mode != "soft" }}');
    assert.deepEqual(parseWhen('{{ inputs.mode == "soft" }}'), { kind: "input", input: "mode", op: "is", value: "soft" });
    assert.deepEqual(parseWhen("{{ inputs.wait }}"), { kind: "input", input: "wait", op: "is", value: true });
});

test("step status conditions round trip", () => {
    for (const test of ["succeeded", "failed", "skipped"]) {
        const text = writeWhen({ kind: "step", step: "check", test });
        assert.match(text, /^\{\{ results\.check is defined and results\.check\.status == "(success|failed|skipped)" \}\}$/);
        assert.deepEqual(parseWhen(text), { kind: "step", step: "check", test, field: "", value: "" });
    }
});

test("step output conditions round trip, with and without a field", () => {
    const field = { kind: "step", step: "check", test: "output", field: "reachable", value: true };
    const text = writeWhen(field);
    assert.equal(text, "{{ results.check is defined and results.check.return_value is mapping and results.check.return_value.reachable == true }}");
    assert.deepEqual(parseWhen(text), field);
    const nested = { kind: "step", step: "check", test: "output_not", field: "facts.os", value: "ios" };
    assert.deepEqual(parseWhen(writeWhen(nested)), nested);
    const whole = { kind: "step", step: "count", test: "output", field: "", value: "3" };
    assert.equal(writeWhen(whole), "{{ results.count is defined and results.count.return_value == 3 }}");
    assert.deepEqual(parseWhen(writeWhen(whole)), whole);
});

test("step keys that are not identifiers use bracket access", () => {
    const row = { kind: "step", step: "check-device", test: "failed", field: "", value: "" };
    assert.equal(writeWhen(row), '{{ results["check-device"] is defined and results["check-device"].status == "failed" }}');
    assert.deepEqual(parseWhen(writeWhen(row)), row);
    assert.deepEqual(referencedSteps(writeWhen(row)), ["check-device"]);
});

test("mismatched or hand-written Jinja stays in the Jinja editor", () => {
    assert.equal(parseWhen("{{ results.a is defined and results.b.status == \"success\" }}"), null);
    assert.equal(parseWhen("{{ inputs.a and inputs.b }}"), null);
    assert.equal(parseWhen("{{ results.a.status == 'success' }}"), null);
});

test("literals", () => {
    assert.equal(literal("5"), "5");
    assert.equal(literal("up"), '"up"');
    assert.equal(literal("say \"hi\""), '"say \\"hi\\""');
});

test("only steps that run earlier are offered", () => {
    const actions = {
        pre: [{ name: "check", label: "Check" }],
        main: [{ name: "reboot" }, { name: "wait" }],
        post: [{ name: "note" }],
    };
    assert.deepEqual(earlierSteps(actions, "pre", 0), []);
    assert.deepEqual(earlierSteps(actions, "main", 1).map((row) => row.key), ["check", "reboot"]);
    assert.deepEqual(earlierSteps(actions, "post", 0).map((row) => row.key), ["check", "reboot", "wait"]);
    assert.deepEqual(earlierSteps(actions, "main", 0).map((row) => row.key), ["check"]);
});
