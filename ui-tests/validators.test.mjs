import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
    fieldError,
    patternError,
    regexProblem,
    rowErrors,
    trySample,
} from "../nautobot_workflow_engine/static/nautobot_workflow_engine/js/shared/validate.js";

const presets = JSON.parse(
    readFileSync(new URL("../nautobot_workflow_engine/schema/validator_presets.json", import.meta.url), "utf-8"),
).presets;

test("every preset compiles in the browser and sorts its examples", () => {
    assert.ok(presets.length >= 7);
    presets.forEach((preset) => {
        const rule = { regex: preset.regex, message: preset.message, flags: preset.flags };
        assert.equal(regexProblem(rule), "", preset.id);
        preset.valid.forEach((value) => assert.equal(patternError([rule], value), "", `${preset.id} should accept ${value}`));
        preset.invalid.filter(Boolean).forEach((value) => assert.equal(patternError([rule], value), preset.message, `${preset.id} should reject ${value}`));
    });
});

test("a pattern the browser can't read is reported, a blank one is not", () => {
    assert.match(regexProblem({ regex: "^(unclosed$" }), /\S/);
    assert.equal(regexProblem({ regex: "" }), "");
    assert.equal(regexProblem({ regex: "^[a-z]+$", flags: ["IGNORECASE"] }), "");
});

test("trying a sample shows the launch-form message and each rule", () => {
    const rules = [
        { regex: "^[a-z]", message: "Start with a letter." },
        { regex: "[0-9]$", message: "End with a number." },
        { regex: "(" },
        { regex: "" },
    ];
    const result = trySample(rules, "sw1");
    assert.equal(result.message, "");
    assert.deepEqual(result.rules.map((rule) => rule.ok), [true, true, null, null]);
    const bad = trySample(rules, "1sw");
    assert.equal(bad.message, "Start with a letter.");
    assert.deepEqual(bad.rules.map((rule) => rule.ok).slice(0, 2), [false, false]);
});

test("flags apply in the browser like they do on the server", () => {
    const rule = { regex: "^chg[0-9]{7}$", message: "Change number", flags: ["IGNORECASE"] };
    assert.equal(patternError([rule], "CHG0012345"), "");
    assert.equal(patternError([{ ...rule, flags: [] }], "CHG0012345"), "Change number");
});

test("list lines and table columns use the same rules", () => {
    const hostname = presets.find((preset) => preset.id === "hostname");
    const spec = { type: "list", validators: [{ regex: hostname.regex, message: hostname.message }] };
    assert.equal(fieldError(spec, ["leaf1", "leaf2"]), "");
    assert.equal(fieldError(spec, ["leaf1", "bad host"]), `Line 2: ${hostname.message}`);
    const columns = [{ key: "mac", label: "MAC", type: "string", validators: [{ regex: presets.find((p) => p.id === "mac_address").regex, message: "MAC please" }] }];
    assert.deepEqual(rowErrors(columns, { mac: "001a.2b3c.4d5e" }), {});
    assert.deepEqual(rowErrors(columns, { mac: "nope" }), { mac: "MAC: MAC please" });
});
