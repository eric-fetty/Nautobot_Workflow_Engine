import assert from "node:assert/strict";
import test from "node:test";

import {
    checkTemplate,
    completionAt,
    fixedText,
    fixedValue,
    inputRef,
    matchReferences,
    paramMode,
} from "../nautobot_workflow_engine/static/nautobot_workflow_engine/js/shared/template.js";

test("parameter modes are read from the stored value", () => {
    assert.deepEqual(paramMode("{{ inputs.device.id }}"), { mode: "input", input: "device", attr: "id" });
    assert.deepEqual(paramMode("{{inputs.reason}}"), { mode: "input", input: "reason", attr: "" });
    assert.equal(paramMode("{{ inputs.device.name | upper }}").mode, "expr");
    assert.equal(paramMode("Rebooted {{ inputs.device.name }}").mode, "expr");
    assert.equal(paramMode("{% if inputs.x %}a{% endif %}").mode, "expr");
    assert.equal(paramMode("active").mode, "fixed");
    assert.equal(paramMode(3).mode, "fixed");
    assert.equal(paramMode(true).mode, "fixed");
    assert.equal(paramMode({ a: 1 }).mode, "fixed");
});

test("an input reference writes the same text it is read from", () => {
    const text = inputRef("device", "id");
    assert.equal(text, "{{ inputs.device.id }}");
    assert.deepEqual(paramMode(text), { mode: "input", input: "device", attr: "id" });
    assert.equal(inputRef("reason", ""), "{{ inputs.reason }}");
});

test("fixed values keep their YAML type", () => {
    assert.equal(fixedValue("15", "integer"), 15);
    assert.equal(fixedValue("abc", "integer"), "abc");
    assert.equal(fixedValue("yes", "boolean"), true);
    assert.equal(fixedValue("0012", "string"), "0012");
    assert.equal(fixedValue("0012", ""), "0012");
    assert.equal(fixedValue("20", "", 15), 20, "a number stays a number");
    assert.deepEqual(fixedValue('{"a": 2}', "", { a: 1 }), { a: 2 });
    assert.equal(fixedText({ a: 1 }), '{"a":1}');
    assert.equal(fixedText(null), "");
});

test("template checks catch typos and unclosed tags", () => {
    const known = { inputs: ["device", "reason"], results: ["check_reachable"] };
    const typo = checkTemplate("{{ inputs.devise.name }}", known);
    assert.equal(typo.length, 1);
    assert.match(typo[0].message, /Did you mean “device”/);
    assert.deepEqual(typo[0].fix, { from: "inputs.devise", to: "inputs.device" });
    assert.match(checkTemplate("{{ inputs.device", known)[0].message, /never closed/);
    assert.match(checkTemplate("{% if inputs.reason %}x", known)[0].message, /endif/);
    assert.match(checkTemplate("{{ results.later_step }}", known)[0].message, /no earlier step/);
    assert.deepEqual(checkTemplate("{{ inputs.device.name }} {{ results.check_reachable.status }}", known), []);
    assert.deepEqual(checkTemplate("{% for d in inputs.device %}{{ d }}{% endfor %}", known), []);
    assert.deepEqual(checkTemplate("plain text", known), []);
});

test("autocomplete knows when the caret is inside a tag", () => {
    assert.deepEqual(completionAt("a {{ inp", 8), { tagStart: 2, start: 5, query: "inp" });
    assert.equal(completionAt("a {{ x }} b", 11), null);
    assert.equal(completionAt("plain", 5), null);
    const refs = [{ path: "inputs.device" }, { path: "inputs.device.name" }, { path: "results.reboot" }, { path: "user.username" }];
    assert.deepEqual(matchReferences(refs, "dev").map((item) => item.path), ["inputs.device", "inputs.device.name"]);
    assert.equal(matchReferences(refs, "").length, 4);
});
