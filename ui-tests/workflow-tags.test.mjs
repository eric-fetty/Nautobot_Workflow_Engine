import assert from "node:assert/strict";
import test from "node:test";

import { draftBody, tagOptions, tagTextColor, toggleTag } from "../nautobot_workflow_engine/static/nautobot_workflow_engine/js/builder/store.js";

const CHOICES = [
    { id: "1", name: "Production", color: "f44336" },
    { id: "2", name: "Lab", color: "ffeb3b" },
    { id: "3", name: "Networking", color: "9c27b0" },
];

test("the tag picker offers tags not chosen yet that match the search", () => {
    assert.deepEqual(tagOptions(CHOICES, ["Lab"], "").map((tag) => tag.name), ["Production", "Networking"]);
    assert.deepEqual(tagOptions(CHOICES, [], "NET").map((tag) => tag.name), ["Networking"]);
    assert.deepEqual(tagOptions(CHOICES, ["Production", "Lab", "Networking"], ""), []);
    assert.deepEqual(tagOptions(undefined, undefined, "x"), []);
});

test("choosing a tag adds it and choosing it again removes it", () => {
    const original = ["Lab"];
    const added = toggleTag(original, "Production");
    assert.deepEqual(added, ["Lab", "Production"]);
    assert.deepEqual(original, ["Lab"]);
    assert.deepEqual(toggleTag(added, "Lab"), ["Production"]);
    assert.deepEqual(toggleTag(undefined, "Lab"), ["Lab"]);
});

test("tag chips use readable text on the tag colour", () => {
    assert.equal(tagTextColor("ffeb3b"), "#000000");
    assert.equal(tagTextColor("#9c27b0"), "#ffffff");
    assert.equal(tagTextColor("nope"), "");
});

test("tag chips go into the draft save body", () => {
    const payload = { key: "k", name: "N", tags: ["Lab", "Production"] };
    const body = draftBody(payload, { base_revision: 4, base_git_commit: "abc" });
    assert.deepEqual(body.tags, ["Lab", "Production"]);
    assert.deepEqual(body.payload.tags, ["Lab", "Production"]);
    assert.equal(body.base_revision, 4);
    assert.equal(body.base_git_commit, "abc");
    assert.equal(body.base_content_hash, "");
    assert.equal(body.strict, false);
    assert.deepEqual(draftBody({ key: "k" }, {}).tags, []);
});
